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
