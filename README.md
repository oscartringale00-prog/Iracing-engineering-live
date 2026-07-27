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

## v15 — CORREZIONE CRITICA: la telemetria non arrivava mai dal PC
Sintomo: sul sito vero i giri venivano salvati ma NON erano cliccabili (nessuna telemetria).
Causa: nell'agente `ws.settimeout(0.005)` (5 ms) serviva a non bloccare la lettura di iRacing,
ma in websocket-client quel timeout vale ANCHE per l'invio. I messaggi piccoli (giro) entrano
subito nel buffer di sistema e partono; la telemetria di un giro (Road America 1:51 = ~6700
campioni x 21 canali = ~1 MB) non ci entra: l'invio procede a tratti e dopo 5 ms va in timeout.
Riprodotto in laboratorio riducendo il buffer di invio del socket (condizioni di rete reale):
timeout 5 ms -> eccezione, 0 righe salvate; timeout adeguato -> salvataggio corretto.
NON si manifestava in locale (loopback assorbe 1 MB istantaneamente) né in demo (~300 campioni).
Correzioni in agent.py:
 - invio spostato su un thread dedicato con coda (classe Sender): il ciclo principale non si
   ferma mai, quindi non si perdono più i primi istanti del giro successivo (verificato: 1135
   cicli di lettura eseguiti durante l'invio di 1 MB);
 - timeout del socket ampio (45 s) adeguato all'invio; la lettura resta non bloccante usando
   select (funzione drain) invece del timeout minuscolo;
 - se la coda si intasa si scarta la telemetria (pesante) ma mai i giri, con avviso a schermo;
 - decimali ridotti dove non tolgono informazione (-7% sul messaggio, meno spazio nel database):
   lat/lon a 6 decimali (~11 cm), velocità 1, sterzo 3, accelerazioni 2, altezze 1.
   Invariati: lapdist (serve all'allineamento dei giri) e corse ammortizzatori.
⚠️ agent.py modificato -> ricostruire l'exe. Su GitHub: agent.py.
NOTA: i giri già registrati senza telemetria restano senza (i dati non sono mai arrivati al server).

## v16 — Pulsante «Indietro» e briciole cliccabili
Problema: nelle schermate profonde solo la prima briciola era cliccabile (auto, pista e sessione
erano testo morto), quindi dalla telemetria si poteva solo tornare all'inizio di tutto.
Causa: gli endpoint restituivano i nomi ma non gli identificativi, quindi il sito non sapeva
dove rimandare.
- Nuovo pulsante «← Indietro» in alto a sinistra, sopra le briciole. Sale di UN livello nella
  gerarchia (non usa la cronologia), quindi funziona anche da collegamento diretto o dopo un
  ricaricamento. Assente nelle schermate di primo livello.
- Tutte le briciole intermedie sono ora collegamenti.
- Gerarchia: giro -> sessione -> auto -> elenco auto. Sugli archivi altrui si resta nell'archivio
  del pilota fino a #/pilots.
- server.py: /api/sessions/{id}/laps ora restituisce car_id, track_id e owner;
  /api/laps/{id}/telemetry restituisce session_id, car_id e owner. owner è lo stesso identificativo
  già esposto da /api/pilots e solo per dati già visibili; nessuna email esposta.
- ✅ Nessun terminale: index.html + server.py, agente invariato.

## v17 — Giri troncati, numerazione sbagliata e righe sui grafici
Segnalazione: dopo due giri, il secondo risultava troncato (solo la coda, schiacciata a sinistra),
numerato male ("come se i giri non fossero allineati"), e sui grafici comparivano righe orizzontali.
Causa 1 (introdotta dalla correzione del giro fantasma): quando una transizione veniva RIFIUTATA
perché il tempo non era cambiato, l'agente si comportava comunque come se un giro fosse finito:
azzerava il buffer telemetria (buttando via il giro vero ancora in corso) e faceva avanzare il
contatore interno (etichettando poi il giro vero col numero sbagliato).
Causa 2: il campione letto nel tick del cambio giro veniva aggiunto al buffer del giro che si stava
chiudendo, pur appartenendo già al giro nuovo: sul grafico la linea tornava dal bordo destro al
bordo sinistro attraversando tutto il tracciato (le "righe che disturbano il segnale").
Correzioni in agent.py:
 - introdotto buf_lap_num: il numero di giro a cui appartiene DAVVERO il buffer, usato nell'evento
   "lap" al posto del contatore attuale (che può essere avanzato da transizioni fasulle);
 - una transizione fasulla allinea solo il contatore: buffer, lap_uid e numero di giro restano
   intatti, quindi il giro vero prosegue e viene registrato completo;
 - il campione del tick di cambio giro finisce nel buffer NUOVO, non in coda a quello vecchio.
Verifica (simulazione): caso reale -> prima giri 6 e 8 con 301 e 60 campioni; dopo giri 6 e 7 con
300 e 300 campioni e nessun salto all'indietro. Sequenza normale e rientro rapido ai box invariati.
⚠️ agent.py modificato -> ricostruire l'exe. Su GitHub: agent.py.

## v17b — Diagnosi automatica dei canali all'avvio
All'aggancio con iRacing l'agente stampa una volta sola quali canali NON vengono forniti.
Se mancano lat/lon avvisa esplicitamente che la mappa non potra' essere disegnata.
Serve a distinguere subito un problema di dati mancanti da un problema del sito.

## v18 — FASE 1: strato di comunicazione affidabile
Sostituita la classe Sender + funzione drain con la classe Link, che gestisce la connessione con
DUE thread dedicati (uno invia, uno riceve). Il ciclo principale non tocca più il socket.
Difetti corretti:
 1. la connessione precedente non veniva mai chiusa (ws.close() non compariva nel file): ora si
    chiude sempre prima di riconnettere e all'uscita;
 2. il thread di invio non veniva atteso: ora close() attende entrambi i thread (max 2s ciascuno);
 3. drain() modificava il timeout dello stesso socket usato dall'invio, potendo interrompere a metà
    una telemetria da 1 MB: la lettura ora avviene su un thread dedicato che non tocca il timeout;
 4. la coda non aveva un tetto complessivo: ora QUEUE_SOFT=8 (stop alla telemetria) e QUEUE_HARD=40
    (stop a tutto), con i giri che hanno la precedenza sulla telemetria.
Verifiche: 5 riconnessioni consecutive -> 0 thread residui (conteggio tornato al valore iniziale);
telemetria da 6700 campioni su rete lenta con lettura concorrente -> arrivata integra, 1527
iterazioni del ciclo principale durante l'invio; rete assente -> coda ferma a 39/40, 36 giri
accettati contro 4 telemetrie.
⚠️ agent.py modificato -> ricostruire l'exe. Su GitHub: agent.py.

## v19 — FASE 2: correttezza dei dati
A) Primo giro buono dopo un rientro rapido ai box: non viene più perso. Il contrassegno di giro
   invalidato restava attivo oltre il giro interrotto e scartava anche quello successivo.
   Ora la prima transizione dopo un teletrasporto chiude il giro morto e riparte pulita.
   Verifica: prima veniva registrato solo il giro 1; ora vengono registrati giro 1 e giro 3 (300
   campioni ciascuno).
B) Tetto ai campioni per giro (MAX_SAMPLES = 9000, ~1,4 MB). Oltre la soglia i campioni vengono
   diradati in modo UNIFORME lungo tutto il giro, conservando primo e ultimo e mantenendo tutti i
   canali della stessa lunghezza. Verifica: 20000 -> 9000, canali allineati, estremi conservati.
