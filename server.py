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
                            "INSERT INTO laps (session_id, stint_id, lap, time_s, client_lap_uid, air_temp, track_temp, humidity, wind_vel, wind_dir) "
                            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) "
                            "ON CONFLICT (session_id, client_lap_uid) WHERE client_lap_uid IS NOT NULL "
                            "DO NOTHING",
                            session_id, stint_id, int(msg["lap"]), float(msg["lastLapTime"]), msg.get("lapUid"),
                            msg.get("airTemp"), msg.get("trackTemp"), msg.get("humidity"),
                            msg.get("windVel"), msg.get("windDir"))
                elif t == "lap_telemetry" and session_id is not None:
                    s = msg.get("samples") or {}
                    ch = ["speed","throttle","brake","clutch","steer","gear","rpm","lapdist",
                          "lataccel","lonaccel","vertaccel",
                          "rh_lf","rh_rf","rh_lr","rh_rr","shock_lf","shock_rf","shock_lr","shock_rr",
                          "lat","lon"]
                    present = [k for k in ch if s.get(k) is not None]
                    cols = ["session_id", "lap_uid"] + present
                    ph = ",".join(f"${i+1}" for i in range(len(cols)))
                    async with pool.acquire() as c:
                        await c.execute(
                            f"INSERT INTO lap_telemetry ({','.join(cols)}) VALUES ({ph}) "
                            "ON CONFLICT (session_id, lap_uid) DO NOTHING",
                            session_id, msg["lapUid"], *[s[k] for k in present])
                elif t == "hb":
                    await pool.execute("UPDATE devices SET last_seen = now() WHERE id = $1", dev_id)
            except Exception as e:
                print(f"[ws_agent] errore su messaggio '{t}': {e}", flush=True)
    except WebSocketDisconnect:
        pass


# ---------- Navigazione archivio ----------

"""====== VISIBILITÀ TRA UTENTI ======
Un utente X può vedere i dati di Y se e solo se:
  1) X è Y, oppure
  2) Y ha l'interruttore pubblico acceso, oppure
  3) X e Y condividono almeno un team.
La verifica avviene SEMPRE qui sul server, mai solo lato interfaccia.
"""

SHARED_TEAM_SQL = ("EXISTS (SELECT 1 FROM team_members a JOIN team_members b ON a.team_id = b.team_id "
                   "WHERE a.user_id = $1 AND b.user_id = $2)")


async def can_view(viewer: str, target: str) -> bool:
    if viewer == target:
        return True
    if await pool.fetchval("SELECT TRUE FROM profiles WHERE user_id = $1 AND is_public", target):
        return True
    return bool(await pool.fetchval(f"SELECT {SHARED_TEAM_SQL}", viewer, target))


async def owner_or_404(viewer: str, pilot: str | None) -> str:
    """Ritorna l'utente di cui leggere i dati; 404 se non visibile (non riveliamo l'esistenza)."""
    target = pilot or viewer
    if not await can_view(viewer, target):
        raise HTTPException(404)
    return target


async def pilot_name_of(uid: str) -> str | None:
    return await pool.fetchval("SELECT pilot_name FROM profiles WHERE user_id = $1", uid)


async def require_pilot_name(uid: str) -> str:
    name = await pilot_name_of(uid)
    if not name:
        raise HTTPException(400, "Imposta prima il tuo nome pilota")
    return name


