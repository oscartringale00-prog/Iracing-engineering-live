CREATE TABLE IF NOT EXISTS cars (
    id      SERIAL PRIMARY KEY,
    token   TEXT NOT NULL,
    name    TEXT NOT NULL,
    UNIQUE (token, name)
);

CREATE TABLE IF NOT EXISTS tracks (
    id      SERIAL PRIMARY KEY,
    token   TEXT NOT NULL,
    name    TEXT NOT NULL,
    UNIQUE (token, name)
);

CREATE TABLE IF NOT EXISTS sessions (
    id            SERIAL PRIMARY KEY,
    token         TEXT NOT NULL,
    client_uid    TEXT NOT NULL,
    car_id        INTEGER NOT NULL REFERENCES cars(id),
    track_id      INTEGER NOT NULL REFERENCES tracks(id),
    session_type  TEXT NOT NULL,
    session_num   INTEGER NOT NULL DEFAULT 0,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (token, client_uid)
);

CREATE TABLE IF NOT EXISTS laps (
    id          SERIAL PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    lap         INTEGER NOT NULL,
    time_s      DOUBLE PRECISION NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions (token);
CREATE INDEX IF NOT EXISTS idx_laps_session   ON laps (session_id);

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS air_temp    DOUBLE PRECISION;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS track_temp  DOUBLE PRECISION;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS humidity    DOUBLE PRECISION;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS wind_vel    DOUBLE PRECISION;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS wind_dir    DOUBLE PRECISION;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS track_usage TEXT;