C) Doppioni fra sessioni diverse: l'identificativo di giro generato dall'agente è un uuid, quindi
   il vincolo di unicità è stato portato da (sessione, giro) a GLOBALE su lap_telemetry.lap_uid e
   laps.client_lap_uid. Verifica sul database: stesso giro inserito sotto due sessioni diverse ->
   il secondo inserimento viene rifiutato, resta 1 riga. La migrazione in schema.sql è idempotente
   e ripulisce prima gli eventuali doppioni esistenti tenendo la riga più vecchia.
D) Modalità demo realistica: DEMO_SAMPLES = 6600 campioni per giro (come un giro vero) invece di
   300. Era proprio lo scarto fra demo e realtà ad aver lasciato passare i bug precedenti.
⚠️ agent.py modificato -> ricostruire l'exe. Su GitHub: agent.py, server.py, schema.sql.

## v20 — FASE 3: registro su file e sicurezza
A) Registro su file in %LOCALAPPDATA%\iRacingTelemetry\iracing-telemetry.log (1 MB, 2 ricicli).
   Contiene data/ora, eventi ed errori col dettaglio tecnico. La chiave del dispositivo viene
   sempre oscurata. Se la cartella non è scrivibile l'agente prosegue senza registro.
   Il percorso viene mostrato all'avvio, così il cliente sa cosa inviare per l'assistenza.
   Verifiche: chiave in chiaro = False; file entro il tetto (1656 byte su 2000);
   cartella protetta -> nessun errore; 33.000 scritture/secondo (l'agente ne fa poche per giro).
B) AUTH_DEBUG non è più attivabile in produzione: viene accettato solo se non ci sono variabili
   RAILWAY_* e se il database è locale. Resta utilizzabile dai collaudi locali.