# ---------- Profilo ----------
@app.get("/api/profile")
async def get_profile(uid: str = Depends(uid_dep)):
    r = await pool.fetchrow("SELECT pilot_name, is_public FROM profiles WHERE user_id = $1", uid)
    if not r:
        await pool.execute("INSERT INTO profiles (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid)
        return {"pilot_name": None, "is_public": False}
    return {"pilot_name": r["pilot_name"], "is_public": r["is_public"]}


class ProfileIn(BaseModel):
    pilot_name: str
    is_public: bool = False


@app.put("/api/profile")
async def set_profile(body: ProfileIn, uid: str = Depends(uid_dep)):
    name = (body.pilot_name or "").strip()[:60]
    if not name:
        raise HTTPException(400, "Il nome pilota non può essere vuoto")
    await pool.execute(
        "INSERT INTO profiles (user_id, pilot_name, is_public) VALUES ($1,$2,$3) "
        "ON CONFLICT (user_id) DO UPDATE SET pilot_name = $2, is_public = $3",
        uid, name, bool(body.is_public))
    return {"ok": True}


# ---------- Piloti visibili ----------
@app.get("/api/pilots")
async def list_pilots(q: str = "", uid: str = Depends(uid_dep)):
    rows = await pool.fetch(
        f"SELECT p.user_id, p.pilot_name, p.is_public, {SHARED_TEAM_SQL.replace('$2','p.user_id')} AS teammate "
        "FROM profiles p "
        "WHERE p.user_id <> $1 AND p.pilot_name IS NOT NULL "
        f"  AND (p.is_public OR {SHARED_TEAM_SQL.replace('$2','p.user_id')}) "
        "  AND ($2 = '' OR lower(p.pilot_name) LIKE '%' || lower($2) || '%') "
        "ORDER BY p.pilot_name LIMIT 100", uid, q)
    return [{"id": r["user_id"], "name": r["pilot_name"],
             "public": r["is_public"], "teammate": r["teammate"]} for r in rows]


# ---------- Team ----------
class TeamIn(BaseModel):
    name: str


@app.post("/api/teams")
async def create_team(body: TeamIn, uid: str = Depends(uid_dep)):
    await require_pilot_name(uid)
    name = (body.name or "").strip()[:60]
    if not name:
        raise HTTPException(400, "Il nome del team non può essere vuoto")
    tid = await pool.fetchval(
        "INSERT INTO teams (name, manager_id) VALUES ($1,$2) RETURNING id", name, uid)
    await pool.execute("INSERT INTO team_members (team_id, user_id) VALUES ($1,$2)", tid, uid)
    return {"id": tid, "name": name}


@app.get("/api/teams")
async def my_teams(uid: str = Depends(uid_dep)):
    rows = await pool.fetch(
        "SELECT t.id, t.name, (t.manager_id = $1) AS is_manager, "
        " (SELECT COUNT(*) FROM team_members m WHERE m.team_id = t.id) AS members, "
        " (SELECT COUNT(*) FROM team_requests r WHERE r.team_id = t.id AND r.status = 'pending') AS pending "
        "FROM teams t JOIN team_members me ON me.team_id = t.id AND me.user_id = $1 "
        "ORDER BY t.name", uid)
    return [dict(r) for r in rows]


@app.get("/api/teams/search")
async def search_teams(q: str = "", uid: str = Depends(uid_dep)):
    rows = await pool.fetch(
        "SELECT t.id, t.name, "
        " (SELECT p.pilot_name FROM profiles p WHERE p.user_id = t.manager_id) AS manager, "
        " (SELECT COUNT(*) FROM team_members m WHERE m.team_id = t.id) AS members, "
        " EXISTS(SELECT 1 FROM team_members m WHERE m.team_id = t.id AND m.user_id = $1) AS is_member, "
        " EXISTS(SELECT 1 FROM team_requests r WHERE r.team_id = t.id AND r.user_id = $1 "
        "        AND r.status = 'pending') AS pending "
        "FROM teams t WHERE ($2 = '' OR lower(t.name) LIKE '%' || lower($2) || '%') "
        "ORDER BY t.name LIMIT 100", uid, q)
    return [dict(r) for r in rows]


@app.post("/api/teams/{team_id}/request")
async def request_join(team_id: int, uid: str = Depends(uid_dep)):
    await require_pilot_name(uid)
    if not await pool.fetchval("SELECT TRUE FROM teams WHERE id = $1", team_id):
        raise HTTPException(404)
    if await pool.fetchval("SELECT TRUE FROM team_members WHERE team_id=$1 AND user_id=$2", team_id, uid):
        raise HTTPException(400, "Fai già parte di questo team")
    await pool.execute(
        "INSERT INTO team_requests (team_id, user_id, status) VALUES ($1,$2,'pending') "
        "ON CONFLICT (team_id, user_id) DO UPDATE SET status='pending', created_at=now()", team_id, uid)
    return {"ok": True}


async def manager_or_404(team_id: int, uid: str):
    mgr = await pool.fetchval("SELECT manager_id FROM teams WHERE id = $1", team_id)
    if mgr is None or mgr != uid:
        raise HTTPException(404)


@app.get("/api/teams/{team_id}/requests")
async def team_requests(team_id: int, uid: str = Depends(uid_dep)):
    await manager_or_404(team_id, uid)
    rows = await pool.fetch(
        "SELECT r.id, r.user_id, r.created_at, p.pilot_name "
        "FROM team_requests r LEFT JOIN profiles p ON p.user_id = r.user_id "
        "WHERE r.team_id = $1 AND r.status = 'pending' ORDER BY r.created_at", team_id)
    return [{"id": r["id"], "pilot": r["pilot_name"] or "—", "created_at": r["created_at"]} for r in rows]


class RequestAction(BaseModel):
    action: str  # approve | reject


@app.post("/api/teams/{team_id}/requests/{req_id}")
async def handle_request(team_id: int, req_id: int, body: RequestAction, uid: str = Depends(uid_dep)):
    await manager_or_404(team_id, uid)
    req = await pool.fetchrow(
        "SELECT user_id FROM team_requests WHERE id=$1 AND team_id=$2 AND status='pending'", req_id, team_id)
    if not req:
        raise HTTPException(404)
    if body.action == "approve":
        await pool.execute("INSERT INTO team_members (team_id, user_id) VALUES ($1,$2) "
                           "ON CONFLICT DO NOTHING", team_id, req["user_id"])
        await pool.execute("UPDATE team_requests SET status='approved' WHERE id=$1", req_id)
    elif body.action == "reject":
        await pool.execute("UPDATE team_requests SET status='rejected' WHERE id=$1", req_id)
    else:
        raise HTTPException(400, "Azione non valida")
    return {"ok": True}


@app.get("/api/teams/{team_id}/members")
async def team_members(team_id: int, uid: str = Depends(uid_dep)):
    if not await pool.fetchval("SELECT TRUE FROM team_members WHERE team_id=$1 AND user_id=$2", team_id, uid):
        raise HTTPException(404)
    mgr = await pool.fetchval("SELECT manager_id FROM teams WHERE id=$1", team_id)
    rows = await pool.fetch(
        "SELECT m.user_id, m.joined_at, p.pilot_name FROM team_members m "
        "LEFT JOIN profiles p ON p.user_id = m.user_id WHERE m.team_id = $1 ORDER BY m.joined_at", team_id)
    return [{"id": r["user_id"], "name": r["pilot_name"] or "—",
             "is_manager": r["user_id"] == mgr, "is_me": r["user_id"] == uid} for r in rows]


@app.delete("/api/teams/{team_id}/members/{member_id}")
async def remove_member(team_id: int, member_id: str, uid: str = Depends(uid_dep)):
    await manager_or_404(team_id, uid)
    if member_id == uid:
        raise HTTPException(400, "Il manager non può rimuovere sé stesso: usa 'esci dal team'")
    await pool.execute("DELETE FROM team_members WHERE team_id=$1 AND user_id=$2", team_id, member_id)
    await pool.execute("DELETE FROM team_requests WHERE team_id=$1 AND user_id=$2", team_id, member_id)
    return {"ok": True}


@app.post("/api/teams/{team_id}/leave")
async def leave_team(team_id: int, uid: str = Depends(uid_dep)):
    if not await pool.fetchval("SELECT TRUE FROM team_members WHERE team_id=$1 AND user_id=$2", team_id, uid):
        raise HTTPException(404)
    await pool.execute("DELETE FROM team_members WHERE team_id=$1 AND user_id=$2", team_id, uid)
    await pool.execute("DELETE FROM team_requests WHERE team_id=$1 AND user_id=$2", team_id, uid)
    mgr = await pool.fetchval("SELECT manager_id FROM teams WHERE id=$1", team_id)
    if mgr == uid:
        # il manager esce: passa il ruolo al membro più anziano, altrimenti il team viene sciolto
        heir = await pool.fetchval(
            "SELECT user_id FROM team_members WHERE team_id=$1 ORDER BY joined_at LIMIT 1", team_id)
        if heir:
            await pool.execute("UPDATE teams SET manager_id=$1 WHERE id=$2", heir, team_id)
        else:
            await pool.execute("DELETE FROM teams WHERE id=$1", team_id)
    return {"ok": True}


@app.get("/api/cars")
async def list_cars(pilot: str | None = None, uid: str = Depends(uid_dep)):
    owner = await owner_or_404(uid, pilot)
    rows = await pool.fetch(
        "SELECT c.id, c.name, COUNT(s.id) AS sessions, MAX(s.started_at) AS last_used "
        "FROM cars c LEFT JOIN sessions s ON s.car_id = c.id "
        "WHERE c.user_id = $1 GROUP BY c.id ORDER BY last_used DESC NULLS LAST", owner)
    return [dict(r) for r in rows]


@app.get("/api/cars/{car_id}/tracks")
async def list_tracks(car_id: int, pilot: str | None = None, uid: str = Depends(uid_dep)):
    owner = await owner_or_404(uid, pilot)
    rows = await pool.fetch(
        "SELECT t.id, t.name, COUNT(s.id) AS sessions, MAX(s.started_at) AS last_used "
        "FROM tracks t JOIN sessions s ON s.track_id = t.id AND s.car_id = $1 "
        "WHERE t.user_id = $2 GROUP BY t.id ORDER BY last_used DESC", car_id, owner)
    return [dict(r) for r in rows]


@app.get("/api/cars/{car_id}/tracks/{track_id}/sessions")
async def list_sessions(car_id: int, track_id: int, pilot: str | None = None, uid: str = Depends(uid_dep)):
    owner = await owner_or_404(uid, pilot)
    rows = await pool.fetch(
        "SELECT s.id, s.session_type, s.started_at, COUNT(l.id) AS laps, MIN(l.time_s) AS best, "
        " s.air_temp, s.track_temp, s.humidity, s.wind_vel, s.wind_dir, s.track_usage "
        "FROM sessions s LEFT JOIN laps l ON l.session_id = s.id "
        "WHERE s.user_id = $1 AND s.car_id = $2 AND s.track_id = $3 "
        "GROUP BY s.id ORDER BY s.started_at DESC", owner, car_id, track_id)
    return [dict(r) for r in rows]


@app.get("/api/sessions/{session_id}/laps")
async def list_laps(session_id: int, uid: str = Depends(uid_dep)):
    meta = await pool.fetchrow(
        "SELECT s.id, s.user_id, s.session_type, s.started_at, c.name AS car, t.name AS track, "
        " s.air_temp, s.track_temp, s.humidity, s.wind_vel, s.wind_dir, s.track_usage "
        "FROM sessions s JOIN cars c ON c.id = s.car_id JOIN tracks t ON t.id = s.track_id "
        "WHERE s.id = $1", session_id)
    if not meta or not await can_view(uid, meta["user_id"]):
        raise HTTPException(404)
    laps = await pool.fetch(
        "SELECT l.id, l.lap, l.time_s, l.air_temp, l.track_temp, l.humidity, l.wind_vel, l.wind_dir, l.stint_id, "
        " (tel.id IS NOT NULL) AS has_telemetry "
        "FROM laps l LEFT JOIN lap_telemetry tel "
        "  ON tel.session_id = l.session_id AND tel.lap_uid = l.client_lap_uid "
        "WHERE l.session_id = $1 ORDER BY l.id", session_id)
    stints = await pool.fetch(
        "SELECT id, setup_name, setup, started_at FROM stints WHERE session_id = $1 ORDER BY started_at", session_id)
    by_stint = {s["id"]: {"id": s["id"], "setup_name": s["setup_name"],
                          "setup": json.loads(s["setup"]) if s["setup"] else {}, "laps": []}
                for s in stints}
    legacy = {"id": 0, "setup_name": None, "setup": {}, "laps": []}
    for l in laps:
        (by_stint.get(l["stint_id"]) or legacy)["laps"].append(
            {k: l[k] for k in ("id", "lap", "time_s", "air_temp", "track_temp", "humidity", "wind_vel", "wind_dir", "has_telemetry")})
    out = ([legacy] if legacy["laps"] else []) + [s for s in by_stint.values() if s["laps"]]
    sess = {k: meta[k] for k in meta.keys() if k != "user_id"}
    sess["pilot"] = await pilot_name_of(meta["user_id"]) if meta["user_id"] != uid else None
    return {"session": sess, "stints": out}


@app.delete("/api/cars/{car_id}")
async def delete_car(car_id: int, uid: str = Depends(uid_dep)):
    r = await pool.execute("DELETE FROM cars WHERE id = $1 AND user_id = $2", car_id, uid)
    if r == "DELETE 0":
        raise HTTPException(404)
    return {"ok": True}


@app.delete("/api/cars/{car_id}/tracks/{track_id}")
async def delete_car_track(car_id: int, track_id: int, uid: str = Depends(uid_dep)):
    # Cancella tutte le sessioni di questa combinazione auto+pista (cascata su stint/giri/telemetria).
    r = await pool.execute(
        "DELETE FROM sessions WHERE user_id = $1 AND car_id = $2 AND track_id = $3", uid, car_id, track_id)
    if r == "DELETE 0":
        raise HTTPException(404)
    # Se non restano altre sessioni per questa pista (con qualunque auto dell'utente), rimuovi anche la pista.
    left = await pool.fetchval(
        "SELECT COUNT(*) FROM sessions WHERE user_id = $1 AND track_id = $2", uid, track_id)
    if left == 0:
        await pool.execute("DELETE FROM tracks WHERE id = $1 AND user_id = $2", track_id, uid)
    return {"ok": True}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: int, uid: str = Depends(uid_dep)):
    r = await pool.execute("DELETE FROM sessions WHERE id = $1 AND user_id = $2", session_id, uid)
    if r == "DELETE 0":
        raise HTTPException(404)
    return {"ok": True}


