-- Track whether a human has edited a draft's body. Machine generation never
-- overwrites a human-edited body; Mise reads this flag to honor the guard.
ALTER TABLE content_packs ADD COLUMN human_edited INTEGER NOT NULL DEFAULT 0;