⚠️ agent.py modificato -> ricostruire l'exe. Su GitHub: agent.py, server.py.

## v21 — PARTE A: dati per la strategia (carburante e gomme)
Aggiunta la raccolta di carburante, usura e temperature gomme, che mancavano completamente.
SCELTA TECNICA IMPORTANTE: NON sono canali ad alta frequenza ma valori registrati UNA VOLTA
PER GIRO (STRATEGY_VARS). Usura e temperature cambiano lentamente e la strategia ragiona per
giro: campionarle 60 volte al secondo avrebbe aggiunto ~40% al peso della telemetria senza
aggiungere informazione. Così costano ~88 byte per giro (+0,008%).
- agent.py: legge FuelLevel a ogni tick (per calcolare il consumo del giro) e usura/temperature
  a fine giro; la diagnosi all'avvio segnala se il carburante non è disponibile.
- schema.sql: 11 colonne idempotenti su laps (fuel, fuel_start, fuel_used, wear_*, temp_*).
- server.py: salvataggio e restituzione nella vista tempi.
- index.html: colonna ⛽ con il consumo per giro, mostrata solo se il dato c'è (i giri storici
  ne sono privi e non causano errori).
Verifica end-to-end: consumo misurato 2,763 L/giro, residuo e usura progressivi corretti.
⚠️ agent.py modificato -> ricostruire l'exe. Su GitHub: agent.py, server.py, schema.sql, index.html.

