# iRacing Telemetry — v3 con account utente (Firebase)

Agente Windows (collegato una volta all'account) → backend Railway + Postgres → archivio web personale con login.

## 1. Console Firebase (da fare una volta)
1. https://console.firebase.google.com → apri il tuo progetto (o creane uno).
2. Authentication → "Sign-in method" → abilita **Email/Password** e **Google**.
3. Authentication → "Settings" → "Authorized domains" → aggiungi il dominio Railway:
   `web-production-8fbbf.up.railway.app`
4. Impostazioni progetto (ingranaggio) → "Generale" → sezione "Le tue app" → se non c'è, crea una App Web (icona `</>`), poi copia i valori `apiKey`, `authDomain`, `projectId`.

## 2. index.html
In cima allo script c'è il blocco CONFIGURAZIONE FIREBASE: sostituisci
`INCOLLA_API_KEY` e `INCOLLA_PROJECT_ID` con i valori copiati al punto 1.4.

## 3. Railway
Servizio web → Variables → aggiungi:
  FIREBASE_PROJECT_ID = <il tuo projectId>
Il database esistente verrà riusato ma lo schema è nuovo (si riparte da zero, come deciso).
Per pulizia, puoi svuotare il vecchio DB: riquadro Postgres → Data/Query → esegui:
  DROP TABLE IF EXISTS laps, stints, sessions, cars, tracks CASCADE;
(le tabelle nuove si creano da sole al riavvio del server).

## 4. GitHub
Carica sovrascrivendo: server.py, index.html, agent.py, schema.sql, requirements.txt.
Railway rifà il deploy da solo.

## 5. Exe (⚠️ terminale)
    pip install pyirsdk websocket-client pyinstaller
    python -m PyInstaller --onefile --name iRacingLive agent.py
Il nuovo dist/iRacingLive.exe sostituisce il vecchio.

## Flusso utente finale
1. Si registra sul sito (email/password o Google).
2. Apre iRacingLive.exe: si apre il browser su "Collega questo PC" → un click.
3. Da lì in poi: solo doppio click. I dati finiscono nel suo profilo, visibili da qualsiasi dispositivo dopo il login.
Sezione "Dispositivi" nel sito per scollegare i PC.

## Test locale senza Firebase/iRacing
    AUTH_DEBUG=1 python -m uvicorn server:app --port 8000
    python agent.py --demo        # backend = ws://localhost:8000 in config.ini
Con AUTH_DEBUG=1 il server accetta token fittizi "debug-<uid>" (SOLO per test locale: mai impostare AUTH_DEBUG in produzione).

## API (tutte con header Authorization: Bearer <idToken Firebase>)
- POST /api/device/start → {code}            (senza auth: lo chiama l'exe)
- GET  /api/device/claim?code=X              (senza auth: polling dell'exe)
- POST /api/device/link {code}               (con auth: conferma dell'utente)
- GET/DELETE /api/devices[/id]
- GET /api/cars · /api/cars/{id}/tracks · /api/cars/{c}/tracks/{t}/sessions · /api/sessions/{id}/laps
WebSocket agente: /ws/agent?device_key=...
