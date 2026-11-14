CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    external_id TEXT NOT NULL,
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','processing','retrying','succeeded','failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_attempt REAL NOT NULL,
    lease_until REAL,
    lease_token TEXT,
    result TEXT,
    received_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(source, external_id)
);
CREATE INDEX IF NOT EXISTS events_queue ON events(status, next_attempt);
CREATE INDEX IF NOT EXISTS events_received ON events(received_at DESC);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES events(id),
    number INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    message TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES events(id),
    message TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (
    client TEXT PRIMARY KEY,
    failures INTEGER NOT NULL,
    reset_at REAL NOT NULL
);