## v22 — PARTE C: calcolatore di strategia (pagina Pitwall)
Nuovo pulsante "🏁 Pitwall" in alto a destra nella schermata delle auto -> pagina #/pitwall.
MOTORE (server, GET /api/strategy/params?car_id=&track_id=): ricava i parametri dai giri
realmente percorsi dall'utente su quella pista con quell'auto, invece di farli digitare a mano
come fanno gli altri calcolatori.
 - consumo: mediana dei consumi misurati;
 - degrado + effetto peso: regressione multipla tempo = base + k*carburante + deg*eta_gomme.
   NOTA: dentro un singolo stint carburante ed eta sono quasi perfettamente correlati (uno scende
   mentre l'altro sale), quindi da UNO stint non si possono separare. Servono piu stint partiti da
   carichi diversi. Se i dati non bastano il sistema NON inventa: usa un effetto peso tipico
   (0,035 s/L) e lo dichiara nella fonte.
 - perdita ai box: mediana del tempo extra sui giri di entrata/uscita.
 - i giri anomali (oltre il 7% sopra la mediana) vengono scartati.
 - ogni parametro dichiara la propria FONTE (misurato su N giri / stimato / da inserire).
CALCOLATORE (frontend): confronta da 0 a 4 soste, mostra tempo totale, differenza, lunghezza
degli stint, benzina per stint e i giri consigliati per le soste. Segnala le strategie non
fattibili per capienza del serbatoio. Avvisa che vale in condizioni pulite.
VERIFICHE:
 - inseriti 35 giri con parametri NOTI (base 100 s, peso 0,035 s/L, degrado 0,040 s/giro,
   consumo 2,80 L): il motore li ha ritrovati con errore massimo 1,7%.
 - sensibilita del modello corretta: degrado 0 -> 0 soste; 0,12 -> 2 soste; 0,5 -> 3 soste.
   Sosta da 10 s -> 3 soste; da 40 s -> 1 sosta.
 - con archivio insufficiente (3 giri) l'endpoint restituisce None invece di inventare valori.
✅ Nessun terminale: server.py + index.html. Agente invariato.

## v23 — PARTE B: Pitwall dal vivo
La pagina Pitwall ha ora due schede: "Dal vivo" e "Calcolatore".
ARCHITETTURA (il punto delicato): accanto al flusso esistente (un pacchetto a fine giro) l'agente
invia una FOTOGRAFIA della gara una volta al secondo (LIVE_EVERY_S), con posizione, giro, stato
box e tempi di TUTTE le auto. Il pacchetto è piccolo e la coda di invio lo scarta per primo se la
rete è congestionata: una fotografia vecchia è inutile, mentre un giro non va mai perso.
STATO IN MEMORIA, MAI NEL DATABASE: a 1 fotografia al secondo salvarla significherebbe ~3600
righe l'ora per pilota. Il server la tiene in un dizionario con scadenza a 30 s.
VERIFICATO: 300 fotografie con 20 auto -> 0 KB di crescita del database, 0 righe aggiunte.
Aggiornamento del browser: interrogazione ogni secondo (invece di WebSocket) perché riusa
l'autenticazione esistente, non introduce logica di riconnessione e 1 s di ritardo è adeguato.
COSA MOSTRA: mappa del tracciato con tutte le auto (la sagoma viene ricavata dal GPS di un giro
già in archivio, perché iRacing dà la posizione degli avversari come frazione di giro e non come
coordinate; se il GPS non è disponibile si ripiega su una barra con la pista srotolata),
classifica dal vivo con distacchi, storia delle soste DEDOTTA dai passaggi in corsia box,
e i dati della propria auto (benzina, usura e temperature) che iRacing non fornisce per gli altri.
TEAM: un compagno può seguire dal proprio browser la sessione di chi guida (l'ingegnere da casa),
con i permessi verificati sul server. VERIFICATO: un estraneo riceve 404 e non vede nessuno.
✅ Nessun terminale per il sito, ma ⚠️ agent.py è cambiato -> ricostruire l'exe.
Su GitHub: agent.py, server.py, index.html.

## v24 — Due correzioni importanti segnalate dall'uso reale
1) GIRO REGISTRATO SBAGLIATO (grave). Sintomo: giro completato, tempo corretto, ma la telemetria
   conteneva solo il tratto DOPO il traguardo (361 campioni invece di ~2700) e con il numero di
   giro sbagliato. Causa: iRacing pubblica LapLastLapTime con qualche istante di RITARDO rispetto
   allo scatto del contatore giri. Il codice pretendeva il tempo nell'istante esatto della
   transizione: non trovandolo, scartava la telemetria del giro appena percorso e poi attribuiva
   il tempo, quando arrivava, al giro successivo.
   Correzione: separati il CONFINE del giro (dato dal contatore, immediato e affidabile) dalla
   sua VALIDITÀ (il tempo, che arriva dopo). Alla transizione il buffer viene messo "in attesa";
   appena arriva un tempo nuovo il giro viene spedito con i suoi dati; se entro 5 secondi non
   arriva nessun tempo nuovo, il giro in attesa viene scartato (era un rientro/reset).
   Verificato: prima 0 giri o frammenti; ora giro completo (2700 campioni) col numero giusto.
   Regressione superata su: sequenza normale, rientro rapido ai box, tempo che non arriva mai.
2) CANALI MANCANTI. "lonaccel" mancava per un mio errore di nome: iRacing la chiama LongAccel,
   non LonAccel. Aggiunto il supporto a nomi alternativi per canale (si prova il primo
   disponibile), applicato anche alle altezze di marcia.
3) MAPPA SENZA GPS. Sul sistema dell'utente iRacing non fornisce Lat/Lon, quindi la mappa non si
   poteva disegnare. Ora, quando le coordinate mancano, il tracciato viene RICOSTRUITO dai dati di
   guida: in curva l'accelerazione laterale è legata al raggio, quindi integrandola lungo il giro
   si ottiene la traiettoria; la lunghezza della pista si stima da velocità media × tempo sul giro.
   Verificato su forme note: errore 0,1% sul diametro di un cerchio, e distingue correttamente un
   ovale da un cerchio. La forma viene chiusa distribuendo l'errore di deriva.
⚠️ agent.py modificato -> ricostruire l'exe. Su GitHub: agent.py, index.html.

