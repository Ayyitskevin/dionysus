CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS organization_members (
    org_id      INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        TEXT NOT NULL DEFAULT 'owner'
                CHECK (role IN ('owner','admin','member')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (org_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_org_members_user ON organization_members(user_id);

CREATE TABLE IF NOT EXISTS subscriptions (
    id                     INTEGER PRIMARY KEY,
    org_id                 INTEGER NOT NULL UNIQUE REFERENCES organizations(id) ON DELETE CASCADE,
    plan                   TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'trialing'
                           CHECK (status IN ('none','trialing','active','past_due','canceled')),
    stripe_customer_id     TEXT,
    stripe_subscription_id TEXT,
    current_period_end     TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at             TEXT
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);
