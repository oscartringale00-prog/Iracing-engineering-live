import configparser
import os
import re
import json
import logging
import logging.handlers
import queue
import select
import threading
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


# Variabili per la strategia: NON sono canali ad alta frequenza.
# Usura e temperature gomme cambiano lentamente e al muretto servono per giro, non 60 volte
# al secondo: registrarle una volta a fine giro costa una manciata di numeri invece di
# raddoppiare il peso della telemetria.
STRATEGY_VARS = [
    ("FuelLevel", "fuel", 3),
    ("LFwearM", "wear_lf", 4), ("RFwearM", "wear_rf", 4),
    ("LRwearM", "wear_lr", 4), ("RRwearM", "wear_rr", 4),
    ("LFtempCM", "temp_lf", 1), ("RFtempCM", "temp_rf", 1),
    ("LRtempCM", "temp_lr", 1), ("RRtempCM", "temp_rr", 1),
]


# ====== CANALI AD ALTA FREQUENZA (360 Hz) ======
# iRacing pubblica queste variabili come pacchetti di 6 campioni per ogni fotogramma a 60 Hz.
# Le raccogliamo SOLO dove servono davvero: i movimenti delle sospensioni oscillano fra 3 e 15 Hz
# e a 60 Hz se ne colgono appena 4-5 campioni per oscillazione. Per pedali, sterzo e velocità
# 60 Hz sono abbondanti (e iRacing non offre comunque di meglio).
HF_CHANNELS = [
    # SOLO sospensioni: è qui che 60 Hz non bastano (oscillazioni fra 3 e 15 Hz).
    # Accelerazioni e rotazione restano a 60 Hz, dove sono già più che sufficienti:
    # includerle avrebbe fatto crescere il messaggio del 40% senza aggiungere informazione utile.
    ("LFshockVel_ST", "shockvel_lf_hf", 3),      # velocità ammortizzatore: serve all'istogramma
    ("RFshockVel_ST", "shockvel_rf_hf", 3),
    ("LRshockVel_ST", "shockvel_lr_hf", 3),
    ("RRshockVel_ST", "shockvel_rr_hf", 3),
    ("LFshockDefl_ST", "shock_lf_hf", 4),        # corsa: cordoli e movimento della piattaforma
    ("RFshockDefl_ST", "shock_rf_hf", 4),
    ("LRshockDefl_ST", "shock_lr_hf", 4),
    ("RRshockDefl_ST", "shock_rr_hf", 4),
    ("HFshockDefl_ST", "heave_f_hf", 4),
    ("HRshockDefl_ST", "heave_r_hf", 4),
]
HF_SUB = 6                  # sotto-campioni per fotogramma: 60 x 6 = 360 Hz
MAX_SAMPLES_HF = 30000      # tetto dedicato (~83 s a 360 Hz), oltre si dirada

# Canali telemetria ad alta frequenza: (nome SDK, chiave, decimali)
TELEMETRY_CHANNELS = [
    # (nome SDK, chiave, decimali). I decimali sono il minimo che non toglie informazione utile:
    # ridurli alleggerisce l'invio (la telemetria di un giro supera 1 MB) e lo spazio nel database.
    ("Speed", "speed", 1),                  # m/s: 0,1 m/s = 0,36 km/h
    ("Throttle", "throttle", 3),
    ("Brake", "brake", 3),
    ("Clutch", "clutch", 2),
    ("SteeringWheelAngle", "steer", 4),     # 4 decimali: a 3 i gradini si vedevano sugli ovali
    ("Gear", "gear", 0),
    ("RPM", "rpm", 0),
    ("LapDistPct", "lapdist", 5),           # invariato: serve ad allineare i giri nel confronto
    ("LatAccel", "lataccel", 2),
    (("LongAccel", "LonAccel"), "lonaccel", 2),
    (("YawRate", "YawRateST"), "yawrate", 4),      # per ricostruire la forma del tracciato
    ("VertAccel", "vertaccel", 2),
    # iRacing non espone le altezze di marcia: al loro posto le corse degli ammortizzatori
    # centrali (heave), che descrivono l'abbassamento della piattaforma aerodinamica.
    ("HFshockDefl", "heave_f", 4),
    ("HRshockDefl", "heave_r", 4),
    ("LFshockDefl", "shock_lf", 4),         # invariato: valori piccoli, la precisione conta
    ("RFshockDefl", "shock_rf", 4),
    ("LRshockDefl", "shock_lr", 4),
    ("RRshockDefl", "shock_rr", 4),
    # Coordinate GPS: iRacing NON le espone (verificato sull'elenco completo delle 354 variabili).
    # In compenso fornisce velocità e direzione, da cui la traiettoria si ricava per integrazione.
    ("VelocityX", "velx", 3),
    ("VelocityY", "vely", 3),
    ("YawNorth", "yaw", 4),
]


