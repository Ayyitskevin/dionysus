-- Idempotency for the Mise argus-pack hook: one campaign per (org, argus_run_id),
-- plus the correlation_id Mise sends so a retry returns the same draft.
ALTER TABLE campaigns ADD COLUMN argus_run_id INTEGER;
ALTER TABLE campaigns ADD COLUMN correlation_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_campaigns_argus_run
    ON campaigns(org_id, argus_run_id)
    WHERE argus_run_id IS NOT NULL;
