-- Phase 2 of the SaaS strip: drop the identity / collaboration tables.
-- audit_events references users(id); rebuild it without that FK first so the
-- users table can be dropped without leaving a dangling foreign key.
CREATE TABLE audit_events_new (
    id            INTEGER PRIMARY KEY,
    org_id        INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    actor_user_id INTEGER,
    action        TEXT NOT NULL,
    entity_type   TEXT,
    entity_id     INTEGER,
    summary       TEXT NOT NULL,
    details_json  TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO audit_events_new
    SELECT id, org_id, actor_user_id, action, entity_type, entity_id,
           summary, details_json, created_at
    FROM audit_events;
DROP TABLE audit_events;
ALTER TABLE audit_events_new RENAME TO audit_events;
CREATE INDEX IF NOT EXISTS idx_audit_events_org_created
    ON audit_events(org_id, created_at);

DROP TABLE workspace_invites;
DROP TABLE organization_members;
DROP TABLE users;