def load_config():
    cfg = configparser.ConfigParser()
    if CONFIG_PATH.exists():
        cfg.read(CONFIG_PATH)
    if not cfg.has_section("agent"):
        cfg.add_section("agent")
    modificato = False
    if not cfg["agent"].get("backend"):
        cfg["agent"]["backend"] = DEFAULT_BACKEND
        modificato = True
    # La voce viene scritta nel file così l'utente la vede e può cambiarla senza doverla indovinare
    if "alta_frequenza" not in cfg["agent"]:
        cfg["agent"]["alta_frequenza"] = "si"
        modificato = True
    if modificato:
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
        self.buf_lap_num = None      # numero di giro a cui appartiene il buffer
        self.fuel_start = None       # carburante a inizio giro
        self.pending_lap = None      # giro concluso, in attesa del suo tempo
        self._risolti = {}           # nome effettivo di ogni variabile, risolto una volta sola
        self._ctx = None             # contesto sessione, riletto al massimo una volta al secondo
        self._ctx_t = 0.0
        self.hf_on = HF_ATTIVA
        self.buf_hf = None
        self.last_live = 0.0         # ultimo invio della fotografia di gara
        self._last_strat = {}
        self.fuel_last = None        # ultimo valore letto
        self.diag_done = False       # diagnosi canali gia' stampata?
        self.prev_pct = None         # posizione sul giro al tick precedente

    def _reset_buffer_hf(self):
        self.buf_hf = {"lapdist_hf": []}
        for _, k, _ in HF_CHANNELS:
            self.buf_hf[k] = []

    def _reset_buffer(self):
        self.buf = {key: [] for _, key, _ in TELEMETRY_CHANNELS}
        if self.hf_on:
            self._reset_buffer_hf()
        self.lap_uid = str(uuid.uuid4())
        self.buf_valid = True

    def invalidate_lap(self):
        """Chiamata su riconnessione: il giro in corso è incompleto, va scartato."""
        self.buf_valid = False

    def _read_context(self):
        # Rileggerlo a ogni tick è inutile e costoso (comporta il riesame delle informazioni di
        # sessione): auto, pista e tipo di sessione cambiano al massimo una volta ogni tanto.
        ora = time.time()
        if self._ctx and ora - self._ctx_t < 1.0:
            return self._ctx
        self._ctx_t = ora
        self._ctx = self._read_context_vero()
        return self._ctx

    def _read_context_vero(self):
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
        """Non bisogna attendere qui: freeze_var_buffer_latest() si mette GIÀ in attesa del
        prossimo aggiornamento di iRacing. La pausa aggiuntiva faceva perdere sistematicamente
        un aggiornamento su due (sleep 16 ms + attesa del prossimo evento a 16 ms = 33 ms),
        dimezzando la frequenza da 60 a 30 campioni al secondo.
        La pausa resta solo se l'SDK non espone l'evento di sincronizzazione, altrimenti il
        ciclo girerebbe a vuoto consumando la CPU."""
        wfd = getattr(self.ir, "wait_for_data", None)
        if callable(wfd):
            try:
                wfd(0.05)
                return
            except Exception:
                pass
        if getattr(self.ir, "_data_valid_event", None) is None:
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
            self.buf_lap_num = None
            self.pending_lap = None
            self._risolti = {}
            self._ctx = None
            self.diag_done = False
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
        strat = {}
        for sdk_name, key, dec in STRATEGY_VARS:
            v = self._first_var(sdk_name)
            strat[key] = round(float(v), dec) if v is not None else None
        self._last_strat = strat
        if strat.get("fuel") is not None:
            if self.fuel_start is None:
                self.fuel_start = strat["fuel"]
            self.fuel_last = strat["fuel"]
        sample = {}
        for sdk_name, key, dec in TELEMETRY_CHANNELS:
            v = self._first_var(sdk_name)
            try:
                sample[key] = round(float(v), dec) if dec else int(v) if v is not None else None
            except Exception:
                sample[key] = None
        self.ir.unfreeze_var_buffer_latest()

        # Diagnosi una tantum: segnala quali canali iRacing NON sta fornendo.
        # Serve a capire subito, per esempio, se mancano le coordinate della mappa.
        if not self.diag_done:
            self.diag_done = True
            mancanti = [k for _, k, _ in TELEMETRY_CHANNELS if sample.get(k) is None]
            mancanti += [k for _, k, _ in STRATEGY_VARS if strat.get(k) is None]
            if mancanti:
                print("ATTENZIONE: iRacing non fornisce questi dati:", ", ".join(mancanti))
                if "lat" in mancanti or "lon" in mancanti:
                    print("  -> mancano le coordinate: la mappa del circuito non potra' essere disegnata.")
                if "fuel" in mancanti:
                    print("  -> manca il carburante: il calcolatore di strategia non potra' misurare i consumi.")
            else:
                print("Tutti i canali telemetrici sono disponibili (mappa inclusa).")
            self._dump_variabili()

        # Fotografia della gara a bassa frequenza (una volta al secondo): serve al Pitwall
        # e non deve mai disturbare la telemetria né il ciclo di lettura.
        now_l = time.time()
        if now_l - self.last_live >= LIVE_EVERY_S:
            self.last_live = now_l
            # La fotografia della gara serve al Pitwall, non alla registrazione dei giri:
            # se si rompe, il Pitwall resta indietro ma la telemetria continua ad arrivare.
            try:
                live = self._read_live()
                if live:
                    events.append(("live", live))
                self.live_ko = 0
            except Exception as e:
                self.live_ko = getattr(self, "live_ko", 0) + 1
                if self.live_ko in (1, 10) or self.live_ko % 300 == 0:
                    log(f"Dati per il Pitwall non disponibili ({type(e).__name__}): "
                        f"i giri continuano comunque a essere registrati.", e)

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
            self.buf_lap_num = lap
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

        # --- 1) CONFINE DEL GIRO: lo dà il contatore, che è immediato e affidabile ---
        # Il tempo sul giro invece iRacing lo pubblica con qualche istante di ritardo: se lo
        # pretendessimo subito, butteremmo via il giro appena percorso (era il bug).
        if lap is not None and lap != self.last_lap:
            if self.buf_lap_num is not None:
                self.pending_lap = {
                    "num": self.buf_lap_num, "uid": self.lap_uid,
                    "buf": self.buf if self.buf_valid else None,
                    "buf_hf": self.buf_hf if (self.buf_valid and self.hf_on) else None,
                    "fuel_start": self.fuel_start, "fuel_last": self.fuel_last,
                    "strat": dict(strat), "t": time.time(),
                }
            self.lap_invalid = False
            self.buf_valid = True
            self._reset_buffer()
            self.buf_lap_num = lap
            self.fuel_start = strat.get("fuel")
            self.last_lap = lap

        # --- 2) VALIDITÀ: appena arriva un tempo NUOVO, il giro in attesa viene spedito ---
        if self.pending_lap:
            nuovo = (last_time and last_time > 0 and
                     (self.last_sent_time is None
                      or abs(float(last_time) - self.last_sent_time) > 0.002))
            if nuovo:
                p = self.pending_lap
                self.pending_lap = None
                wx = self._read_weather(num)
                wx.pop("trackUsage", None)
                strategia = {k: v for k, v in p["strat"].items() if v is not None}
                if p["fuel_start"] is not None and p["fuel_last"] is not None:
                    strategia["fuel_start"] = p["fuel_start"]
                    strategia["fuel_used"] = round(max(0.0, p["fuel_start"] - p["fuel_last"]), 3)
                events.append(("lap", {"lap": p["num"], "lastLapTime": float(last_time),
                                       "lapUid": p["uid"], **wx, **strategia}))
                self.last_sent_time = float(last_time)
                if p["buf"] and len(p["buf"]["lapdist"]) > 10:
                    n_camp = len(p["buf"]["lapdist"])
                    p["hz"] = round(n_camp / float(last_time), 1) if last_time else None
                    campioni = _riduci(p["buf"])
                    n_hf = 0
                    if p.get("buf_hf") and len(p["buf_hf"]["lapdist_hf"]) > 10:
                        hf = _riduci(p["buf_hf"], MAX_SAMPLES_HF, "lapdist_hf")
                        n_hf = len(hf["lapdist_hf"])
                        campioni.update(hf)
                    events.append(("telemetry", {"lapUid": p["uid"], "samples": campioni,
                                                 "hz": p["hz"], "n_hf": n_hf,
                                                 "hz_hf": round(n_hf / float(last_time), 1) if (n_hf and last_time) else None}))
                if self.pending_stint:
                    self.stint = self.pending_stint
                    self.pending_stint = None
                    events.append(("stint", self.stint))
            elif time.time() - self.pending_lap["t"] > LAP_TIME_WAIT:
                # nessun tempo nuovo entro la finestra: non era un giro vero (rientro, reset)
                self.pending_lap = None

        # Il campione corrente appartiene al giro attualmente in corso: dopo un giro completato
        # finisce nel buffer NUOVO, così non lascia in coda un punto del giro successivo
        # (che sul grafico faceva tornare la linea indietro attraversando tutto il tracciato).
        if self.buf is not None:
            for _, k, _ in TELEMETRY_CHANNELS:
                self.buf[k].append(sample[k])
            if self.hf_on and self.buf_hf is not None:
                self._append_hf(lap_pct)
        self.prev_pct = lap_pct
        return events



    def _append_hf(self, lap_pct):
        """Aggiunge i 6 sotto-campioni di ogni variabile _ST, con il proprio asse delle distanze.
        LapDistPct esiste solo a 60 Hz: i 6 punti intermedi vengono distribuiti per interpolazione
        fra il valore precedente e quello attuale, gestendo il passaggio sul traguardo."""
        prev = self.prev_pct if self.prev_pct is not None else lap_pct
        cur = lap_pct
        if prev is None or cur is None:
            return
        salto = cur - prev
        if salto < -0.5:                     # passaggio sul traguardo: 0.99 -> 0.01
            salto += 1.0
        letti = {}
        for nome, key, dec in HF_CHANNELS:
            v = self._first_var(nome)
            if v is None:
                continue
            try:
                seq = list(v)
            except Exception:
                continue
            if len(seq) < HF_SUB:
                continue
            letti[key] = [round(float(x), dec) for x in seq[:HF_SUB]]
        if not letti:
            return
        for j in range(HF_SUB):
            d = (prev + salto * (j + 1) / HF_SUB) % 1.0
            self.buf_hf["lapdist_hf"].append(round(d, 6))
            for _, key, _ in HF_CHANNELS:
                if key in self.buf_hf:
                    self.buf_hf[key].append(letti.get(key, [None] * HF_SUB)[j] if key in letti else None)

    def _read_live(self):
        """Fotografia della gara: posizione di tutte le auto, classifica, stato box.
        iRacing fornisce questi dati per TUTTE le auto, ma carburante e gomme solo per la propria."""
        def arr(nome):
            v = self._safe_var(nome)
            return list(v) if v is not None else None
        lap = arr("CarIdxLap"); dist = arr("CarIdxLapDistPct")
        if lap is None or dist is None:
            return None
        pos = arr("CarIdxPosition") or []
        cpos = arr("CarIdxClassPosition") or []
        cls = arr("CarIdxClass") or []
        pit = arr("CarIdxOnPitRoad") or []
        surf = arr("CarIdxTrackSurface") or []
        last = arr("CarIdxLastLapTime") or []
        best = arr("CarIdxBestLapTime") or []
        f2 = arr("CarIdxF2Time") or []            # distacco gia' calcolato da iRacing
        done = arr("CarIdxLapCompleted") or []
        tyre = arr("CarIdxTireCompound") or []
        def g(a, i, conv=int, minimo=None):
            if i >= len(a) or a[i] is None:
                return None
            try:
                v = conv(a[i])
            except Exception:
                return None
            return None if (minimo is not None and v <= minimo) else v
        cars = []
        for i in range(len(dist)):
            if dist[i] is None or dist[i] < 0:
                continue                      # auto non in pista
            if i < len(surf) and surf[i] is not None and surf[i] < 0:
                continue
            cars.append({
                "i": i,
                "p": g(pos, i) or 0,
                "cp": g(cpos, i),                     # posizione nella propria classe
                "cl": g(cls, i),                      # classe (per il colore)
                "l": g(done, i) if g(done, i) is not None else (g(lap, i) or 0),
                "d": round(float(dist[i]), 4),
                "pit": 1 if (i < len(pit) and pit[i]) else 0,
                "lt": g(last, i, float, 0),
                "bt": g(best, i, float, 0),
                "gap": g(f2, i, float, -1),           # distacco dal precedente
                "ty": g(tyre, i),                     # mescola
            })
        ctx = self._read_context()
        me = self._safe_var("PlayerCarIdx")
        di = self._safe_var("DriverInfo") or {}
        piloti = [{"i": d.get("CarIdx"), "n": d.get("UserName"), "num": d.get("CarNumber"),
                   "auto": d.get("CarScreenNameShort")}
                  for d in (di.get("Drivers") or []) if d.get("CarIdx") is not None]
        return {
            "ts": time.time(),
            "me": int(me) if me is not None else None,
            "cars": cars,
            "piloti": piloti,
            "sessione": {
                "tipo": ctx[3],
                "pista": ctx[1],
                "auto": ctx[0],
                "tempoRimasto": self._safe_var("SessionTimeRemain"),
                "giriRimasti": self._safe_var("SessionLapsRemain"),
                "bandiera": self._safe_var("SessionFlags"),
                "airTemp": self._safe_var("AirTemp"),
                "trackTemp": self._safe_var("TrackTempCrew"),
                "pioggia": self._safe_var("Precipitation"),
                "bagnato": self._safe_var("TrackWetness"),
                "cielo": self._safe_var("Skies"),
                "umidita": self._safe_var("RelativeHumidity"),
                "vento": self._safe_var("WindVel"),
                "ventoDir": self._safe_var("WindDir"),
                "usura": self._safe_var("TrackUsage") if False else None,
            },
            "mia": {k: v for k, v in (self._last_strat or {}).items() if v is not None},
        }

    def _dump_variabili(self):
        """Scrive nel registro l'elenco di TUTTE le variabili che iRacing espone su questo PC.
        Serve a capire con certezza quali dati sono disponibili (per esempio le coordinate GPS),
        invece di tirare a indovinare sui nomi."""
        try:
            nomi = []
            for attr in ("_var_headers_dict", "var_headers_dict", "_var_headers"):
                v = getattr(self.ir, attr, None)
                if isinstance(v, dict):
                    nomi = sorted(v.keys()); break
                if isinstance(v, (list, tuple)):
                    nomi = sorted(getattr(h, "name", str(h)) for h in v); break
            if not nomi:
                log("Elenco variabili non disponibile in questa versione di pyirsdk.")
                return
            log(f"iRacing espone {len(nomi)} variabili. Elenco completo:")
            for i in range(0, len(nomi), 12):
                log("   " + ", ".join(nomi[i:i+12]))
            interessanti = [n for n in nomi if any(k in n.lower()
                            for k in ("lat", "lon", "yaw", "pos", "velocity", "ride", "gps"))]
            if interessanti:
                log("Variabili utili a posizione e assetto: " + ", ".join(interessanti))
        except Exception as e:
            log("Impossibile elencare le variabili", e)

    def _first_var(self, nomi):
        """Risolve il nome UNA VOLTA SOLA e poi lo riusa.
        Prima si ritentavano a ogni tick anche i nomi inesistenti: in Python sollevare e
        catturare eccezioni costa, e con una decina di variabili assenti il ciclo scendeva
        da 60 a 30 letture al secondo."""
        chiave = nomi if isinstance(nomi, str) else tuple(nomi)
        if chiave in self._risolti:
            nome = self._risolti[chiave]
            return self._safe_var(nome) if nome else None
        candidati = (nomi,) if isinstance(nomi, str) else nomi
        for n in candidati:
            v = self._safe_var(n)
            if v is not None:
                self._risolti[chiave] = n
                return v
        self._risolti[chiave] = None      # non esiste: non ci riprovo più
        return None

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
        self.anomalie = ["sosta", "teletrasporto", "fasulla"]   # riprodotte una per giro
        self.fuel_demo = 45.0       # serbatoio simulato
        self.wear_demo = 1.0        # 1.0 = gomma nuova
        self.last_live_demo = 0.0
        self.avvio_demo = time.time()

    def _fake_telemetry(self):
        """Giro sintetico realistico: pista a forma di fagiolo, campioni come un giro vero.
        Il numero di campioni rispecchia la frequenza reale (~60 al secondo per ~110 s), così i
        collaudi fanno emergere gli stessi problemi che prima si vedevano solo in pista."""
        import math
        n = DEMO_SAMPLES
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
            buf["speed"].append(round(speed, 2)); buf["throttle"].append(round(thr, 3))
            buf["brake"].append(round(brk, 3)); buf["clutch"].append(0.0)
            buf["steer"].append(round(steer, 4)); buf["gear"].append(gear)
            buf["rpm"].append(int(rpm)); buf["lapdist"].append(round(d, 5))
            buf["lataccel"].append(round(2.5 * math.sin(2 * a), 3))
            buf["lonaccel"].append(round(1.5 * math.sin(3 * a + 1), 3))
            buf["vertaccel"].append(round(9.81 + 0.8 * math.sin(6 * a), 3))
            # corse degli ammortizzatori centrali (le altezze di marcia iRacing non le espone)
            buf["heave_f"].append(round(0.021 + 0.005 * math.sin(3 * a), 4))
            buf["heave_r"].append(round(0.024 + 0.005 * math.sin(3 * a + 1), 4))
            buf["velx"].append(round(speed, 3))
            buf["vely"].append(0.0)
            buf["yaw"].append(round((a + math.pi / 2) % (2 * math.pi), 4))
            buf["yawrate"].append(round(math.sin(2 * a) * 0.15, 4))
            for _k, _ph in (("shock_lf",0),("shock_rf",0.3),("shock_lr",1),("shock_rr",1.3)):
                buf[_k].append(round(0.017 + 0.006 * math.sin(3 * a + _ph), 4))
            for k, ph in (("shock_lf", 0), ("shock_rf", 0.3), ("shock_lr", 1), ("shock_rr", 1.3)):
                buf[k].append(round(0.017 + 0.006 * math.sin(3 * a + ph), 4))
            for k, ph in (("shock_lf",0),("shock_rf",0.3),("shock_lr",1),("shock_rr",1.3)):
                buf[k].append(round(0.017 + 0.006 * math.sin(3 * a + ph), 4))
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
        now_l = time.time()
        if now_l - self.last_live_demo >= LIVE_EVERY_S:
            self.last_live_demo = now_l
            t = now_l - self.avvio_demo        # tempo trascorso, non tempo assoluto
            cars = []
            for i in range(12):
                vel = 1/108.0 * (1 + (i % 5) * 0.004)       # ritmi leggermente diversi
                d = ((t * vel) + i * 0.083) % 1.0
                giro = int((t * vel) + i * 0.083)
                cars.append({"i": i, "p": i + 1, "l": giro, "d": round(d, 4),
                             "pit": 1 if (i == 4 and int(t) % 60 < 8) else 0,
                             "lt": round(108 + (i % 5) * 0.4, 3),
                             "bt": round(107.2 + (i % 5) * 0.35, 3)})
            events.append(("live", {
                "ts": t, "me": 0, "cars": cars,
                "piloti": [{"i": i, "n": f"Pilota {i+1}", "num": str(10 + i), "auto": "MX-5"} for i in range(12)],
                "sessione": {"tipo": "Race", "pista": "Autodromo Nazionale Monza", "auto": "Mazda MX-5 Cup",
                             "tempoRimasto": 1800, "giriRimasti": 16,
                             "bandiera": 0, "airTemp": 24.5, "trackTemp": 37.0},
                "mia": {"fuel": round(self.fuel_demo, 1), "wear_lf": round(self.wear_demo, 3),
                        "wear_rf": round(self.wear_demo - 0.01, 3), "wear_lr": round(self.wear_demo - 0.02, 3),
                        "wear_rr": round(self.wear_demo - 0.03, 3), "temp_lf": 83.0, "temp_rf": 85.0,
                        "temp_lr": 80.0, "temp_rr": 82.0}}))

        if time.time() >= self.next_at:
            self.lap += 1
            lap_uid = str(uuid.uuid4())
            events.append(("lap", {"lap": self.lap, "lastLapTime": 107.0 + random.uniform(-2.0, 2.0),
                                   "lapUid": lap_uid,
                                   "airTemp": round(24 + random.uniform(-1, 1), 1),
                                   "trackTemp": round(36 + random.uniform(-2, 2), 1),
                                   "humidity": round(random.uniform(0.4, 0.6), 2),
                                   "windVel": round(random.uniform(1, 6), 1),
                                   "windDir": round(random.uniform(0, 6.28), 2),
                                   # dati di strategia simulati: consumo e usura progressiva
                                   "fuel": round(self.fuel_demo, 3),
                                   "fuel_start": round(self.fuel_demo + 2.8, 3),
                                   "fuel_used": round(2.75 + random.uniform(-0.15, 0.15), 3),
                                   "wear_lf": round(self.wear_demo, 4), "wear_rf": round(self.wear_demo - 0.01, 4),
                                   "wear_lr": round(self.wear_demo - 0.02, 4), "wear_rr": round(self.wear_demo - 0.03, 4),
                                   "temp_lf": round(82 + random.uniform(-4, 4), 1),
                                   "temp_rf": round(84 + random.uniform(-4, 4), 1),
                                   "temp_lr": round(79 + random.uniform(-4, 4), 1),
                                   "temp_rr": round(81 + random.uniform(-4, 4), 1)}))
            self.fuel_demo = max(0.0, self.fuel_demo - 2.75)
            self.wear_demo = max(0.0, self.wear_demo - 0.012)
            events.append(("telemetry", {"lapUid": lap_uid, "samples": self._fake_telemetry()}))
            self.next_at = time.time() + 6
            if self.lap % s["stints"][self.sti][1] == 0:
                self.sti += 1
                self.stint = None
                if self.sti >= len(s["stints"]):
                    self.si += 1
                    self.session = None
        return events


