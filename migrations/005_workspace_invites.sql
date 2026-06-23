CREATE TABLE IF NOT EXISTS workspace_invites (
    id                  INTEGER PRIMARY KEY,
    org_id              INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    email               TEXT NOT NULL,
    invitee_name        TEXT,
    role                TEXT NOT NULL CHECK (role IN ('admin','member')),
    token               TEXT UNIQUE NOT NULL,
    invited_by_user_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    accepted_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','accepted','revoked')),
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    accepted_at         TEXT,
    revoked_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_workspace_invites_org_status
    ON workspace_invites(org_id, status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_workspace_invites_pending_email
    ON workspace_invites(org_id, email) WHERE status='pending';
