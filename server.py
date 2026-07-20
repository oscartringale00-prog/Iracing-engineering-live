import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

BASE = Path(__file__).parent
SCHEMA = (BASE / "schema.sql").read_text()
DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/iracing")
PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "")
AUTH_DEBUG = os.environ.get("AUTH_DEBUG") == "1"  # solo per test locali, MAI in produzione
LINK_CODE_TTL = 600

_jwk_client = jwt.PyJWKClient(
    "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"
)

pool: asyncpg.Pool = None


def verify_id_token(token: str) -> str:
    """Verifica l'ID token Firebase e ritorna l'uid. Solleva se non valido."""
    if AUTH_DEBUG and token.startswith("debug-"):
        return token[6:]
    key = _jwk_client.get_signing_key_from_jwt(token).key
    data = jwt.decode(
        token, key, algorithms=["RS256"], audience=PROJECT_ID,
        issuer=f"https://securetoken.google.com/{PROJECT_ID}",
    )
    return data["sub"]


def uid_dep(authorization: str = Header(default="")) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Login richiesto")
    try:
        return verify_id_token(authorization[7:])
    except Exception:
        raise HTTPException(401, "Token non valido o scaduto")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=5)
    async with pool.acquire() as c:
        await c.execute(SCHEMA)
    yield
    await pool.close()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
@app.get("/link")
async def index():
    return FileResponse(BASE / "index.html")


# ---------- Collegamento dispositivo (exe) ----------

@app.post("/api/device/start")
async def device_start():
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
    await pool.execute("INSERT INTO link_codes (code) VALUES ($1)", code)
    return {"code": code}


class LinkBody(BaseModel):
    code: str
    name: str | None = None


@app.post("/api/device/link")
async def device_link(body: LinkBody, uid: str = Depends(uid_dep)):
    row = await pool.fetchrow(
        "SELECT code, device_key, created_at FROM link_codes WHERE code = $1", body.code.upper())
    if not row or row["device_key"] is not None:
        raise HTTPException(404, "Codice non valido o già usato")
    if (time.time() - row["created_at"].timestamp()) > LINK_CODE_TTL:
        raise HTTPException(410, "Codice scaduto: riavvia il programma sul PC")
    device_key = secrets.token_urlsafe(32)
    await pool.execute(
        "INSERT INTO devices (user_id, device_key, name) VALUES ($1,$2,$3)",
        uid, device_key, body.name or "PC di gara")
    await pool.execute("UPDATE link_codes SET device_key = $1 WHERE code = $2", device_key, body.code.upper())
    return {"ok": True}


@app.get("/api/device/claim")
async def device_claim(code: str):
    row = await pool.fetchrow("SELECT device_key, created_at FROM link_codes WHERE code = $1", code.upper())
    if not row:
        raise HTTPException(404, "Codice sconosciuto")
    if row["device_key"]:
        await pool.execute("DELETE FROM link_codes WHERE code = $1", code.upper())
        return {"status": "linked", "device_key": row["device_key"]}
    if (time.time() - row["created_at"].timestamp()) > LINK_CODE_TTL:
        await pool.execute("DELETE FROM link_codes WHERE code = $1", code.upper())
        raise HTTPException(410, "Codice scaduto")
    return {"status": "pending"}


@app.get("/api/devices")
async def list_devices(uid: str = Depends(uid_dep)):
    rows = await pool.fetch(
        "SELECT id, name, created_at, last_seen FROM devices WHERE user_id = $1 ORDER BY created_at", uid)
    return [dict(r) for r in rows]


@app.delete("/api/devices/{device_id}")
async def delete_device(device_id: int, uid: str = Depends(uid_dep)):
    await pool.execute("DELETE FROM devices WHERE id = $1 AND user_id = $2", device_id, uid)
    return {"ok": True}


# ---------- Ingestione dall'agente ----------

