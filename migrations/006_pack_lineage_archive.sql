ALTER TABLE content_packs ADD COLUMN source_pack_id INTEGER;
ALTER TABLE content_packs ADD COLUMN revision_note TEXT;
ALTER TABLE content_packs ADD COLUMN archived_at TEXT;

CREATE INDEX IF NOT EXISTS idx_packs_source
    ON content_packs(source_pack_id);
CREATE INDEX IF NOT EXISTS idx_packs_org_archive_status
    ON content_packs(org_id, archived_at, status, created_at);
