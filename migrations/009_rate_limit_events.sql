CREATE TABLE IF NOT EXISTS rate_limit_events (
    id          INTEGER PRIMARY KEY,
    action      TEXT NOT NULL,
    identity    TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_rate_limit_events_lookup
    ON rate_limit_events(action, identity, created_at);
