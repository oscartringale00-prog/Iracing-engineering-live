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