@app.get("/api/laps/{lap_id}/telemetry")
async def lap_telemetry(lap_id: int, uid: str = Depends(uid_dep)):
    meta = await pool.fetchrow(
        "SELECT l.id, l.lap, l.time_s, l.client_lap_uid, l.session_id, s.user_id, s.track_id, "
        " c.name AS car, t.name AS track "
        "FROM laps l JOIN sessions s ON s.id = l.session_id "
        "JOIN cars c ON c.id = s.car_id JOIN tracks t ON t.id = s.track_id "
        "WHERE l.id = $1", lap_id)
    if not meta or not await can_view(uid, meta["user_id"]):
        raise HTTPException(404)
    tel = await pool.fetchrow(
        "SELECT speed, throttle, brake, clutch, steer, gear, rpm, lapdist, lataccel, lonaccel, vertaccel, "
        "rh_lf, rh_rf, rh_lr, rh_rr, shock_lf, shock_rf, shock_lr, shock_rr, lat, lon "
        "FROM lap_telemetry WHERE session_id = $1 AND lap_uid = $2", meta["session_id"], meta["client_lap_uid"])
    if not tel:
        raise HTTPException(404, "Telemetria non disponibile per questo giro")
    return {"lap": {"lap": meta["lap"], "time_s": meta["time_s"], "car": meta["car"], "track": meta["track"],
                    "track_id": meta["track_id"],
                    "pilot": (await pilot_name_of(meta["user_id"])) if meta["user_id"] != uid else None},
            "channels": {k: list(tel[k]) if tel[k] is not None else [] for k in tel.keys()}}