@app.websocket("/ws/agent")
async def ws_agent(ws: WebSocket, device_key: str):
    row = await pool.fetchrow("SELECT id, user_id FROM devices WHERE device_key = $1", device_key)
    if not row:
        await ws.close(code=4401)
        return
    uid, dev_id = row["user_id"], row["id"]
    await ws.accept()
    session_id = None
    stint_id = None
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
                t = msg.get("type")
            except Exception:
                continue
            try:
                if t == "session_start":
                    async with pool.acquire() as c:
                        car_id = await c.fetchval(
                            "INSERT INTO cars (user_id, name) VALUES ($1,$2) "
                            "ON CONFLICT (user_id, name) DO UPDATE SET name=EXCLUDED.name RETURNING id",
                            uid, msg["car"])
                        track_id = await c.fetchval(
                            "INSERT INTO tracks (user_id, name) VALUES ($1,$2) "
                            "ON CONFLICT (user_id, name) DO UPDATE SET name=EXCLUDED.name RETURNING id",
                            uid, msg["track"])
                        session_id = await c.fetchval(
                            "INSERT INTO sessions (user_id, client_uid, car_id, track_id, session_type, session_num, "
                            " air_temp, track_temp, humidity, wind_vel, wind_dir, track_usage) "
                            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) "
                            "ON CONFLICT (user_id, client_uid) DO UPDATE SET session_type=EXCLUDED.session_type "
                            "RETURNING id",
                            uid, msg["uid"], car_id, track_id, msg.get("sessionType", "Session"), msg.get("sessionNum", 0),
                            msg.get("airTemp"), msg.get("trackTemp"), msg.get("humidity"),
                            msg.get("windVel"), msg.get("windDir"), msg.get("trackUsage"))
                        stint_id = None
                elif t == "stint_start" and session_id is not None:
                    async with pool.acquire() as c:
                        stint_id = await c.fetchval(
                            "INSERT INTO stints (session_id, client_uid, setup_name, setup) "
                            "VALUES ($1,$2,$3,$4::jsonb) "
                            "ON CONFLICT (session_id, client_uid) DO UPDATE SET setup_name=EXCLUDED.setup_name "
                            "RETURNING id",
                            session_id, msg["uid"], msg.get("setupName"), json.dumps(msg.get("setup") or {}))
                elif t == "lap" and session_id is not None:
                    async with pool.acquire() as c:
                        await c.execute(
                            "INSERT INTO laps (session_id, stint_id, lap, time_s, air_temp, track_temp, humidity, wind_vel, wind_dir) "
                            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                            session_id, stint_id, int(msg["lap"]), float(msg["lastLapTime"]),
                            msg.get("airTemp"), msg.get("trackTemp"), msg.get("humidity"),
                            msg.get("windVel"), msg.get("windDir"))
                elif t == "hb":
                    await pool.execute("UPDATE devices SET last_seen = now() WHERE id = $1", dev_id)
            except Exception as e:
                print(f"[ws_agent] errore su messaggio '{t}': {e}", flush=True)
    except WebSocketDisconnect:
        pass


# ---------- Navigazione archivio ----------

@app.get("/api/cars")
async def list_cars(uid: str = Depends(uid_dep)):
    rows = await pool.fetch(
        "SELECT c.id, c.name, COUNT(s.id) AS sessions, MAX(s.started_at) AS last_used "
        "FROM cars c LEFT JOIN sessions s ON s.car_id = c.id "
        "WHERE c.user_id = $1 GROUP BY c.id ORDER BY last_used DESC NULLS LAST", uid)
    return [dict(r) for r in rows]


@app.get("/api/cars/{car_id}/tracks")
async def list_tracks(car_id: int, uid: str = Depends(uid_dep)):
    rows = await pool.fetch(
        "SELECT t.id, t.name, COUNT(s.id) AS sessions, MAX(s.started_at) AS last_used "
        "FROM tracks t JOIN sessions s ON s.track_id = t.id AND s.car_id = $1 "
        "WHERE t.user_id = $2 GROUP BY t.id ORDER BY last_used DESC", car_id, uid)
    return [dict(r) for r in rows]


@app.get("/api/cars/{car_id}/tracks/{track_id}/sessions")
async def list_sessions(car_id: int, track_id: int, uid: str = Depends(uid_dep)):
    rows = await pool.fetch(
        "SELECT s.id, s.session_type, s.started_at, COUNT(l.id) AS laps, MIN(l.time_s) AS best, "
        " s.air_temp, s.track_temp, s.humidity, s.wind_vel, s.wind_dir, s.track_usage "
        "FROM sessions s LEFT JOIN laps l ON l.session_id = s.id "
        "WHERE s.user_id = $1 AND s.car_id = $2 AND s.track_id = $3 "
        "GROUP BY s.id ORDER BY s.started_at DESC", uid, car_id, track_id)
    return [dict(r) for r in rows]


@app.get("/api/sessions/{session_id}/laps")
async def list_laps(session_id: int, uid: str = Depends(uid_dep)):
    meta = await pool.fetchrow(
        "SELECT s.id, s.session_type, s.started_at, c.name AS car, t.name AS track, "
        " s.air_temp, s.track_temp, s.humidity, s.wind_vel, s.wind_dir, s.track_usage "
        "FROM sessions s JOIN cars c ON c.id = s.car_id JOIN tracks t ON t.id = s.track_id "
        "WHERE s.id = $1 AND s.user_id = $2", session_id, uid)
    if not meta:
        raise HTTPException(404)
    laps = await pool.fetch(
        "SELECT lap, time_s, air_temp, track_temp, humidity, wind_vel, wind_dir, stint_id "
        "FROM laps WHERE session_id = $1 ORDER BY id", session_id)
    stints = await pool.fetch(
        "SELECT id, setup_name, setup, started_at FROM stints WHERE session_id = $1 ORDER BY started_at", session_id)
    by_stint = {s["id"]: {"id": s["id"], "setup_name": s["setup_name"],
                          "setup": json.loads(s["setup"]) if s["setup"] else {}, "laps": []}
                for s in stints}
    legacy = {"id": 0, "setup_name": None, "setup": {}, "laps": []}
    for l in laps:
        (by_stint.get(l["stint_id"]) or legacy)["laps"].append(
            {k: l[k] for k in ("lap", "time_s", "air_temp", "track_temp", "humidity", "wind_vel", "wind_dir")})
    out = ([legacy] if legacy["laps"] else []) + [s for s in by_stint.values() if s["laps"]]
    return {"session": dict(meta), "stints": out}
