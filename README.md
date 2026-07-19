# iRacing Telemetry — Archivio sessioni

Agente Windows → backend cloud (Railway + Postgres) → archivio web navigabile:
Auto → Piste → Sessioni (data/ora + tipo) → Tempi giro.

## Aggiornamento del deploy esistente su Railway (da fare una volta)
1. **Aggiungi il database**: nel progetto Railway clicca "+ Add" (o "New") → "Database" → "Add PostgreSQL". Railway lo crea in pochi secondi.
2. **Collega il database al server**: apri il servizio web (quello collegato a GitHub) → scheda "Variables" → "Add Variable Reference" → scegli `DATABASE_URL` dal servizio Postgres. Salva: il server riparte da solo con la variabile.
3. **Carica i file aggiornati su GitHub** (sovrascrivendo i vecchi): `server.py`, `index.html`, `agent.py`, `schema.sql`, `requirements.txt`, `Procfile`. Railway rifà il deploy da solo. Le tabelle vengono create automaticamente al primo avvio (schema.sql).
4. **Ricostruisci l'exe** (l'agente è cambiato):
   pip install pyirsdk pyinstaller websocket-client
   python -m PyInstaller --onefile --name iRacingLive agent.py
   Il nuovo `dist/iRacingLive.exe` va ridistribuito ai clienti. Il token in `config.ini` resta lo stesso, quindi chi aveva già il programma ritrova i propri dati.

## Test locale senza iRacing
Serve un Postgres locale raggiungibile come `postgresql://postgres:postgres@localhost/iracing`
(oppure imposta la variabile d'ambiente `DATABASE_URL`).
    python -m uvicorn server:app --port 8000     # terminale 1
    python agent.py --demo                        # terminale 2 (backend = ws://localhost:8000 in config.ini)
La demo simula 2 sessioni (Prove Libere + Gara) su Monza con la MX-5 Cup, un giro ogni ~8s.

## Protocollo agente → server (WebSocket /ws/agent?token=...)
- {"type":"session_start","uid":"...","car":"...","track":"...","sessionType":"Race","sessionNum":0,"ts":...}
  (uid generato dall'agente: le riconnessioni non duplicano la sessione)
- {"type":"lap","lap":12,"lastLapTime":92.431,"ts":...}
- {"type":"hb"}

## API REST (tutte con ?token=...)
- GET /api/cars
- GET /api/cars/{car_id}/tracks
- GET /api/cars/{car_id}/tracks/{track_id}/sessions
- GET /api/sessions/{id}/laps

## Note
- Ogni utente vede solo i propri dati (filtro per token in ogni query).
- I dati sono permanenti: vivono nel Postgres di Railway, non nel filesystem del server.
