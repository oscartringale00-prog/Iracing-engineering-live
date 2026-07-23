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
LINK_POLL_S = 3
LINK_TIMEOUT_S = 600
TICK_S = 1 / 60  # frequenza nativa iRacing

# Canali telemetria ad alta frequenza: (nome SDK, chiave, decimali)
TELEMETRY_CHANNELS = [
    ("Speed", "speed", 2),
    ("Throttle", "throttle", 3),
    ("Brake", "brake", 3),
    ("Clutch", "clutch", 3),
    ("SteeringWheelAngle", "steer", 4),
    ("Gear", "gear", 0),
    ("RPM", "rpm", 0),
    ("LapDistPct", "lapdist", 5),
    ("LatAccel", "lataccel", 3),
    ("LonAccel", "lonaccel", 3),
    ("VertAccel", "vertaccel", 3),
    ("LFrideHeight", "rh_lf", 2),
    ("RFrideHeight", "rh_rf", 2),
    ("LRrideHeight", "rh_lr", 2),
    ("RRrideHeight", "rh_rr", 2),
    ("LFshockDefl", "shock_lf", 4),
    ("RFshockDefl", "shock_rf", 4),
    ("LRshockDefl", "shock_lr", 4),
    ("RRshockDefl", "shock_rr", 4),
    ("Lat", "lat", 7),
    ("Lon", "lon", 7),
]


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
        self.lap_uid = None
        self.buf = None
        self.buf_valid = True
        self.last_sent_time = None   # tempo dell'ultimo giro realmente inviato
        self.pending_stint = None    # stint letto all'uscita dai box, in attesa del prossimo giro
        self.lap_invalid = False     # giro in corso troncato (teletrasporto/reset)
        self.prev_pct = None         # posizione sul giro al tick precedente

    def _reset_buffer(self):
        self.buf = {key: [] for _, key, _ in TELEMETRY_CHANNELS}
        self.lap_uid = str(uuid.uuid4())
        self.buf_valid = True

    def invalidate_lap(self):
        """Chiamata su riconnessione: il giro in corso è incompleto, va scartato."""
        self.buf_valid = False

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

    def _wait_tick(self):
        # Sincronizzazione al tick nativo dell'SDK dove disponibile, altrimenti timer 60Hz
        wfd = getattr(self.ir, "wait_for_data", None)
        if callable(wfd):
            try:
                wfd(0.05)
                return
            except Exception:
                pass
        time.sleep(TICK_S)

    def poll(self):
        events = []
        was = self.connected
        if not self.connected:
            self.connected = bool(self.ir.startup())
            if not self.connected:
                time.sleep(0.5)
        elif not self.ir.is_connected:
            self.ir.shutdown()
            self.connected = False
            self.last_lap = None
            self.session_key = None
            self.session = None
            self.stint = None
            self.on_pit = None
            self.buf = None
            self.last_sent_time = None
            self.pending_stint = None
            self.lap_invalid = False
            self.prev_pct = None
        if was != self.connected:
            events.append(("status", self.connected))
        if not self.connected:
            return events

        self._wait_tick()
        self.ir.freeze_var_buffer_latest()
        lap = self.ir["Lap"]
        last_time = self.ir["LapLastLapTime"]
        on_pit = bool(self.ir["OnPitRoad"])
        lap_pct = self._safe_var("LapDistPct")
        sample = {}
        for sdk_name, key, dec in TELEMETRY_CHANNELS:
            try:
                v = self.ir[sdk_name]
                sample[key] = round(float(v), dec) if dec else int(v) if v is not None else None
            except Exception:
                sample[key] = None
        self.ir.unfreeze_var_buffer_latest()

        car, track, num, stype = self._read_context()
        key = (car, track, num)
        if key != self.session_key:
            self.session_key = key
            self.session = {"uid": str(uuid.uuid4()), "car": car, "track": track,
                            "sessionType": stype, "sessionNum": num, "ts": time.time(),
                            **self._read_weather(num)}
            self.last_lap = lap
            self.last_sent_time = None
            self.pending_stint = None
            events.append(("session", self.session))
            self.stint = self._read_setup()
            self.on_pit = on_pit
            events.append(("stint", self.stint))
            self._reset_buffer()
        elif self.on_pit and not on_pit:
            # Uscita dai box: il setup va letto ORA (è aggiornato), ma il nuovo stint
            # verrà applicato solo al primo giro completato dopo l'uscita, così il giro
            # durante il quale si è sostato resta nello stint in cui era iniziato.
            self.pending_stint = self._read_setup()
        self.on_pit = on_pit

        # --- Rilevamento rientro NON guidato ai box (tasto rapido / reset / tow) ---
        # L'auto viene teletrasportata: il giro in corso è troncato e non è un giro valido.
        if self._detect_teleport(lap_pct, on_pit):
            self.buf_valid = False
            self.lap_invalid = True

        if self.buf is not None:
            for _, k, _ in TELEMETRY_CHANNELS:
                self.buf[k].append(sample[k])
        self.prev_pct = lap_pct

        if lap is not None and lap != self.last_lap:
            valid = (self.last_lap is not None and last_time and last_time > 0
                     and not self.lap_invalid
                     # un giro reale ha SEMPRE un tempo nuovo: se è identico all'ultimo
                     # inviato, iRacing non l'ha aggiornato -> giro fantasma, da scartare
                     and (self.last_sent_time is None
                          or abs(float(last_time) - self.last_sent_time) > 0.002))
            if valid:
                wx = self._read_weather(num)
                wx.pop("trackUsage", None)
                events.append(("lap", {"lap": self.last_lap, "lastLapTime": float(last_time),
                                       "lapUid": self.lap_uid, **wx}))
                self.last_sent_time = float(last_time)
                if self.buf_valid and self.buf and len(self.buf["lapdist"]) > 10:
                    events.append(("telemetry", {"lapUid": self.lap_uid, "samples": self.buf}))
            # Il nuovo stint entra in vigore ORA: i giri successivi apparterranno ad esso
            if self.pending_stint:
                self.stint = self.pending_stint
                self.pending_stint = None
                events.append(("stint", self.stint))
            self.last_lap = lap
            self.lap_invalid = False
            self._reset_buffer()
        return events

    def _safe_var(self, name):
        try:
            return self.ir[name]
        except Exception:
            return None

    def _detect_teleport(self, lap_pct, on_pit):
        """Vero se l'auto è rientrata ai box senza percorrere la pista (tasto rapido, reset, tow).
        Difensivo: se una variabile non esiste in questa versione dell'SDK, viene ignorata."""
        # 1) indicatore diretto, se disponibile: l'auto non è in pista
        for name in ("IsOnTrack", "IsOnTrackCar"):
            v = self._safe_var(name)
            if v is not None and not bool(v):
                return True
        # 2) salto improvviso della posizione sul giro MENTRE si finisce ai box.
        #    Il vincolo "on_pit" evita falsi positivi: un rallentamento o un tick perso
        #    in pista può far variare la posizione, ma non porta ai box.
        if on_pit and lap_pct is not None and self.prev_pct is not None:
            d = abs(float(lap_pct) - float(self.prev_pct))
            if 0.05 < d < 0.95:      # esclude il normale passaggio 0.99 -> 0.01 sul traguardo
                return True
        return False