## v25 — Assi con lo zero, sterzo più fine, diagnostica completa
1) ASSI VERTICALI. Velocità, acceleratore, freno, frizione, marcia, RPM, altezze e ammortizzatori
   includono SEMPRE lo zero. Forze G e sterzo hanno lo zero AL CENTRO (scala simmetrica), con una
   linea di riferimento sullo zero. Motivo pratico: su un ovale acceleratore (100%), freno (0) e
   marcia sono costanti, e con la scala automatica venivano disegnati come righe piatte sul bordo,
   dando l'impressione che il grafico fosse rotto.
2) STERZO. Riportato a 4 decimali: a 3 (scelti per alleggerire i dati) la risoluzione era 0,06° e
   sugli ovali, dove si sterza di pochi gradi, i gradini erano visibili a occhio. Era questo a dare
   l'impressione di "pochi campioni". Il risparmio era irrisorio.
3) FREQUENZA nel registro: ogni giro riporta ora quanti campioni al secondo sono stati raccolti,
   così la domanda "è troppo bassa?" ha una risposta misurata invece che a sensazione.
4) YawRate aggiunto ai canali: se disponibile, la forma del tracciato viene ricostruita dalla
   rotazione MISURATA invece che dedotta dall'accelerazione laterale (più preciso).
5) ELENCO COMPLETO delle variabili iRacing scritto nel registro all'avvio, con evidenza di quelle
   utili a posizione e assetto. Serve a stabilire con certezza se le coordinate GPS esistono su
   quel PC e con quale nome, invece di tirare a indovinare.
⚠️ agent.py modificato -> ricostruire l'exe. Su GitHub: agent.py, index.html.

## v26 — Il registro ha risolto tre questioni aperte
Analisi dell'elenco completo (354 variabili) inviato dall'utente:
1) FREQUENZA DIMEZZATA (era 30 Hz invece di 60). Causa: _wait_tick faceva time.sleep(1/60) MA
   freeze_var_buffer_latest() attende GIÀ l'aggiornamento di iRacing: le due attese si sommavano
   (16 ms + 16 ms = 33 ms) e si perdeva un aggiornamento su due. Rimossa l'attesa superflua
   (resta solo se l'SDK non espone l'evento di sincronizzazione). Misurato: da 30,0 a 60,5 al secondo.
   Nota: in questa versione di pyirsdk wait_for_data NON esiste, quindi si cadeva sempre nel ramo
   con la pausa.
2) COORDINATE GPS: NON esistono su questo sistema (né Lat né Lon fra le 354 variabili). Esistono
   però VelocityX, VelocityY e YawNorth: la traiettoria si ricava ora integrando la velocità
   ruotata secondo la direzione MISURATA rispetto al nord, che non accumula errore di rotazione.
   Verificato su un ovale noto 900x400 m: errore 0,0% e 0,1%.
3) ALTEZZE DI MARCIA: non esistono (nessun *rideHeight). Sostituite con HFshockDefl/HRshockDefl
   (corse degli ammortizzatori centrali), che descrivono l'abbassamento della piattaforma.
Inoltre: il contesto di sessione (auto/pista/tipo) viene riletto al massimo una volta al secondo
invece che a ogni tick, e i nomi delle variabili si risolvono una volta sola.
⚠️ agent.py modificato -> ricostruire l'exe. Su GitHub: agent.py, server.py, schema.sql, index.html.

## v28 — Alta frequenza 360 Hz sulle sospensioni
LIMITE ACCERTATO: pedali, sterzo, velocità, marcia, RPM e posizione sul giro esistono SOLO a
60 Hz (nessuna variante _ST). Con la correzione precedente siamo già al massimo possibile.
DISPONIBILE a 360 Hz: 22 variabili con suffisso _ST (pacchetti di 6 sotto-campioni per fotogramma).
SCELTA: raccogliamo a 360 Hz SOLO le sospensioni (4 velocità ammortizzatore, 4 corse, 2 heave).
Accelerazioni e rotazione restano a 60 Hz: includerle avrebbe aggiunto il 40% di dati senza
informazione utile, perché per l'analisi di guida 60 Hz sono abbondanti. Sulle sospensioni invece
le oscillazioni stanno fra 3 e 15 Hz e a 60 Hz se ne colgono 4-5 campioni per ciclo: troppo pochi,
soprattutto per l'istogramma delle velocità ammortizzatore.
ARCHITETTURA: i canali a 360 Hz hanno un PROPRIO asse delle distanze (lapdist_hf), perché
LapDistPct esiste solo a 60 Hz e i 6 sotto-campioni vengono distribuiti per interpolazione.
Nel frontend ogni serie porta con sé il proprio asse: disegno, cursore e riquadro dei valori
funzionano su entrambe le frequenze.
INTERRUTTORE: 'alta_frequenza = si/no' in config.ini (predefinito: acceso), segnalato all'avvio.
COSTO MISURATO (Daytona, giro di 45,3 s): 279 KB spento -> 1673 KB acceso, cioè 6 volte.
Su una pista lunga come Monza si arriva a ~4 MB per giro. Tetto di sicurezza dedicato a 30000
campioni con diradamento uniforme.
VERIFICHE: 360,1 Hz misurati, rapporto 6,0x, 10/10 canali presenti, assi allineati e coerenti,
comportamento identico a prima con l'interruttore spento.
⚠️ agent.py modificato -> ricostruire l'exe. Su GitHub: agent.py, server.py, schema.sql, index.html.

