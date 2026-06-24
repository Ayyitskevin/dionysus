ALTER TABLE jobs ADD COLUMN idempotency_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_active_idempotency
    ON jobs(idempotency_key)
    WHERE idempotency_key IS NOT NULL
      AND status IN ('queued','running');
