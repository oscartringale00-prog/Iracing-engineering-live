-- Schema v3: dati legati all'uid Firebase (user_id), agente autenticato via device_key

CREATE TABLE IF NOT EXISTS devices (
    id          SERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL,
    device_key  TEXT NOT NULL UNIQUE,
    name        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS link_codes (
    code        TEXT PRIMARY KEY,
    device_key  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cars (
    id      SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    name    TEXT NOT NULL,
    UNIQUE (user_id, name)
);

CREATE TABLE IF NOT EXISTS tracks (
    id      SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    name    TEXT NOT NULL,
    UNIQUE (user_id, name)
);

CREATE TABLE IF NOT EXISTS sessions (
    id            SERIAL PRIMARY KEY,
    user_id       TEXT NOT NULL,
    client_uid    TEXT NOT NULL,
    car_id        INTEGER NOT NULL REFERENCES cars(id),
    track_id      INTEGER NOT NULL REFERENCES tracks(id),
    session_type  TEXT NOT NULL,
    session_num   INTEGER NOT NULL DEFAULT 0,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    air_temp      DOUBLE PRECISION,
    track_temp    DOUBLE PRECISION,
    humidity      DOUBLE PRECISION,
    wind_vel      DOUBLE PRECISION,
    wind_dir      DOUBLE PRECISION,
    track_usage   TEXT,
    UNIQUE (user_id, client_uid)
);

CREATE TABLE IF NOT EXISTS stints (
    id          SERIAL PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    client_uid  TEXT NOT NULL,
    setup_name  TEXT,
    setup       JSONB,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, client_uid)
);

CREATE TABLE IF NOT EXISTS laps (
    id          SERIAL PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    stint_id    INTEGER REFERENCES stints(id) ON DELETE SET NULL,
    lap         INTEGER NOT NULL,
    time_s      DOUBLE PRECISION NOT NULL,
    air_temp    DOUBLE PRECISION,
    track_temp  DOUBLE PRECISION,
    humidity    DOUBLE PRECISION,
    wind_vel    DOUBLE PRECISION,
    wind_dir    DOUBLE PRECISION,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sessions_user  ON sessions (user_id);
CREATE INDEX IF NOT EXISTS idx_stints_session ON stints (session_id);
CREATE INDEX IF NOT EXISTS idx_laps_session   ON laps (session_id);
CREATE INDEX IF NOT EXISTS idx_devices_user   ON devices (user_id);

-- Completa la cascata di cancellazione: cancellando un'auto o una pista, le sue
-- sessioni (e a catena stint/giri/telemetria) devono sparire. Sicuro da rieseguire
-- e sicuro anche su un database già esistente: sostituisce il vincolo, non tocca i dati.
ALTER TABLE sessions DROP CONSTRAINT IF EXISTS sessions_car_id_fkey;
ALTER TABLE sessions ADD CONSTRAINT sessions_car_id_fkey
    FOREIGN KEY (car_id) REFERENCES cars(id) ON DELETE CASCADE;
ALTER TABLE sessions DROP CONSTRAINT IF EXISTS sessions_track_id_fkey;
ALTER TABLE sessions ADD CONSTRAINT sessions_track_id_fkey
    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE;

ALTER TABLE laps ADD COLUMN IF NOT EXISTS client_lap_uid TEXT;

CREATE TABLE IF NOT EXISTS lap_telemetry (
    id          SERIAL PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    lap_uid     TEXT NOT NULL,
    speed       DOUBLE PRECISION[],
    throttle    DOUBLE PRECISION[],
    brake       DOUBLE PRECISION[],
    clutch      DOUBLE PRECISION[],
    steer       DOUBLE PRECISION[],
    gear        INTEGER[],
    rpm         DOUBLE PRECISION[],
    lapdist     DOUBLE PRECISION[],
    lataccel    DOUBLE PRECISION[],
    lonaccel    DOUBLE PRECISION[],
    lat         DOUBLE PRECISION[],
    lon         DOUBLE PRECISION[],
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, lap_uid)
);
CREATE INDEX IF NOT EXISTS idx_telemetry_session ON lap_telemetry (session_id);

ALTER TABLE lap_telemetry ADD COLUMN IF NOT EXISTS vertaccel DOUBLE PRECISION[];
ALTER TABLE lap_telemetry ADD COLUMN IF NOT EXISTS rh_lf     DOUBLE PRECISION[];
ALTER TABLE lap_telemetry ADD COLUMN IF NOT EXISTS rh_rf     DOUBLE PRECISION[];
ALTER TABLE lap_telemetry ADD COLUMN IF NOT EXISTS rh_lr     DOUBLE PRECISION[];
ALTER TABLE lap_telemetry ADD COLUMN IF NOT EXISTS rh_rr     DOUBLE PRECISION[];
ALTER TABLE lap_telemetry ADD COLUMN IF NOT EXISTS shock_lf  DOUBLE PRECISION[];
ALTER TABLE lap_telemetry ADD COLUMN IF NOT EXISTS shock_rf  DOUBLE PRECISION[];
ALTER TABLE lap_telemetry ADD COLUMN IF NOT EXISTS shock_lr  DOUBLE PRECISION[];
ALTER TABLE lap_telemetry ADD COLUMN IF NOT EXISTS shock_rr  DOUBLE PRECISION[];

-- ============ v12: profili pilota, team, visibilità ============

CREATE TABLE IF NOT EXISTS profiles (
    user_id     TEXT PRIMARY KEY,
    pilot_name  TEXT,
    is_public   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_profiles_name ON profiles (lower(pilot_name));
CREATE INDEX IF NOT EXISTS idx_profiles_public ON profiles (is_public);

CREATE TABLE IF NOT EXISTS teams (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    manager_id  TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_teams_name ON teams (lower(name));

CREATE TABLE IF NOT EXISTS team_members (
    team_id    INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id    TEXT NOT NULL,
    joined_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_team_members_user ON team_members (user_id);

CREATE TABLE IF NOT EXISTS team_requests (
    id           SERIAL PRIMARY KEY,
    team_id      INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | rejected
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (team_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_team_requests_team ON team_requests (team_id, status);

-- indici utili alle query che ora attraversano più utenti
CREATE INDEX IF NOT EXISTS idx_tracks_name ON tracks (lower(name));
CREATE INDEX IF NOT EXISTS idx_sessions_track ON sessions (track_id);
CREATE INDEX IF NOT EXISTS idx_cars_user ON cars (user_id);

-- ============ v13: protezione contro i giri duplicati ============
-- Prima rimuove eventuali doppioni esatti già presenti (stessa sessione + stesso identificativo
-- di giro generato dall'agente): tiene la riga più vecchia e scarta le copie.
DELETE FROM laps a USING laps b
 WHERE a.client_lap_uid IS NOT NULL
   AND a.client_lap_uid = b.client_lap_uid
   AND a.session_id = b.session_id
   AND a.id > b.id;

-- Poi impedisce che se ne creino di nuovi. Indice parziale: i giri storici senza
-- identificativo (client_lap_uid nullo) restano validi e non vengono toccati.
CREATE UNIQUE INDEX IF NOT EXISTS uq_laps_session_lapuid
    ON laps (session_id, client_lap_uid) WHERE client_lap_uid IS NOT NULL;