## v29 — Mappa specchiata: convenzione degli angoli
Segnalazione: la mappa di Road America risultava SPECCHIATA (il tracciato girava a sinistra
invece che a destra, curva 1 dalla parte sbagliata).
Causa: YawNorth è una BUSSOLA (l'angolo cresce in senso ORARIO partendo da nord), mentre la
ricostruzione lo trattava come angolo matematico (antiorario partendo da est). È esattamente il
cambio di verso che produce un'immagine riflessa.
Verifica del difetto: con la formula vecchia un'auto diretta a nord si spostava a DESTRA e un
tracciato percorso in senso orario veniva disegnato antiorario.
Correzione: direzione di marcia = (sin, cos) sugli assi (est, nord). Stessa convenzione applicata
anche ai metodi di ripiego (YawRate e accelerazione laterale), con il segno invertito perché una
rotazione antioraria corrisponde a una bussola decrescente.
Verifica: su un rettangolo percorso in senso orario (nord, est, sud, ovest) il disegno risulta
orario, il primo tratto sale e il secondo va a destra.
✅ Nessun terminale: solo index.html.

## v30 — PITWALL rifatto: FASE 1 (torre di controllo + Overview & Timing)
Rifatta la pagina Pitwall sulla base della specifica "muretto box", ma DENTRO il progetto
esistente invece che come progetto separato: si riusa l'agente, il server con account/team/
visibilita', l'archivio e il deploy gia' funzionanti.
TORRE DI CONTROLLO (barra fissa in alto, sempre visibile):
 - minimappa con tutte le auto colorate PER CLASSE (gare multiclasse), la propria in bianco,
   chi e' ai box in trasparenza, i piloti selezionati con contorno evidenziato;
 - GHOST CAR: se mi fermassi adesso, in che posizione rientrerei e fra quali piloti. Calcolata
   confrontando la distanza percorsa con quella che gli altri accumulano durante la sosta,
   usando la perdita ai box misurata dall'archivio;
 - meteo compatto: aria, asfalto, stato pista, pioggia in corso, vento con direzione, tempo/giri.
SCHEDA "OVERVIEW & TIMING": posizione assoluta e di classe, pilota con pallino colore classe e
numero, categoria, giro, ultimo, migliore, PASSO MEDIO recente (mediana dei giri validi recenti,
scartando gli anomali), distacco dal leader e dal precedente convertiti in secondi, giri nello
stint con numero di soste, tempo ai box (in corso o dell'ultima sosta).
Spuntando due piloti vengono evidenziati sulla minimappa.
SCHEDA "METEO": solo dati reali. Dichiarato esplicitamente che iRacing NON fornisce previsioni
(verificato: nessuna variabile di previsione fra le 354), quindi niente radar a 2 ore inventato.
Lato agente: la fotografia ora include classe, posizione di classe, distacco calcolato da iRacing,
giri completati, mescola, e meteo esteso (pioggia, bagnato, cielo, umidita').
Lato server: calcolo dei valori derivati che iRacing non da' (giri nello stint, passo medio,
durata delle soste), sempre in memoria e mai nel database.
Restano da fare: schede Strategia predittiva e Analisi giri (fasi successive).
⚠️ agent.py modificato -> ricostruire l'exe. Su GitHub: agent.py, server.py, index.html.

## v31 — PITWALL FASE 2: strategia predittiva dal vivo
Nuova scheda "Strategia" dentro il Pitwall dal vivo, che unisce i dati della gara in corso ai
parametri MISURATI dall'archivio (consumo, degrado, effetto peso, perdita ai box). I parametri
vengono cercati automaticamente all'inizio della sessione abbinando auto e pista per nome.
BENZINA: quantita' a bordo, consumo, autonomia in giri, quanto serve per finire, avanzo o
deficit. Se manca benzina calcola il LIFT & COAST: quanti litri al giro risparmiare e in che
percentuale del consumo, per tagliare il traguardo con il margine di sicurezza impostato
(evita i tagli di potenza dei motori GTP a secco).
MODELLO GOMME: malus da gomme fredde sul giro di uscita, dimezzato al secondo giro, quasi nullo
al terzo, poi solo degrado lineare. Verificato: 102,60 -> 102,04 -> 101,72 -> 101,52 s.
SOSTA: rifornimento in secondi/litro + cambio gomme, con interruttore CONTEMPORANEO o
SEQUENZIALE. Verificato su 30 L e 4 gomme: 95 s contro 108 s.
CONFRONTO A/B/C: solo benzina, benzina + 2 gomme, benzina + 4 gomme. Per ogni scenario cerca il
giro di sosta ottimale e mostra durata sosta, tempo totale e differenza dal migliore.
Verifiche di sensibilita': con gomme consumate (18 giri) il cambio conviene; con degrado alto
(0,25 s/giro) il vantaggio del cambio sale a 110 s; con rifornimenti lunghi il cambio gomme
diventa gratuito perche' avviene durante il rifornimento.
Preferenze del pannello ricordate nel browser.
✅ Nessun terminale: solo index.html. Agente e server invariati rispetto alla Fase 1.

## v32 — PITWALL FASE 3: analisi giri e stint
Nuova scheda "Analisi giri" nel Pitwall dal vivo.
 - CONFRONTO fino a 5 piloti contemporaneamente, scelti da chip cliccabili con il numero di giri
   disponibili e il pallino del colore di classe.
 - GRAFICO DEL PASSO disegnato a mano su canvas: una linea per pilota, griglia con i tempi,
   legenda con numero di giri, media e giro migliore nella finestra selezionata.
 - FINESTRA DI ANALISI: giro di inizio e di fine, piu' pulsanti che isolano automaticamente i
   singoli STINT ricavati dalle soste dedotte, e un pulsante "Tutto".
 - FILTRO GIRI PULITI: scarta i giri oltre il 2% sopra la mediana del pilota (out-lap, rientri,
   traffico). La prima versione usava il 5% e non scartava NULLA: verificato che al 2% vengono
   correttamente esclusi giro di uscita e rientri, e la media scende di 0,67 s facendo emergere
   il passo reale.
 - TABELLA cronologica dei tempi con il delta fra i primi due piloti selezionati, evidenziando
   il migliore di ogni giro.
 - Cliccando il nome di un pilota nella classifica si salta direttamente alla sua analisi.
Corretto anche uno stint fantasma che compariva quando l'ultima sosta coincideva con l'ultimo giro.
Lato server: lo storico dei giri passa da 10 tempi sciolti a 300 coppie giro+tempo per auto,
necessarie all'isolamento degli stint. Sempre in memoria, mai nel database.
✅ Nessun terminale: index.html + server.py. Agente invariato.

## v33 — Barra non piu' appiccicata + riquadri ridimensionabili
1) LA BARRA IN ALTO non e' piu' "sticky": era un mio fraintendimento. Resta comunque visibile
   passando da una scheda all'altra (sta fuori dal contenitore delle schede), ma ora scorre
   normalmente quando si scende nella pagina. Stessa cosa per la mappa nella vista telemetria.
2) RIQUADRI RIDIMENSIONABILI: minimappa, classifica, grafico del passo, tabella dei giri, mappa
   telemetria e colonna dei grafici si allargano trascinando l'angolo in basso a destra.
   Le misure vengono ricordate nel browser (chiave "dimensioni") e riapplicate al rientro.
   I grafici disegnati su canvas NON si limitano a essere tagliati: rileggono l'altezza del
   riquadro e si ridisegnano alla nuova dimensione.
   Il salvataggio e' ritardato di 400 ms per non scrivere a ogni pixel durante il trascinamento.
