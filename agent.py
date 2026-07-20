import configparser
import json
import random
import secrets
import sys
import time
import urllib.request
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
LINK_POLL_S = 3
LINK_TIMEOUT_S = 600


def load_config():
    cfg = configparser.ConfigParser()
    if CONFIG_PATH.exists():
        cfg.read(CONFIG_PATH)
    if not cfg.has_section("agent"):
        cfg.add_section("agent")
    if not cfg["agent"].get("backend"):
        cfg["agent"]["backend"] = DEFAULT_BACKEND
        save_config(cfg)
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        cfg.write(f)


def http_json(url, method="GET", body=None):
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def ensure_device_key(cfg, http_url):
    """Primo avvio: collega questo PC all'account dell'utente (una volta sola)."""
    if cfg["agent"].get("device_key"):
        return cfg["agent"]["device_key"]
    print("Primo avvio: collegamento all'account...")
    code = http_json(f"{http_url}/api/device/start", method="POST", body={})["code"]
    link_url = f"{http_url}/link?code={code}"
    print(f"Si sta aprendo il browser. Accedi e conferma il collegamento.\n{link_url}")
    webbrowser.open(link_url)
    deadline = time.time() + LINK_TIMEOUT_S
    while time.time() < deadline:
        try:
            r = http_json(f"{http_url}/api/device/claim?code={code}")
            if r.get("status") == "linked":
                cfg["agent"]["device_key"] = r["device_key"]
                save_config(cfg)
                print("Dispositivo collegato al tuo account. Non servirà rifarlo.")
                return r["device_key"]
        except urllib.error.HTTPError as e:
            if e.code == 410:
                print("Codice scaduto: riavvia il programma e conferma entro 10 minuti.")
                sys.exit(1)
        except Exception:
            pass
        time.sleep(LINK_POLL_S)
    print("Tempo scaduto: riavvia il programma e conferma il collegamento nel browser.")
    sys.exit(1)


class IracingSource:
    def __init__(self):
        self.ir = irsdk.IRSDK() if irsdk else None
        self.connected = False
        self.last_lap = None
        self.session_key = None
        self.session = None
        self.stint = None
        self.on_pit = None

    def _read_context(self):
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

    def _read_setup(self):
        try:
            name = self.ir["DriverInfo"]["DriverSetupName"]
        except Exception:
            name = None
        try:
            setup = self.ir["CarSetup"] or {}
            setup = {k: v for k, v in setup.items() if k != "UpdateCount"}
        except Exception:
            setup = {}
        return {"uid": str(uuid.uuid4()), "setupName": name or "Setup sconosciuto", "setup": setup}

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
            self.stint = None
            self.on_pit = None
        if was != self.connected:
            events.append(("status", self.connected))
        if not self.connected:
            return events

        self.ir.freeze_var_buffer_latest()
        lap = self.ir["Lap"]
        last_time = self.ir["LapLastLapTime"]
        on_pit = bool(self.ir["OnPitRoad"])
        self.ir.unfreeze_var_buffer_latest()

        car, track, num, stype = self._read_context()
        key = (car, track, num)
        if key != self.session_key:
            self.session_key = key
            self.session = {"uid": str(uuid.uuid4()), "car": car, "track": track,
                            "sessionType": stype, "sessionNum": num, "ts": time.time(),
                            **self._read_weather(num)}
            self.last_lap = lap
            events.append(("session", self.session))
            self.stint = self._read_setup()
            self.on_pit = on_pit
            events.append(("stint", self.stint))
        elif self.on_pit and not on_pit:
            # Uscita dai box: nuovo stint, il setup può essere cambiato
            self.stint = self._read_setup()
            events.append(("stint", self.stint))
        self.on_pit = on_pit

        if lap is not None and lap != self.last_lap:
            if self.last_lap is not None and last_time and last_time > 0:
                wx = self._read_weather(num)
                wx.pop("trackUsage", None)
                events.append(("lap", {"lap": self.last_lap, "lastLapTime": float(last_time), **wx}))
            self.last_lap = lap
        return events


