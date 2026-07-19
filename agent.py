import configparser
import json
import random
import secrets
import sys
import time
import uuid
import webbrowser
from pathlib import Path

try:
    import irsdk
except ImportError:
    irsdk = None

import websocket

DEFAULT_BACKEND = "wss://web-production-8fbbf.up.railway.app"

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
        self.session_key = None
        self.session = None

    def _read_context(self):
        """Estrae auto, pista e tipo sessione dai blocchi YAML dell'SDK."""
        try:
            di = self.ir["DriverInfo"]
            car = di["Drivers"][di["DriverCarIdx"]]["CarScreenName"]
        except Exception:
            car = "Auto sconosciuta"
        try:
            track = self.ir["WeekendInfo"]["TrackDisplayName"]
        except Exception:
            track = "Pista sconosciuta"
        num = self.ir["SessionNum"] or 0
        try:
            stype = self.ir["SessionInfo"]["Sessions"][num]["SessionType"]
        except Exception:
            stype = "Session"
        return car, track, num, stype

    def _read_weather(self, num):
        def var(name):
            try:
                v = self.ir[name]
                return float(v) if v is not None else None
            except Exception:
                return None
        try:
            usage = self.ir["SessionInfo"]["Sessions"][num]["SessionTrackRubberState"]
        except Exception:
            usage = None
        return {
            "airTemp": var("AirTemp"),
            "trackTemp": var("TrackTempCrew") if var("TrackTempCrew") is not None else var("TrackTemp"),
            "humidity": var("RelativeHumidity"),
            "windVel": var("WindVel"),
            "windDir": var("WindDir"),
            "trackUsage": usage,
        }

    def poll(self):
        events = []
        was = self.connected
        if not self.connected:
            self.connected = bool(self.ir.startup())
        elif not self.ir.is_connected:
            self.ir.shutdown()
            self.connected = False
            self.last_lap = None
            self.session_key = None
            self.session = None
        if was != self.connected:
            events.append(("status", self.connected))
        if not self.connected:
            return events

        self.ir.freeze_var_buffer_latest()
        lap = self.ir["Lap"]
        last_time = self.ir["LapLastLapTime"]
        self.ir.unfreeze_var_buffer_latest()

        car, track, num, stype = self._read_context()
        key = (car, track, num)
        if key != self.session_key:
            # Nuova sessione: uid client-side per dedup lato server su riconnessioni
            self.session_key = key
            self.session = {"uid": str(uuid.uuid4()), "car": car, "track": track,
                            "sessionType": stype, "sessionNum": num, "ts": time.time(),
                            **self._read_weather(num)}
            self.last_lap = lap
            events.append(("session", self.session))

        if lap is not None and lap != self.last_lap:
            if self.last_lap is not None and last_time and last_time > 0:
                events.append(("lap", {"lap": self.last_lap, "lastLapTime": float(last_time)}))
            self.last_lap = lap
        return events


class DemoSource:
    """Simula due sessioni (Practice poi Race) su auto/piste diverse."""
    SCRIPT = [
        {"car": "Mazda MX-5 Cup", "track": "Autodromo Nazionale Monza", "sessionType": "Practice", "laps": 3},
        {"car": "Mazda MX-5 Cup", "track": "Autodromo Nazionale Monza", "sessionType": "Race", "laps": 3},
    ]

    def __init__(self):
        self.connected = True
        self.announced = False
        self.si = 0
        self.lap = 0
        self.next_at = time.time() + 3
        self.session = None

    def poll(self):
        events = []
        if not self.announced:
            self.announced = True
            events.append(("status", True))
        if self.si >= len(self.SCRIPT):
            return events
        if self.session is None:
            s = self.SCRIPT[self.si]
            self.session = {"uid": str(uuid.uuid4()), "car": s["car"], "track": s["track"],
                            "sessionType": s["sessionType"], "sessionNum": self.si, "ts": time.time(),
                            "airTemp": round(random.uniform(18, 30), 1), "trackTemp": round(random.uniform(28, 45), 1),
                            "humidity": round(random.uniform(0.3, 0.8), 2), "windVel": round(random.uniform(0, 8), 1),
                            "windDir": round(random.uniform(0, 6.28), 2), "trackUsage": random.choice(["low usage", "moderately low usage", "high usage"])}
            self.lap = 0
            events.append(("session", self.session))
        if time.time() >= self.next_at:
            self.lap += 1
            events.append(("lap", {"lap": self.lap, "lastLapTime": 107.0 + random.uniform(-2.0, 2.0)}))
            self.next_at = time.time() + 8
            if self.lap >= self.SCRIPT[self.si]["laps"]:
                self.si += 1
                self.session = None
        return events


def run():
    backend, token = load_config()
    http_url = backend.replace("wss://", "https://").replace("ws://", "http://")
    dashboard = f"{http_url}/?token={token}"
    print(f"iRacing Telemetry Agent {'(DEMO)' if DEMO else ''}\nDashboard: {dashboard}")
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
            # Dopo una riconnessione il server deve riagganciare la sessione corrente
            if getattr(source, "session", None):
                ws.send(json.dumps({"type": "session_start", **source.session}))
            last_hb = time.time()
            while True:
                for kind, data in source.poll():
                    if kind == "status":
                        print("iRacing:", "connesso" if data else "in attesa...")
                    elif kind == "session":
                        print(f"Sessione: {data['sessionType']} | {data['car']} @ {data['track']}")
                        ws.send(json.dumps({"type": "session_start", **data}))
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
