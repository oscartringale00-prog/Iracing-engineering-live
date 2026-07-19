# iRacing Live Timing

Agente Windows → backend cloud → dashboard web. Giro e tempo in tempo reale.

## Esperienza utente finale
1. Scarica `iRacingLive.exe` e fa doppio click.
2. Il browser si apre da solo sulla sua dashboard privata (token generato automaticamente al primo avvio, salvato in `config.ini` accanto all'exe).
3. Appena entra in pista, i giri compaiono da soli. Fine.

## Deploy backend (una volta sola, tu)
```bash
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8000
```
Funziona su Railway / Fly.io / VPS. Metti `index.html` nella stessa cartella di `server.py` (viene servito su `/`). In produzione usa HTTPS/WSS (reverse proxy o piattaforma che lo fornisce).

## Build dell'agente (una volta sola, tu)
1. In `agent.py` imposta `DEFAULT_BACKEND = "wss://tuodominio.com"`.
2. Su Windows:
```bash
pip install pyirsdk websocket-client pyinstaller
pyinstaller --onefile --name iRacingLive agent.py
```
L'exe è in `dist/iRacingLive.exe`.

## Test end-to-end senza iRacing
```bash
uvicorn server:app --port 8000            # terminale 1
python agent.py --demo                     # terminale 2 (con DEFAULT_BACKEND=ws://localhost:8000)
```
La modalità `--demo` genera un giro simulato ogni ~12 secondi.

## Protocollo messaggi (JSON su WebSocket)
- Agente → server: `{"type":"lap","lap":12,"lastLapTime":92.431,"ts":...}` · `{"type":"status","iracing":true}` · `{"type":"hb"}`
- Server → viewer: `{"type":"init","laps":[...],"agentOnline":true,"iracing":true}` · `{"type":"lap",...}` · `{"type":"agent","online":bool,"iracing":bool}` · `{"type":"reset"}`

## Note
- Stato in memoria: al riavvio del server lo storico si azzera (ok per MVP).
- Il token nell'URL identifica la stanza; per la vendita potrai sostituirlo con account/licenze senza toccare l'agente (stesso campo in `config.ini`).
