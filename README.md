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

## v4 — Telemetria ad alta frequenza (grafici giro)
- L'agente campiona 12 canali alla frequenza nativa iRacing e invia la telemetria del giro completato in un unico messaggio `lap_telemetry`.
- Nuova tabella `lap_telemetry` (array per canale) + colonna `client_lap_uid` su `laps`: si creano da sole al riavvio del server (schema.sql).
- Nella pagina tempi, i giri con telemetria mostrano 📈 e sono cliccabili → vista con mappa circuito (da GPS lat/lon) + grafici velocità/gas/freno/marcia/sterzo, cursore sincronizzato mappa↔grafici (mouse e touch).
- Deploy: carica su GitHub server.py, index.html, agent.py, schema.sql (Railway ridispiega da solo). ⚠️ agent.py cambiato → ricostruisci l'exe:
    python -m PyInstaller --onefile --name iRacingLive agent.py
- Nota costi: con frequenza nativa e tutti i giri salvati un giro ~90s ≈ 5.000 campioni/canale; se DB/costi Railway crescono troppo, valuta poi meno frequenza o solo giri migliori.

## v5 — Cancellazione auto/piste/sessioni
- X rossa su ogni riga (auto, pista, sessione) con conferma obbligatoria prima di cancellare.
- Cancellazione a cascata: auto → tutte le sue piste/sessioni/stint/giri/telemetria; pista → le sue sessioni (per quell'auto) e tutto il contenuto; sessione → i suoi stint/giri/telemetria.
- ✅ Nessun terminale: agent.py non è stato toccato.
- Deploy: carica su GitHub server.py, index.html, schema.sql. Railway ridispiega da solo.
- IMPORTANTE (una tantum sul database esistente): lo schema aggiorna automaticamente i vincoli di cancellazione a cascata al riavvio del server (query idempotenti in schema.sql), nessun intervento manuale extra necessario su Railway.


## v7 — Confronto giri (Tappa 1: propri giri, stessa pista)
- Nella vista telemetria: pulsante "+ Aggiungi giro" → popup che elenca le proprie sessioni sulla stessa pista (qualsiasi auto) e i loro giri con telemetria. Fino a 3 giri sovrapposti.
- Giro principale rosso; giri aggiunti con colore personalizzabile (selettore colore) e X per rimuoverli.
- Grafici: una linea per giro nel proprio colore, allineate per lapdist. Canali a 2 serie (altezze/ammortizzatori): in confronto mostrano solo la prima serie per giro.
- Mappa stile Garage 61: traiettoria di ogni giro sovrapposta nel proprio colore + un pallino per giro che segue il cursore.
- Readout: una riga per giro col valore in quel punto.
- Nuovo endpoint server: GET /api/tracks/{id}/comparable (sessioni+giri dell'utente su quella pista, scoping uid). /api/laps/{id}/telemetry ora include track_id.
- ✅ Nessun terminale: agent invariato. Deploy: carica server.py e index.html su GitHub.

## v8 — Responsività + zoom/pan mappa
- Contenitori resi fluidi: max-width min(96vw,1040px), il sito sfrutta meglio gli schermi grandi restando a colonna singola su mobile.
- Vista telemetria: mappa e grafici affiancati meglio su desktop, mappa più grande (340px), sticky mentre si scorrono i grafici.
- Mappa telemetria: zoom con rotellina (verso il puntatore) e pinch a due dita; pan trascinando (mouse sinistro o un dito); si torna alla vista intera zoomando all'indietro. Il cursore/pallini restano guidati dai grafici.
- ✅ Nessun terminale, solo index.html. agent/server invariati.

## v9 — Mappa: proporzioni reali + pallino che segue il mouse
- Aspect ratio del tracciato corretto (correzione longitudine × cos(latitudine)): la forma della pista è geograficamente fedele, niente stiramenti.
- Muovendo il mouse SULLA mappa (senza premere) il pallino segue il mouse agganciandosi al punto più vicino della traiettoria, e sincronizza cursore/pallini su tutti i grafici. Muovendo il mouse sui grafici resta la logica di prima.
- Trascinamento = pan, rotellina/pinch = zoom (invariati).
- NOTA: nella demo le coordinate lat/lon sono sintetiche, quindi la forma della pista NON è realistica e i giri si sovrappongono; con i dati reali di iRacing la forma sarà fedele e le traiettorie distinguibili.
- ✅ Nessun terminale, solo index.html.

## v10 — Freccia partenza/direzione sulla mappa
- Sul giro principale, all'inizio del giro (lapdist≈0 ≈ linea del traguardo), freccia bianca che indica il verso di percorrenza + trattino perpendicolare tipo linea di partenza. Una sola freccia anche in confronto multi-giro. Segue zoom/pan.
- ✅ Nessun terminale, solo index.html.

## v11 — Zoom orizzontale dei grafici telemetria
- Finestra di zoom UNICA condivisa da tutti i grafici (asse distanza, solo orizzontale).
- Rotellina sul grafico = zoom in/out verso il puntatore; zoom-out fino al minimo torna al giro intero.
- Doppio click = inizia la selezione di un tratto (linea rossa + area evidenziata), click successivo = chiude la selezione e zooma su quel tratto (estremi ordinati automaticamente).
- Tasto destro = annulla la selezione in corso; se non c'è selezione, azzera lo zoom. Menu contestuale del browser disattivato sui grafici.
- Finestra minima 2% del giro. Cursore, readout, confronto multi-giro, mappa e pallini restano sincronizzati sul tratto zoomato.
- Migliorata la correttezza del disegno: le linee ora sono posizionate per lapdist (non per indice), quindi cursore e valori del readout coincidono esattamente e i confronti tra giri sono allineati correttamente.
- ✅ Nessun terminale, solo index.html.

## v12 — Tappa 2+3: giri pubblici e team
Modello di visibilità (verificato lato server, non solo nell'interfaccia):
X vede i dati di Y se: X è Y, oppure Y è pubblico, oppure X e Y condividono un team.
- Nome pilota (nome account iRacing) richiesto al primo accesso, modificabile in Impostazioni.
- Interruttore pubblico unico: tutto o niente, SETUP INCLUSI. Default spento.
- Team: chi crea è manager; ricerca team + richiesta di ingresso; il manager approva/rifiuta/rimuove.
  Si può stare in più team. Uscendo, la visibilità cessa subito. Se esce il manager il ruolo passa
  al membro più anziano; se resta vuoto il team viene sciolto.
- I compagni vedono tutto anche a pubblico spento.
- Sezione Piloti: ricerca e archivio altrui in SOLA LETTURA (nessuna cancellazione).
- Confronto giri a 3 passi: pilota -> sessione -> giro (max 3 giri, come prima).
- Nota tecnica: le piste sono per-utente, quindi il confronto tra piloti abbina le piste PER NOME.
- Nessuna email viene mai esposta ad altri utenti.
- Nuove tabelle (idempotenti, si creano al riavvio del server): profiles, teams, team_members, team_requests.
- ✅ Nessun terminale: agent.py invariato. Carica su GitHub: server.py, schema.sql, index.html.

## v13 — Correzione giro fantasma dopo il rientro rapido ai box
Sintomo: usando il tasto rapido per tornare ai box, nel nuovo stint compariva un giro in più
con il tempo IDENTICO all'ultimo giro valido precedente (3 giri + sosta + 3 giri -> 4 giri nel 2° stint).
Causa: iRacing non aggiorna LapLastLapTime per il giro troncato dal teletrasporto; l'agente
rileggeva il valore vecchio e lo registrava come giro nuovo, per giunta nel nuovo stint.
Correzioni in agent.py:
 - un giro NON viene registrato se il tempo è identico (entro 2 ms) all'ultimo già inviato:
   un giro reale ha sempre un tempo nuovo;
 - rilevamento del rientro non guidato ai box (IsOnTrack se disponibile, altrimenti salto di
   LapDistPct MENTRE si è ai box: il vincolo sui box evita falsi positivi in pista): il giro
   troncato e la sua telemetria vengono scartati;
 - il nuovo stint non nasce più all'uscita dai box ma viene applicato al primo giro completato
   dopo: il giro della sosta resta nello stint in cui era iniziato.
Correzioni server.py + schema.sql:
 - indice unico parziale uq_laps_session_lapuid (session_id, client_lap_uid) e ON CONFLICT DO NOTHING
   sull'inserimento dei giri: prima era l'unico INSERT senza protezione anti-duplicati;
 - lo schema rimuove automaticamente eventuali doppioni esatti già presenti (tiene la riga più vecchia).
⚠️ agent.py modificato -> ricostruire l'exe:
   python -m PyInstaller --onefile --name iRacingLive agent.py
Su GitHub caricare: agent.py, server.py, schema.sql

## v14 — Schede Telemetria/Grafici + limite di aderenza
- La vista telemetria ora ha due schede: "Telemetria" (invariata) e "Grafici" (nuova, estensibile).
- CORREZIONE UNITÀ: iRacing fornisce le accelerazioni in m/s², venivano mostrate grezze ma etichettate "G"
  (il G verticale a riposo segnava 9.81 invece di 1.00). Ora sono divise per 9,81: G veri ovunque.
- Nuovo grafico "Limite di aderenza": X = velocità km/h, Y = |G laterale|, un punto per campione,
  filtrato per soglia angolo sterzo (isola le curve). Curva di inviluppo (percentile 95 per intervalli
  di velocità, lisciata) = limite di aderenza; linea di tendenza tratteggiata (regressione lineare).
- Alimentato dai giri in confronto, ognuno col proprio colore.
- Controlli: visibilità punti 0-100% con preset Pulito 10 / Dettagli 40 / Panoramica 100,
  soglia sterzo con conteggio "N/M punti visibili", interruttori tendenza e inviluppo.
  Impostazioni ricordate nel browser (chiave plotPrefs).
- Cambiando scheda non si ricaricano i dati né si perde lo stato (giri, colori, canali, zoom).
- ✅ Nessun terminale, solo index.html.
