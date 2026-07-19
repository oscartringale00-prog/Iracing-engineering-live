import configparser
import json
import random
import secrets
import sys
import threading
import time
import webbrowser
from pathlib import Path

try:
    import irsdk
except ImportError:
    irsdk = None

import websocket

# URL del tuo backend: cambialo prima di buildare l'exe. L'utente finale non tocca nulla.
DEFAULT_BACKEND = "wss://TUO-BACKEND.example.com"

CONFIG_PATH = Path(sys.executable if getattr(sys, "frozen", False) else __file__).parent / "config.ini"
DEMO = "--demo" in sys.argv
HEARTBEAT_S = 5
POLL_S = 0.5


def load_config():
    cfg = configparser.ConfigParser()
    if CONFIG_PATH.exists():
        cfg.read(CONFIG_PATH)
    if not cfg.has_section("agent"):
        cfg.add_section("agent")
    changed = False
    if not cfg["agent"].get("backend"):
        cfg["agent"]["backend"] = DEFAULT_BACKEND
        changed = True
    if not cfg["agent"].get("token"):
        # Token generato una sola volta: identifica la "stanza" privata dell'utente
        cfg["agent"]["token"] = secrets.token_urlsafe(9)
        changed = True
    if changed:
        with open(CONFIG_PATH, "w") as f:
            cfg.write(f)
    return cfg["agent"]["backend"].rstrip("/"), cfg["agent"]["token"]


class IracingSource:
    def __init__(self):
        self.ir = irsdk.IRSDK() if irsdk else None
        self.connected = False
        self.last_lap = None

    def poll(self):
        """Ritorna ('status', bool) su cambio stato o ('lap', dict) su nuovo giro completato."""
        events = []
        if not self.ir:
            return events
        was = self.connected
        if not self.connected:
            self.connected = bool(self.ir.startup())
        elif not self.ir.is_connected:
            self.ir.shutdown()
            self.connected = False
            self.last_lap = None
        if was != self.connected:
            events.append(("status", self.connected))
        if self.connected:
            self.ir.freeze_var_buffer_latest()
            lap = self.ir["Lap"]
            last_time = self.ir["LapLastLapTime"]
            self.ir.unfreeze_var_buffer_latest()
            if lap is not None and lap != self.last_lap:
                if self.last_lap is not None and last_time and last_time > 0:
                    events.append(("lap", {"lap": self.last_lap, "lastLapTime": float(last_time)}))
                self.last_lap = lap
        return events


class DemoSource:
    def __init__(self):
        self.connected = True
        self.announced = False
        self.lap = 0
        self.next_at = time.time() + 3

    def poll(self):
        events = []
        if not self.announced:
            self.announced = True
            events.append(("status", True))
        if time.time() >= self.next_at:
            self.lap += 1
            events.append(("lap", {"lap": self.lap, "lastLapTime": 92.5 + random.uniform(-1.5, 1.5)}))
            self.next_at = time.time() + 12
        return events


def run():
    backend, token = load_config()
    http_url = backend.replace("wss://", "https://").replace("ws://", "http://")
    dashboard = f"{http_url}/?token={token}"
    print(f"iRacing Live Agent {'(DEMO)' if DEMO else ''}\nDashboard: {dashboard}")
    webbrowser.open(dashboard)

    source = DemoSource() if DEMO else IracingSource()
    if not DEMO and irsdk is None:
        print("ERRORE: pyirsdk non installato (pip install pyirsdk)")
        return

    backoff = 1
    while True:
        try:
            ws = websocket.create_connection(f"{backend}/ws/agent?token={token}", timeout=10)
            ws.settimeout(POLL_S)
            backoff = 1
            print("Connesso al server.")
            ws.send(json.dumps({"type": "status", "iracing": getattr(source, "connected", False)}))
            last_hb = time.time()
            while True:
                for kind, data in source.poll():
                    if kind == "status":
                        print("iRacing:", "connesso" if data else "in attesa...")
                        ws.send(json.dumps({"type": "status", "iracing": data}))
                    else:
                        print(f"Giro {data['lap']}: {data['lastLapTime']:.3f}s")
                        ws.send(json.dumps({"type": "lap", **data, "ts": time.time()}))
                if time.time() - last_hb >= HEARTBEAT_S:
                    ws.send(json.dumps({"type": "hb"}))
                    last_hb = time.time()
                try:
                    ws.recv()
                except websocket.WebSocketTimeoutException:
                    pass
        except KeyboardInterrupt:
            return
        except Exception as e:
            print(f"Server non raggiungibile ({e}), ritento tra {backoff}s...")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)


if __name__ == "__main__":
    run()
