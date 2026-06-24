ALTER TABLE jobs ADD COLUMN org_id INTEGER;
ALTER TABLE jobs ADD COLUMN result_pack_id INTEGER;
ALTER TABLE jobs ADD COLUMN source_pack_id INTEGER;
ALTER TABLE jobs ADD COLUMN completed_at TEXT;

CREATE INDEX IF NOT EXISTS idx_jobs_org_status
    ON jobs(org_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_result_pack
    ON jobs(result_pack_id);