async def track_name_visible(track_id: int, uid: str) -> str:
    """Nome della pista di riferimento; 404 se la pista non è visibile all'utente."""
    tr = await pool.fetchrow("SELECT name, user_id FROM tracks WHERE id = $1", track_id)
    if not tr or not await can_view(uid, tr["user_id"]):
        raise HTTPException(404)
    return tr["name"]


@app.get("/api/tracks/{track_id}/pilots")
async def track_pilots(track_id: int, uid: str = Depends(uid_dep)):
    """Piloti visibili che hanno almeno un giro con telemetria su QUESTA pista (abbinata per nome,
    perché ogni utente ha la propria riga pista)."""
    tname = await track_name_visible(track_id, uid)
    rows = await pool.fetch(
        "SELECT s.user_id, COUNT(*) AS laps FROM sessions s "
        "JOIN tracks t ON t.id = s.track_id AND lower(t.name) = lower($2) "
        "JOIN laps l ON l.session_id = s.id "
        "JOIN lap_telemetry tel ON tel.session_id = l.session_id AND tel.lap_uid = l.client_lap_uid "
        "WHERE s.user_id = $1 OR EXISTS(SELECT 1 FROM profiles p WHERE p.user_id = s.user_id AND p.is_public) "
        f"   OR {SHARED_TEAM_SQL.replace('$2','s.user_id')} "
        "GROUP BY s.user_id", uid, tname)
    out = []
    for r in rows:
        name = await pilot_name_of(r["user_id"])
        out.append({"id": r["user_id"], "name": (name or "Io") if r["user_id"] != uid else "I miei giri",
                    "laps": r["laps"], "is_me": r["user_id"] == uid})
    out.sort(key=lambda x: (not x["is_me"], x["name"].lower()))
    return out