✅ Nessun terminale: solo index.html.

## v34 — Il Pitwall usa la mappa vera del circuito
Problema: nel Pitwall compariva la barra orizzontale invece della pista. Causa: l'endpoint
/api/track-outline cercava le coordinate GPS, che su questo sistema iRacing NON fornisce; non
trovandole rispondeva "non disponibile" e la pagina ripiegava sulla barra. Nel frattempo la vista
telemetria disegnava gia' la mappa corretta ricostruendola da velocita' e direzione.
Correzione: l'endpoint non cerca piu' il GPS ma restituisce i canali disponibili (velocita',
direzione, rotazione, distanza sul giro) del giro PIU' VELOCE in archivio su quella pista, e la
forma viene ricostruita dal browser con la STESSA funzione gia' usata nella telemetria (spostata
a livello globale): una sola matematica, gia' verificata, invece di due che rischiano di divergere.
Verifica: sagoma chiusa di 1528x1528 m, auto collocate correttamente a 0/25/50/75% del giro.
NOTA: i canali di velocita' e direzione sono stati aggiunti da poco; i giri registrati prima non
li hanno, quindi per quelle piste la sagoma comparira' dopo aver rifatto un giro.
Corretto anche un difetto nella DEMO (solo anteprima, non il sito): mancavano i riferimenti ai
dati di telemetria, persi in una rigenerazione precedente.
✅ Nessun terminale: index.html + server.py.

