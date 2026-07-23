/* ====== DEMO: dati finti generati nel browser, nessun server ====== */
const DEMO = (() => {
  const setup = {
    TiresAero: {
      TireType: { TireType: "Dry" },
      LeftFront: { StartingPressure: "152 kPa", LastHotPressure: "173 kPa", TreadRemaining: "98%" },
      RightFront: { StartingPressure: "152 kPa", LastHotPressure: "169 kPa", TreadRemaining: "98%" },
      LeftRear: { StartingPressure: "152 kPa", LastHotPressure: "174 kPa" },
      RightRear: { StartingPressure: "152 kPa", LastHotPressure: "170 kPa" },
      AeroSettings: { RearWingAngle: "17 deg" },
      AeroCalculator: { FrontRHAtSpeed: "15.0 mm", RearRHAtSpeed: "25.0 mm", DownforceBalance: "46.04%", LD: "3.754" }
    },
    Chassis: {
      Front: { HeaveSpring: "1", HeavePerchOffset: "-16.0 mm", ArbSize: "A", ArbBlades: "1", ToeIn: "-0.9 mm" },
      Rear: { HeaveSpring: "4", HeavePerchOffset: "-107.0 mm", ArbSize: "B", ArbBlades: "3", FuelLevel: "44.5 L" },
      LeftFront: { CornerWeight: "2663 N", RideHeight: "30.2 mm", Camber: "-2.9 deg" },
      RightFront: { CornerWeight: "2663 N", RideHeight: "30.2 mm", Camber: "-2.9 deg" },
      LeftRear: { CornerWeight: "2934 N", RideHeight: "47.0 mm", Camber: "-1.8 deg", ToeIn: "+0.1 mm" },
      RightRear: { CornerWeight: "2934 N", RideHeight: "47.0 mm", Camber: "-1.8 deg", ToeIn: "+0.1 mm" }
    },
    Dampers: {
      LeftFrontDamper: { LsCompDamping: "16 clicks", HsCompDamping: "10 clicks", LsRbdDamping: "17 clicks", HsRbdDamping: "7 clicks" },
      RightFrontDamper: { LsCompDamping: "16 clicks", HsCompDamping: "10 clicks", LsRbdDamping: "17 clicks", HsRbdDamping: "7 clicks" },
      LeftRearDamper: { LsCompDamping: "13 clicks", HsCompDamping: "28 clicks", LsRbdDamping: "14 clicks", HsRbdDamping: "30 clicks" },
      RightRearDamper: { LsCompDamping: "13 clicks", HsCompDamping: "28 clicks", LsRbdDamping: "14 clicks", HsRbdDamping: "30 clicks" }
    },
    Systems: {
      GearRatios: { GearStack: "Short", SpeedInFirst: "121.7 Km/h", SpeedInSixth: "291.0 Km/h" },
      BrakeSpec: { PadCompound: "Low", FrontMasterCyl: "17.8 mm", BrakePressureBias: "48.50%" },
      TractionControl: { TractionControlGain: "4 (TC2)", TractionControlSlip: "5 (TC1)" },
      Fuel: { FuelLevel: "44.5 L", FuelTarget: "2.00 L" }
    }
  };

  // Telemetria sintetica; "style" cambia leggermente guida e traiettoria fra piloti
  function makeTelemetry(seed, style){
    style = style || 0;
    const n = 800, ch = {speed:[],throttle:[],brake:[],clutch:[],steer:[],gear:[],rpm:[],lapdist:[],
      lataccel:[],lonaccel:[],vertaccel:[],rh_lf:[],rh_rf:[],rh_lr:[],rh_rr:[],
      shock_lf:[],shock_rf:[],shock_lr:[],shock_rr:[],lat:[],lon:[]};
    const rnd = a => Math.sin(seed*12.9898 + a*4.1414)*0.5;
    for (let i=0;i<n;i++){
      const d = i/n, a = d*2*Math.PI;
      const curve = Math.max(0, Math.min(1,
        0.5*Math.abs(Math.sin(3*a)) + 0.4*Math.abs(Math.sin(5*a+1)) + 0.15*Math.abs(Math.sin(8*a))));
      const speed = 30 + (75-30)*(1-curve) + 3*rnd(a) + style*2;
      const thr = curve < 0.35 ? 1 : Math.max(0, 1-curve*(1.6 - style*0.1));
      const brk = curve > 0.55 ? Math.min(1,(curve-0.4)*1.8) : 0;
      const steer = (Math.sin(3*a)+0.5*Math.sin(5*a+1)) * curve * (2.2 + style*0.15);
      const gear = Math.max(2, Math.min(6, Math.round(2 + (speed-30)/9)));
      const rpm = 4000 + 3500*thr - 300*brk;
      // traiettoria leggermente diversa per pilota: si vede il confronto sulla mappa
      const off = style*0.0006;
      const lat = 45.615 + (0.010+off)*Math.sin(a) + 0.004*Math.sin(2*a+0.6);
      const lon = 9.281 + (0.014+off)*Math.cos(a) + 0.003*Math.cos(3*a);
      // accelerazioni realistiche in m/s² (come le fornisce iRacing): fino a ~25 m/s² = ~2,5 G
      const gripMax = 22 + style*1.6;                    // limite di aderenza, diverso per pilota
      const vFactor = Math.min(1, 0.55 + speed/150);     // più veloci = più carico aerodinamico
      const latG = Math.sign(steer||1) * gripMax * vFactor * Math.min(1, curve*1.35) * (0.9 + 0.1*rnd(a));
      const lonG = (thr - brk) * 11;
      ch.speed.push(+speed.toFixed(2)); ch.throttle.push(+thr.toFixed(3)); ch.brake.push(+brk.toFixed(3));
      ch.clutch.push(0); ch.steer.push(+steer.toFixed(4)); ch.gear.push(gear); ch.rpm.push(Math.round(rpm));
      ch.lapdist.push(+d.toFixed(5));
      ch.lataccel.push(+latG.toFixed(3)); ch.lonaccel.push(+lonG.toFixed(3));
      ch.vertaccel.push(+(9.81 + 1.2*Math.sin(6*a) + 0.8*brk).toFixed(3));
      ch.rh_lf.push(+(30 - 6*brk + 2*Math.sin(3*a)).toFixed(2));
      ch.rh_rf.push(+(30 - 6*brk + 2*Math.sin(3*a+0.3)).toFixed(2));
      ch.rh_lr.push(+(47 - 5*thr + 2*Math.sin(3*a+1)).toFixed(2));
      ch.rh_rr.push(+(47 - 5*thr + 2*Math.sin(3*a+1.3)).toFixed(2));
      ch.shock_lf.push(+(0.017 + 0.006*Math.sin(3*a)).toFixed(4));
      ch.shock_rf.push(+(0.017 + 0.006*Math.sin(3*a+0.3)).toFixed(4));
      ch.shock_lr.push(+(0.019 + 0.006*Math.sin(3*a+1)).toFixed(4));
      ch.shock_rr.push(+(0.019 + 0.006*Math.sin(3*a+1.3)).toFixed(4));
      ch.lat.push(+lat.toFixed(7)); ch.lon.push(+lon.toFixed(7));
    }
    return ch;
  }

  // ---- Profilo mio ----
  let profile = { pilot_name: "Oscar Tringale", is_public: false };

  // ---- Archivi: me (p1) + altri piloti ----
  const mkSessions = (baseId, style) => ([
    { id: baseId+0, session_type:"Practice", started_at:"2026-07-20T14:12:00Z", air_temp:24.6, track_temp:38.2,
      humidity:0.52, wind_vel:3.2, wind_dir:1.1, track_usage:"moderately low usage",
      stints:[{ id: baseId+900, setup_name:"Baseline Monza", setup, laps:[
        {id:baseId+1, lap:1, time_s:107.324+style}, {id:baseId+2, lap:2, time_s:106.981+style}, {id:baseId+3, lap:3, time_s:107.556+style}]},
        { id: baseId+901, setup_name:"Low Downforce", setup, laps:[
        {id:baseId+4, lap:4, time_s:106.412+style}, {id:baseId+5, lap:5, time_s:107.033+style}]}]},
    { id: baseId+10, session_type:"Race", started_at:"2026-07-20T15:40:00Z", air_temp:27.1, track_temp:41.0,
      humidity:0.44, wind_vel:5.0, wind_dir:2.3, track_usage:"high usage",
      stints:[{ id: baseId+910, setup_name:"Race Setup", setup, laps:[
        {id:baseId+6, lap:1, time_s:108.002+style}, {id:baseId+7, lap:2, time_s:106.744+style}]}]}
  ]);

  const pilots = {
    me:  { id:"me", name:"I miei giri", style:0, sessions: mkSessions(100, 0) },
    p2:  { id:"p2", name:"Marco Rossi", teammate:true,  public:false, style:1, sessions: mkSessions(200, -0.6) },
    p3:  { id:"p3", name:"Luca Bianchi", teammate:false, public:true, style:2, sessions: mkSessions(300, 0.9) },
  };

  const lapIndex = {};   // lap_id -> {pilot, lap, session}
  Object.values(pilots).forEach(p => p.sessions.forEach(s => s.stints.forEach(st => st.laps.forEach(l => {
    lapIndex[l.id] = { pilot:p, lap:l, session:s, tel: makeTelemetry(l.id, p.style) };
  }))));

  // ---- Team ----
  let teams = [
    { id:1, name:"Scuderia Demo", is_manager:true, members:[
        {id:"me", name:"Oscar Tringale", is_manager:true, is_me:true},
        {id:"p2", name:"Marco Rossi", is_manager:false, is_me:false}],
      requests:[{id:11, pilot:"Andrea Verdi", created_at:"2026-07-21T09:00:00Z"}] },
    { id:2, name:"Team Endurance IT", is_manager:false, members:[], requests:[], notMine:true, manager:"Giulia Neri", memberCount:5 }
  ];

  const visible = p => p.id==="me" || p.public || p.teammate;

  function parse(path){
    const [route, qs] = path.split("?");
    const q = {};
    (qs||"").split("&").filter(Boolean).forEach(kv => { const [k,v]=kv.split("="); q[k]=decodeURIComponent(v||""); });
    return [route, q];
  }
  const pilotOf = q => pilots[q.pilot] || pilots.me;

  function route(path, opts={}){
    const method = (opts.method||"GET").toUpperCase();
    const [p, q] = parse(path);
    let m;

    // ----- profilo -----
    if (p === "profile" && method === "GET") return {...profile};
    if (p === "profile" && method === "PUT"){
      const b = JSON.parse(opts.body||"{}");
      profile = { pilot_name: b.pilot_name, is_public: !!b.is_public };
      return {ok:true};
    }
    // ----- piloti visibili -----
    if (p === "pilots"){
      const needle = (q.q||"").toLowerCase();
      return Object.values(pilots).filter(x => x.id!=="me" && visible(x)
             && (!needle || x.name.toLowerCase().includes(needle)))
        .map(x => ({id:x.id, name:x.name, public:!!x.public, teammate:!!x.teammate}));
    }
    // ----- team -----
    if (p === "teams" && method === "GET")
      return teams.filter(t=>!t.notMine).map(t=>({id:t.id, name:t.name, is_manager:t.is_manager,
        members:t.members.length, pending:t.requests.length}));
    if (p === "teams" && method === "POST"){
      const b = JSON.parse(opts.body||"{}");
      const t = {id: teams.length+10, name:b.name, is_manager:true,
                 members:[{id:"me", name:profile.pilot_name||"Io", is_manager:true, is_me:true}], requests:[]};
      teams.push(t); return {id:t.id, name:t.name};
    }
    if (p === "teams/search"){
      const needle = (q.q||"").toLowerCase();
      return teams.filter(t=>!needle || t.name.toLowerCase().includes(needle)).map(t=>({
        id:t.id, name:t.name, manager: t.notMine ? t.manager : (profile.pilot_name||"Io"),
        members: t.notMine ? t.memberCount : t.members.length,
        is_member: !t.notMine, pending:false}));
    }
    if ((m = p.match(/^teams\/(\d+)\/request$/)) && method === "POST"){
      const t = teams.find(x=>x.id==m[1]); if(!t) throw new Error("404");
      alert("Demo: richiesta inviata al manager del team."); return {ok:true};
    }
    if ((m = p.match(/^teams\/(\d+)\/requests$/)) && method === "GET"){
      const t = teams.find(x=>x.id==m[1]); if(!t||!t.is_manager) throw new Error("404");
      return t.requests;
    }
    if ((m = p.match(/^teams\/(\d+)\/requests\/(\d+)$/)) && method === "POST"){
      const t = teams.find(x=>x.id==m[1]); const b = JSON.parse(opts.body||"{}");
      const i = t.requests.findIndex(r=>r.id==m[2]); if(i<0) throw new Error("404");
      const r = t.requests[i]; t.requests.splice(i,1);
      if (b.action === "approve") t.members.push({id:"p9", name:r.pilot, is_manager:false, is_me:false});
      return {ok:true};
    }
    if ((m = p.match(/^teams\/(\d+)\/members$/))){
      const t = teams.find(x=>x.id==m[1]); if(!t) throw new Error("404"); return t.members;
    }
    if ((m = p.match(/^teams\/(\d+)\/members\/(.+)$/)) && method === "DELETE"){
      const t = teams.find(x=>x.id==m[1]);
      t.members = t.members.filter(x=>x.id !== decodeURIComponent(m[2]));
      if (decodeURIComponent(m[2])==="p2") pilots.p2.teammate = false;
      return {ok:true};
    }
    if ((m = p.match(/^teams\/(\d+)\/leave$/)) && method === "POST"){
      teams = teams.filter(x=>x.id!=m[1]);
      pilots.p2.teammate = false;   // uscendo dal team non vedo più Marco
      return {ok:true};
    }

    // ----- archivio (mio o di un altro pilota) -----
    const P = pilotOf(q);
    if (!visible(P)) throw new Error("404");   // stessa regola del server
    if (p === "cars"){
      const laps = P.sessions.reduce((a,s)=>a+s.stints.reduce((b,st)=>b+st.laps.length,0),0);
      return laps ? [{id:1, name:"Mazda MX-5 Cup", sessions:P.sessions.length,
                      last_used:P.sessions[P.sessions.length-1].started_at}] : [];
    }
    if (p === "devices")
      return [{id:1, name:"PC di gara (demo)", created_at:"2026-07-20T14:00:00Z", last_seen:"2026-07-20T15:59:00Z"}];
    if (p.match(/^cars\/\d+\/tracks$/))
      return P.sessions.length ? [{id:1, name:"Autodromo Nazionale Monza", sessions:P.sessions.length,
                                   last_used:P.sessions[P.sessions.length-1].started_at}] : [];
    if (p.match(/^cars\/\d+\/tracks\/\d+\/sessions$/))
      return P.sessions.map(s => {
        const laps = s.stints.reduce((a,st)=>a+st.laps.length,0);
        const best = Math.min(...s.stints.flatMap(st=>st.laps.map(l=>l.time_s)));
        return {id:s.id, session_type:s.session_type, started_at:s.started_at, laps, best,
                air_temp:s.air_temp, track_temp:s.track_temp, humidity:s.humidity,
                wind_vel:s.wind_vel, wind_dir:s.wind_dir, track_usage:s.track_usage};
      });
    if ((m = p.match(/^sessions\/(\d+)\/laps$/))){
      let owner=null, s=null;
      Object.values(pilots).forEach(pl => pl.sessions.forEach(x => { if (x.id==m[1]){ owner=pl; s=x; } }));
      if (!s || !visible(owner)) throw new Error("404");
      return { session:{session_type:s.session_type, started_at:s.started_at, car:"Mazda MX-5 Cup",
                        track:"Autodromo Nazionale Monza", pilot: owner.id==="me"?null:owner.name,
                        air_temp:s.air_temp, track_temp:s.track_temp, humidity:s.humidity,
                        wind_vel:s.wind_vel, wind_dir:s.wind_dir, track_usage:s.track_usage},
        stints: s.stints.map(st=>({id:st.id, setup_name:st.setup_name, setup:st.setup,
          laps: st.laps.map(l=>({id:l.id, lap:l.lap, time_s:l.time_s, air_temp:s.air_temp,
            track_temp:s.track_temp, humidity:s.humidity, wind_vel:s.wind_vel, wind_dir:s.wind_dir,
            has_telemetry:true}))}))};
    }
    if ((m = p.match(/^laps\/(\d+)\/telemetry$/))){
      const e = lapIndex[m[1]];
      if (!e || !visible(e.pilot)) throw new Error("404");
      return { lap:{lap:e.lap.lap, time_s:e.lap.time_s, car:"Mazda MX-5 Cup",
                    track:"Autodromo Nazionale Monza", track_id:1,
                    pilot: e.pilot.id==="me"?null:e.pilot.name},
               channels: e.tel };
    }
    // ----- confronto -----
    if (p.match(/^tracks\/\d+\/pilots$/))
      return Object.values(pilots).filter(visible).map(x=>({
        id:x.id, name:x.id==="me"?"I miei giri":x.name, is_me:x.id==="me",
        laps: x.sessions.reduce((a,s)=>a+s.stints.reduce((b,st)=>b+st.laps.length,0),0)}));
    if (p.match(/^tracks\/\d+\/comparable$/)){
      const T = pilots[q.pilot] || pilots.me;
      if (!visible(T)) throw new Error("404");
      return T.sessions.map(s=>({session_id:s.id, session_type:s.session_type, started_at:s.started_at,
        car:"Mazda MX-5 Cup", laps: s.stints.flatMap(st=>st.laps.map(l=>({lap_id:l.id, lap:l.lap, time_s:l.time_s})))}));
    }
    return [];
  }
  return { route };
})();

async function api(p, opts={}){
  await new Promise(r=>setTimeout(r,100));
  return DEMO.route(p, opts);
}