@app.get("/api/tracks/{track_id}/comparable")
async def track_comparable(track_id: int, pilot: str | None = None, uid: str = Depends(uid_dep)):
    """Sessioni (con i giri che hanno telemetria) di un pilota su questa pista.
    Senza 'pilot' ritorna le proprie: mantiene il comportamento della Tappa 1."""
    tname = await track_name_visible(track_id, uid)
    owner = await owner_or_404(uid, pilot)
    rows = await pool.fetch(
        "SELECT s.id AS session_id, s.session_type, s.started_at, c.name AS car, "
        " l.id AS lap_id, l.lap, l.time_s "
        "FROM sessions s JOIN cars c ON c.id = s.car_id "
        "JOIN tracks t ON t.id = s.track_id AND lower(t.name) = lower($2) "
        "JOIN laps l ON l.session_id = s.id "
        "JOIN lap_telemetry tel ON tel.session_id = l.session_id AND tel.lap_uid = l.client_lap_uid "
        "WHERE s.user_id = $1 "
        "ORDER BY s.started_at DESC, l.lap ASC", owner, tname)
    sessions = {}
    for r in rows:
        sid = r["session_id"]
        if sid not in sessions:
            sessions[sid] = {"session_id": sid, "session_type": r["session_type"],
                             "started_at": r["started_at"], "car": r["car"], "laps": []}
        sessions[sid]["laps"].append({"lap_id": r["lap_id"], "lap": r["lap"], "time_s": r["time_s"]})
    return list(sessions.values())