class DemoSource:
    SCRIPT = [
        {"car": "Mazda MX-5 Cup", "track": "Autodromo Nazionale Monza", "sessionType": "Practice",
         "stints": [("Baseline Monza", 2), ("Low Downforce", 2)]},
        {"car": "Mazda MX-5 Cup", "track": "Autodromo Nazionale Monza", "sessionType": "Race",
         "stints": [("Race Setup", 2)]},
    ]
    FAKE_SETUP = lambda self, n: {
        "TiresAero": {"LeftFront": {"ColdPressure": "165 kPa"}, "RightFront": {"ColdPressure": "165 kPa"}},
        "Chassis": {"Front": {"ArbSetting": n, "ToeIn": "-1.0 mm"}, "Rear": {"FuelLevel": "35.0 L"}},
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

    def _fake_telemetry(self):
        """Giro sintetico: pista a forma di fagiolo, ~300 campioni."""
        import math
        n = 300
        buf = {key: [] for _, key, _ in TELEMETRY_CHANNELS}
        for i in range(n):
            d = i / n
            a = d * 2 * math.pi
            speed = 45 + 25 * math.sin(3 * a + 1) * math.sin(a)      # m/s con variazioni
            thr = max(0.0, min(1.0, 0.6 + 0.5 * math.sin(3 * a + 1)))
            brk = max(0.0, min(1.0, -0.6 * math.sin(3 * a + 1)))
            steer = 0.5 * math.sin(2 * a) + 0.2 * math.sin(5 * a)
            gear = max(2, min(6, int(3 + 2 * math.sin(3 * a + 1))))
            rpm = 4500 + 2500 * thr
            lat = 45.62 + 0.006 * math.sin(a) + 0.002 * math.sin(2 * a)
            lon = 9.28 + 0.009 * math.cos(a)
            buf["speed"].append(round(speed, 2)); buf["throttle"].append(round(thr, 3))
            buf["brake"].append(round(brk, 3)); buf["clutch"].append(0.0)
            buf["steer"].append(round(steer, 4)); buf["gear"].append(gear)
            buf["rpm"].append(int(rpm)); buf["lapdist"].append(round(d, 5))
            buf["lataccel"].append(round(2.5 * math.sin(2 * a), 3))
            buf["lonaccel"].append(round(1.5 * math.sin(3 * a + 1), 3))
            buf["vertaccel"].append(round(9.81 + 0.8 * math.sin(6 * a), 3))
            for k, base, amp, ph in (("rh_lf",30,4,0),("rh_rf",30,4,0.3),("rh_lr",47,5,1),("rh_rr",47,5,1.3)):
                buf[k].append(round(base + amp * math.sin(3 * a + ph), 2))
            for k, ph in (("shock_lf",0),("shock_rf",0.3),("shock_lr",1),("shock_rr",1.3)):
                buf[k].append(round(0.017 + 0.006 * math.sin(3 * a + ph), 4))
            buf["lat"].append(round(lat, 7)); buf["lon"].append(round(lon, 7))
        return buf

    def poll(self):
        events = []
        time.sleep(0.05)
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
                            "windDir": round(random.uniform(0, 6.28), 2), "trackUsage": "low usage"}
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
            lap_uid = str(uuid.uuid4())
            events.append(("lap", {"lap": self.lap, "lastLapTime": 107.0 + random.uniform(-2.0, 2.0),
                                   "lapUid": lap_uid,
                                   "airTemp": round(24 + random.uniform(-1, 1), 1),
                                   "trackTemp": round(36 + random.uniform(-2, 2), 1),
                                   "humidity": round(random.uniform(0.4, 0.6), 2),
                                   "windVel": round(random.uniform(1, 6), 1),
                                   "windDir": round(random.uniform(0, 6.28), 2)}))
            events.append(("telemetry", {"lapUid": lap_uid, "samples": self._fake_telemetry()}))
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
            ws.settimeout(0.005)
            backoff = 1
            print("Connesso al server.")
            # Dopo una riconnessione: riallineo sessione/stint e scarto il giro in corso (incompleto)
            if getattr(source, "session", None):
                ws.send(json.dumps({"type": "session_start", **source.session}))
                if getattr(source, "stint", None):
                    ws.send(json.dumps({"type": "stint_start", **source.stint}))
            if hasattr(source, "invalidate_lap"):
                source.invalidate_lap()
            last_hb = time.time()
            last_recv = time.time()
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
                    elif kind == "telemetry":
                        n = len(data["samples"]["lapdist"])
                        print(f"  telemetria giro inviata ({n} campioni)")
                        ws.send(json.dumps({"type": "lap_telemetry", **data}))
                    else:
                        print(f"Giro {data['lap']}: {data['lastLapTime']:.3f}s")
                        ws.send(json.dumps({"type": "lap", **data, "ts": time.time()}))
                now = time.time()
                if now - last_hb >= HEARTBEAT_S:
                    ws.send(json.dumps({"type": "hb"}))
                    last_hb = now
                if now - last_recv >= 1.0:
                    last_recv = now
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