class DemoSource:
    SCRIPT = [
        {"car": "Mazda MX-5 Cup", "track": "Autodromo Nazionale Monza", "sessionType": "Practice",
         "stints": [("Baseline Monza", 2), ("Low Downforce", 2)]},
        {"car": "Mazda MX-5 Cup", "track": "Autodromo Nazionale Monza", "sessionType": "Race",
         "stints": [("Race Setup", 2)]},
    ]
    FAKE_SETUP = lambda self, n: {
        "TiresAero": {"LeftFront": {"ColdPressure": "165 kPa", "LastTempsIMO": "78C, 82C, 85C"},
                      "RightFront": {"ColdPressure": "165 kPa"}},
        "Chassis": {"Front": {"ArbSetting": n, "ToeIn": "-1.0 mm"},
                    "Rear": {"FuelLevel": "35.0 L", "ToeIn": "+2.0 mm"}},
    }

    def __init__(self):
        self.connected = True
        self.announced = False
        self.si = 0
        self.sti = 0
        self.lap = 0
        self.next_at = time.time() + 3
        self.session = None
        self.stint = None

    def poll(self):
        events = []
        if not self.announced:
            self.announced = True
            events.append(("status", True))
        if self.si >= len(self.SCRIPT):
            return events
        s = self.SCRIPT[self.si]
        if self.session is None:
            self.session = {"uid": str(uuid.uuid4()), "car": s["car"], "track": s["track"],
                            "sessionType": s["sessionType"], "sessionNum": self.si, "ts": time.time(),
                            "airTemp": round(random.uniform(18, 30), 1), "trackTemp": round(random.uniform(28, 45), 1),
                            "humidity": round(random.uniform(0.3, 0.8), 2), "windVel": round(random.uniform(0, 8), 1),
                            "windDir": round(random.uniform(0, 6.28), 2), "trackUsage": random.choice(["low usage", "moderately low usage", "high usage"])}
            self.sti = 0
            self.lap = 0
            self.stint = None
            events.append(("session", self.session))
        if self.stint is None:
            name = s["stints"][self.sti][0]
            self.stint = {"uid": str(uuid.uuid4()), "setupName": name, "setup": self.FAKE_SETUP(self.sti + 1)}
            events.append(("stint", self.stint))
        if time.time() >= self.next_at:
            self.lap += 1
            events.append(("lap", {"lap": self.lap, "lastLapTime": 107.0 + random.uniform(-2.0, 2.0),
                                   "airTemp": round(24 + random.uniform(-1, 1), 1),
                                   "trackTemp": round(36 + random.uniform(-2, 2), 1),
                                   "humidity": round(random.uniform(0.4, 0.6), 2),
                                   "windVel": round(random.uniform(1, 6), 1),
                                   "windDir": round(random.uniform(0, 6.28), 2)}))
            self.next_at = time.time() + 6
            if self.lap % s["stints"][self.sti][1] == 0:
                self.sti += 1
                self.stint = None
                if self.sti >= len(s["stints"]):
                    self.si += 1
                    self.session = None
        return events


def run():
    cfg = load_config()
    backend = cfg["agent"]["backend"].rstrip("/")
    http_url = backend.replace("wss://", "https://").replace("ws://", "http://")
    print(f"iRacing Telemetry Agent {'(DEMO)' if DEMO else ''}")

    device_key = ensure_device_key(cfg, http_url)
    webbrowser.open(http_url)

    source = DemoSource() if DEMO else IracingSource()
    if not DEMO and irsdk is None:
        print("ERRORE: pyirsdk non installato (pip install pyirsdk)")
        return

    backoff = 1
    while True:
        try:
            ws = websocket.create_connection(f"{backend}/ws/agent?device_key={device_key}", timeout=10)
            ws.settimeout(POLL_S)
            backoff = 1
            print("Connesso al server.")
            if getattr(source, "session", None):
                ws.send(json.dumps({"type": "session_start", **source.session}))
                if getattr(source, "stint", None):
                    ws.send(json.dumps({"type": "stint_start", **source.stint}))
            last_hb = time.time()
            while True:
                for kind, data in source.poll():
                    if kind == "status":
                        print("iRacing:", "connesso" if data else "in attesa...")
                    elif kind == "session":
                        print(f"Sessione: {data['sessionType']} | {data['car']} @ {data['track']}")
                        ws.send(json.dumps({"type": "session_start", **data}))
                    elif kind == "stint":
                        print(f"Stint: setup '{data['setupName']}'")
                        ws.send(json.dumps({"type": "stint_start", **data}))
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
