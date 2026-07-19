import json
import os
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

INDEX = Path(__file__).parent / "index.html"
AGENT_TIMEOUT = 15

# Stato in memoria per token: {agent, viewers, laps, iracing, last_seen}
rooms: dict[str, dict] = {}


def room(token: str) -> dict:
    return rooms.setdefault(token, {"agent": None, "viewers": set(), "laps": [], "iracing": False, "last_seen": 0})


async def broadcast(r: dict, msg: dict):
    dead = []
    for v in r["viewers"]:
        try:
            await v.send_text(json.dumps(msg))
        except Exception:
            dead.append(v)
    for v in dead:
        r["viewers"].discard(v)


@app.get("/")
async def index():
    return FileResponse(INDEX)


@app.websocket("/ws/agent")
async def ws_agent(ws: WebSocket, token: str):
    await ws.accept()
    r = room(token)
    r["agent"] = ws
    r["last_seen"] = time.time()
    await broadcast(r, {"type": "agent", "online": True, "iracing": r["iracing"]})
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            r["last_seen"] = time.time()
            if msg["type"] == "lap":
                lap = {"lap": msg["lap"], "lastLapTime": msg["lastLapTime"], "ts": msg.get("ts", time.time())}
                r["laps"].append(lap)
                await broadcast(r, {"type": "lap", **lap})
            elif msg["type"] == "status":
                r["iracing"] = bool(msg.get("iracing"))
                # Nuova sessione iRacing: azzera lo storico giri
                if r["iracing"]:
                    r["laps"] = []
                    await broadcast(r, {"type": "reset"})
                await broadcast(r, {"type": "agent", "online": True, "iracing": r["iracing"]})
    except WebSocketDisconnect:
        pass
    finally:
        if r["agent"] is ws:
            r["agent"] = None
            r["iracing"] = False
            await broadcast(r, {"type": "agent", "online": False, "iracing": False})


@app.websocket("/ws/viewer")
async def ws_viewer(ws: WebSocket, token: str):
    await ws.accept()
    r = room(token)
    r["viewers"].add(ws)
    online = r["agent"] is not None and time.time() - r["last_seen"] < AGENT_TIMEOUT
    await ws.send_text(json.dumps({"type": "init", "laps": r["laps"], "agentOnline": online, "iracing": r["iracing"] if online else False}))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        r["viewers"].discard(ws)