DEMO_SAMPLES = 6600        # campioni per giro in modalità demo: come un giro vero

# ====== REGISTRO SU FILE ======
# Serve all'assistenza: quando qualcosa va storto sul PC di un cliente, la finestra nera si chiude
# e non resta traccia. Qui teniamo data/ora, eventi ed errori, con un tetto di dimensione.
LOG_MAX_BYTES = 1_000_000     # 1 MB, poi si ricicla
LOG_BACKUPS = 2
_logger = None
_log_path = None


def _mask(txt):
    """Non lascia mai finire la chiave del dispositivo nel registro."""
    txt = str(txt)
    key = globals().get("_device_key_corrente")
    if key and len(key) > 6:
        txt = txt.replace(key, key[:4] + "…" + key[-2:])
    return re.sub(r"(device_key=)[^&\s\"']+", r"\1<nascosta>", txt)


def setup_log():
    """Prepara il file di registro. Se non è scrivibile, si prosegue senza: mai bloccare l'agente."""
    global _logger, _log_path
    try:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        cartella = Path(base) / "iRacingTelemetry"
        cartella.mkdir(parents=True, exist_ok=True)
        _log_path = cartella / "iracing-telemetry.log"
        h = logging.handlers.RotatingFileHandler(_log_path, maxBytes=LOG_MAX_BYTES,
                                                 backupCount=LOG_BACKUPS, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%Y-%m-%d %H:%M:%S"))
        lg = logging.getLogger("iracing")
        lg.setLevel(logging.INFO)
        lg.handlers.clear()
        lg.addHandler(h)
        _logger = lg
        return _log_path
    except Exception:
        _logger = None
        _log_path = None
        return None


def log(msg, errore=None):
    """Stampa a schermo (in italiano, per l'utente) e scrive nel registro (con il dettaglio tecnico)."""
    print(msg)
    if _logger is None:
        return
    try:
        if errore is not None:
            _logger.error(_mask(msg) + " | dettaglio: " + _mask(repr(errore)))
        else:
            _logger.info(_mask(msg))
    except Exception:
        pass


MAX_SAMPLES = 9000         # tetto per giro (~1,4 MB): oltre, si riducono in modo uniforme
LAP_TIME_WAIT = 5.0        # attesa massima perché iRacing pubblichi il tempo del giro
LIVE_EVERY_S = 1.0         # fotografia della gara: una volta al secondo
HF_ATTIVA = True           # alta frequenza (360 Hz) sui canali di sospensioni e dinamica
SEND_TIMEOUT = 45          # tempo concesso all'invio: la telemetria di un giro può superare 1 MB
QUEUE_SOFT = 8             # oltre questa attesa si smette di accodare telemetria (pesante)
QUEUE_HARD = 40            # tetto assoluto: oltre, si scarta tutto per non far crescere la memoria


def _log_traccia(e):
    """Scrive nel registro il punto esatto in cui l'errore si e' verificato: senza questo,
    un errore di programmazione e' quasi impossibile da individuare a distanza."""
    try:
        import traceback
        for riga in traceback.format_exception(type(e), e, e.__traceback__):
            for r in riga.rstrip().split("\n"):
                if r.strip():
                    log("    " + r.strip())
    except Exception:
        pass


def _riduci(buf, tetto=None, asse="lapdist"):
    """Se il giro ha troppi campioni li dirada in modo uniforme lungo tutto il giro.
    Conserva il primo e l'ultimo campione e mantiene allineati fra loro tutti i canali dello
    stesso asse, così la forma delle curve e la copertura del giro restano fedeli."""
    tetto = tetto or MAX_SAMPLES
    MAX_SAMPLES_LOC = tetto
    n = len(buf[asse])
    if n <= tetto:
        return buf
    passo = n / MAX_SAMPLES_LOC
    idx = [int(i * passo) for i in range(MAX_SAMPLES_LOC)]
    if idx[-1] != n - 1:
        idx[-1] = n - 1
    print(f"  giro molto lungo ({n} campioni): ridotti a {len(idx)} per non appesantire l'invio")
    return {k: [v[i] for i in idx] for k, v in buf.items()}


class Link:
    """Gestisce la connessione al server con DUE thread dedicati: uno invia, uno riceve.

    Perché così: spedire la telemetria di un giro (oltre 1 MB) richiede secondi su rete lenta.
    Se lo facesse il ciclo principale, l'agente smetterebbe di leggere iRacing proprio all'inizio
    del giro successivo. E se la lettura avvenisse dal ciclo principale toccando il timeout del
    socket (come faceva la vecchia funzione drain), interromperebbe a metà un invio in corso.
    Separando i due compiti, il ciclo principale non tocca mai il socket.
    """

    def __init__(self, ws):
        self.ws = ws
        self.q = queue.Queue()
        self.error = None
        self.scartati = 0
        self._closed = False
        self._ts = threading.Thread(target=self._send_loop, daemon=True)
        self._tr = threading.Thread(target=self._recv_loop, daemon=True)
        self._ts.start()
        self._tr.start()

    def send(self, obj):
        """Accoda un messaggio. Ritorna False se scartato per congestione."""
        if self.error or self._closed:
            return False
        n = self.q.qsize()
        # I giri sono leggeri e preziosi, la telemetria è pesante: si sacrifica prima la seconda.
        if n >= 3 and obj.get("type") == "live":
            return False          # una fotografia vecchia è inutile: meglio scartarla subito
        if n >= QUEUE_SOFT and obj.get("type") == "lap_telemetry":
            self.scartati += 1
            return False
        if n >= QUEUE_HARD:
            self.scartati += 1
            return False
        self.q.put(obj)
        return True

    def _send_loop(self):
        while True:
            obj = self.q.get()
            if obj is None:
                return
            try:
                self.ws.send(json.dumps(obj))
            except Exception as e:
                if not self._closed:
                    self.error = e
                return

    def _recv_loop(self):
        # Consuma i messaggi in arrivo e si accorge subito se la connessione cade.
        while not self._closed:
            try:
                self.ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception as e:
                if not self._closed:
                    self.error = e
                return

    def close(self):
        """Chiude la connessione e ATTENDE i thread, così non ne restano di orfani."""
        self._closed = True
        try:
            self.q.put_nowait(None)
        except Exception:
            pass
        try:
            self.ws.close()          # sblocca anche la lettura ferma in attesa
        except Exception:
            pass
        for t in (self._ts, self._tr):
            t.join(timeout=2)
        return self.threads_alive() == 0

    def threads_alive(self):
        return sum(1 for t in (self._ts, self._tr) if t.is_alive())


def run():
    p = setup_log()
    if p:
        print(f"Registro attività: {p}")
    else:
        print("Registro attività non disponibile (cartella non scrivibile): proseguo comunque.")
    cfg = load_config()
    backend = cfg["agent"]["backend"].rstrip("/")
    global HF_ATTIVA
    try:
        val = cfg["agent"].get("alta_frequenza", "si").strip().lower()
        HF_ATTIVA = val not in ("no", "0", "false", "off")
    except Exception:
        pass
    http_url = backend.replace("wss://", "https://").replace("ws://", "http://")
    print(f"iRacing Telemetry Agent {'(DEMO)' if DEMO else ''}")
    log("Alta frequenza (360 Hz su sospensioni e dinamica): "
        + ("ATTIVA" if HF_ATTIVA else "spenta")
        + " — si cambia con 'alta_frequenza = si/no' in config.ini")

    device_key = ensure_device_key(cfg, http_url)
    webbrowser.open(http_url)

    source = DemoSource() if DEMO else IracingSource()
    if not DEMO and irsdk is None:
        print("ERRORE: pyirsdk non installato (pip install pyirsdk)")
        return

    backoff = 1
    ws = None
    while True:
        link = None
        try:
            globals()["_device_key_corrente"] = device_key
            ws = websocket.create_connection(f"{backend}/ws/agent?device_key={device_key}", timeout=15)
            # Timeout ampio: serve all'INVIO, perché la telemetria di un giro può superare 1 MB
            # e su rete reale non entra tutta in una volta nel buffer di sistema.
            # La lettura resta non bloccante grazie a select (vedi più sotto).
            ws.settimeout(SEND_TIMEOUT)
            link = Link(ws)
            backoff = 1
            log("Connesso al server.")
            # Dopo una riconnessione: riallineo sessione/stint e scarto il giro in corso (incompleto)
            if getattr(source, "session", None):
                link.send({"type": "session_start", **source.session})
                if getattr(source, "stint", None):
                    link.send({"type": "stint_start", **source.stint})
            if hasattr(source, "invalidate_lap"):
                source.invalidate_lap()
            last_hb = time.time()
            while True:
                if link.error:
                    raise link.error
                for kind, data in source.poll():
                    if kind == "status":
                        print("iRacing:", "connesso" if data else "in attesa...")
                    elif kind == "session":
                        log(f"Sessione: {data['sessionType']} | {data['car']} @ {data['track']}")
                        link.send({"type": "session_start", **data})
                    elif kind == "stint":
                        log(f"Stint: setup '{data['setupName']}'")
                        link.send({"type": "stint_start", **data})
                    elif kind == "live":
                        link.send({"type": "live", **data})
                    elif kind == "telemetry":
                        n = len(data["samples"]["lapdist"])
                        if link.send({"type": "lap_telemetry", **data}):
                            hz = data.get("hz")
                            log(f"  telemetria giro in invio ({n} campioni"
                                + (f", {hz} al secondo)" if hz else ")"))
                        else:
                            log("  ATTENZIONE: connessione lenta, telemetria di questo giro non inviata")
                    else:
                        log(f"Giro {data['lap']}: {data['lastLapTime']:.3f}s")
                        link.send({"type": "lap", **data, "ts": time.time()})
                now = time.time()
                if now - last_hb >= HEARTBEAT_S:
                    link.send({"type": "hb"})
                    last_hb = now
        except KeyboardInterrupt:
            if link:
                link.close()
            return
        except Exception as e:
            # Chiudo sempre il collegamento vecchio e attendo i suoi thread: altrimenti a ogni
            # riconnessione resterebbero una connessione aperta a vuoto e thread orfani.
            if link:
                link.close()
                link = None
            log(f"Connessione persa, ritento tra {backoff}s...", e)
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)


if __name__ == "__main__":
    run()
