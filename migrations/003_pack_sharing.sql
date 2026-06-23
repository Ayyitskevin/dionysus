ALTER TABLE content_packs ADD COLUMN share_token TEXT;
ALTER TABLE content_packs ADD COLUMN approved_at TEXT;
ALTER TABLE content_packs ADD COLUMN exported_at TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_content_packs_share_token
    ON content_packs(share_token) WHERE share_token IS NOT NULL;
