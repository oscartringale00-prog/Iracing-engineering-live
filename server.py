import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

BASE = Path(__file__).parent
SCHEMA = (BASE / "schema.sql").read_text()
DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/iracing")

pool: asyncpg.Pool = None


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
async def index():
    return FileResponse(BASE / "index.html")


@app.websocket("/ws/agent")
async def ws_agent(ws: WebSocket, token: str):
    await ws.accept()
    session_id = None
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            t = msg.get("type")
            if t == "session_start":
                async with pool.acquire() as c:
                    car_id = await c.fetchval(
                        "INSERT INTO cars (token, name) VALUES ($1,$2) "
                        "ON CONFLICT (token, name) DO UPDATE SET name=EXCLUDED.name RETURNING id",
                        token, msg["car"])
                    track_id = await c.fetchval(
                        "INSERT INTO tracks (token, name) VALUES ($1,$2) "
                        "ON CONFLICT (token, name) DO UPDATE SET name=EXCLUDED.name RETURNING id",
                        token, msg["track"])
                    # client_uid dedup: la riconnessione dell'agente non duplica la sessione
                    session_id = await c.fetchval(
                        "INSERT INTO sessions (token, client_uid, car_id, track_id, session_type, session_num) "
                        "VALUES ($1,$2,$3,$4,$5,$6) "
                        "ON CONFLICT (token, client_uid) DO UPDATE SET session_type=EXCLUDED.session_type "
                        "RETURNING id",
                        token, msg["uid"], car_id, track_id, msg.get("sessionType", "Session"), msg.get("sessionNum", 0))
            elif t == "lap" and session_id is not None:
                async with pool.acquire() as c:
                    await c.execute(
                        "INSERT INTO laps (session_id, lap, time_s) VALUES ($1,$2,$3)",
                        session_id, int(msg["lap"]), float(msg["lastLapTime"]))
    except WebSocketDisconnect:
        pass


@app.get("/api/cars")
async def list_cars(token: str):
    rows = await pool.fetch(
        "SELECT c.id, c.name, COUNT(s.id) AS sessions, MAX(s.started_at) AS last_used "
        "FROM cars c LEFT JOIN sessions s ON s.car_id = c.id "
        "WHERE c.token = $1 GROUP BY c.id ORDER BY last_used DESC NULLS LAST", token)
    return [dict(r) for r in rows]


@app.get("/api/cars/{car_id}/tracks")
async def list_tracks(car_id: int, token: str):
    rows = await pool.fetch(
        "SELECT t.id, t.name, COUNT(s.id) AS sessions, MAX(s.started_at) AS last_used "
        "FROM tracks t JOIN sessions s ON s.track_id = t.id AND s.car_id = $1 "
        "WHERE t.token = $2 GROUP BY t.id ORDER BY last_used DESC", car_id, token)
    return [dict(r) for r in rows]


@app.get("/api/cars/{car_id}/tracks/{track_id}/sessions")
async def list_sessions(car_id: int, track_id: int, token: str):
    rows = await pool.fetch(
        "SELECT s.id, s.session_type, s.started_at, COUNT(l.id) AS laps, MIN(l.time_s) AS best "
        "FROM sessions s LEFT JOIN laps l ON l.session_id = s.id "
        "WHERE s.token = $1 AND s.car_id = $2 AND s.track_id = $3 "
        "GROUP BY s.id ORDER BY s.started_at DESC", token, car_id, track_id)
    return [dict(r) for r in rows]


@app.get("/api/sessions/{session_id}/laps")
async def list_laps(session_id: int, token: str):
    meta = await pool.fetchrow(
        "SELECT s.id, s.session_type, s.started_at, c.name AS car, t.name AS track "
        "FROM sessions s JOIN cars c ON c.id = s.car_id JOIN tracks t ON t.id = s.track_id "
        "WHERE s.id = $1 AND s.token = $2", session_id, token)
    if not meta:
        raise HTTPException(404)
    laps = await pool.fetch(
        "SELECT lap, time_s FROM laps WHERE session_id = $1 ORDER BY lap", session_id)
    return {"session": dict(meta), "laps": [dict(r) for r in laps]}