## v35 — Classifica per intero e filtro categorie
1) TABELLA DEI TEMPI: niente piu' barra di scorrimento interna, cresce per tutta la sua lunghezza
   e si scorre con la pagina. Resta comunque ridimensionabile trascinando l'angolo, per chi
   preferisce tenerla compatta: se non la si tocca, resta intera.
2) FILTRO CATEGORIE: menu a tendina con una spunta per ogni categoria presente in pista.
   L'etichetta usa il modello di auto piu' diffuso nella classe (iRacing identifica le classi con
   numeri interni, quindi si legge "GT3" e non "classe 4"). La scelta viene ricordata nel browser.
   LA PROPRIA AUTO RESTA SEMPRE VISIBILE anche nascondendo la sua categoria.
3) DISTACCHI DAL LEADER DI CLASSE, non dal leader assoluto: in una gara multiclasse la corsa che
   conta e' quella di categoria. Verificato che ogni classe abbia il proprio leader come
   riferimento. La colonna "Precedente" segue invece l'ordine mostrato, quindi si adatta al filtro
   ed e' utile anche per il traffico.
   Aggiunta la colonna con la posizione di classe accanto a quella assoluta.
✅ Nessun terminale: solo index.html.

## v36 — Tabelle configurabili: colonne, ordine, ordinamento, pausa
Un unico meccanismo (oggetto TAB) usato sia dalla classifica dal vivo sia dalla tabella dei giri.
 - SCEGLIERE LE COLONNE: menu a tendina con una spunta per colonna, stesso stile del menu Canali.
 - RIORDINARLE: trascinando l'intestazione (computer) oppure con le frecce su/giu' nel menu
   (funzionano anche da telefono, dove il trascinamento e' scomodo). Piu' un pulsante
   "Ripristina ordine" che riporta tutto com'era.
 - ORDINARE: click sull'intestazione (crescente, decrescente, nessuno). I valori mancanti
   restano SEMPRE in fondo: nella prima versione in ordine decrescente finivano in cima, corretto.
 - Tutto memorizzato nel browser, separatamente per ogni tabella.
PROBLEMA RISOLTO sull'ordinamento della classifica dal vivo: la colonna "Precedente" mostrava il
distacco da chi precede NELL'ORDINE VISUALIZZATO, quindi ordinando per un'altra colonna avrebbe
indicato un'auto lontanissima in pista. Ora le righe si costruiscono sempre sull'ordine di gara e
il distacco resta riferito a chi precede IN CLASSIFICA, qualunque ordinamento sia attivo.
PAUSA: pulsante che congela l'aggiornamento della classifica, utile perche' la tabella si
ridisegna ogni secondo e ordinandola le righe si rimescolerebbero sotto le dita.
Inoltre la tabella dei giri cresce ora per intero come quella della classifica.
✅ Nessun terminale: solo index.html.
