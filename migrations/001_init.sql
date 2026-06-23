CREATE TABLE IF NOT EXISTS organizations (
    id            INTEGER PRIMARY KEY,
    slug          TEXT UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL,
    audience      TEXT NOT NULL CHECK (audience IN ('restaurant','photographer')),
    company       TEXT,
    plan          TEXT NOT NULL DEFAULT 'starter',
    market        TEXT,
    service_mix   TEXT,
    brand_voice   TEXT,
    access_token  TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS menu_items (
    id          INTEGER PRIMARY KEY,
    org_id      INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    category    TEXT,
    notes       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_menu_org ON menu_items(org_id);

CREATE TABLE IF NOT EXISTS campaigns (
    id           INTEGER PRIMARY KEY,
    org_id       INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    goal         TEXT,
    launch_date  TEXT,
    status       TEXT NOT NULL DEFAULT 'draft'
                 CHECK (status IN ('draft','generated','approved','archived')),
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_campaigns_org ON campaigns(org_id, status);

CREATE TABLE IF NOT EXISTS content_recipes (
    id                INTEGER PRIMARY KEY,
    slug              TEXT UNIQUE NOT NULL,
    name              TEXT NOT NULL,
    audience          TEXT NOT NULL DEFAULT 'both'
                      CHECK (audience IN ('both','restaurant','photographer')),
    channels          TEXT NOT NULL DEFAULT '[]',
    deliverable_note  TEXT NOT NULL DEFAULT '',
    price_anchor_cents INTEGER NOT NULL DEFAULT 0,
    active            INTEGER NOT NULL DEFAULT 1,
    sort              INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_recipes_active ON content_recipes(active, sort);

CREATE TABLE IF NOT EXISTS content_packs (
    id                 INTEGER PRIMARY KEY,
    org_id              INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    campaign_id         INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    recipe_id           INTEGER NOT NULL REFERENCES content_recipes(id) ON DELETE RESTRICT,
    title               TEXT NOT NULL,
    body_json           TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft','approved','exported')),
    ai_model            TEXT,
    ai_draft_original   TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_packs_org ON content_packs(org_id, created_at);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued','running','done','failed')),
    attempts    INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

INSERT OR IGNORE INTO content_recipes
    (slug, name, audience, channels, deliverable_note, price_anchor_cents, sort)
VALUES
    ('menu-launch', 'Menu Launch Pack', 'restaurant',
     '["instagram","website","email","delivery_apps"]',
     'captions, shot priorities, hero copy, and delivery-app angle', 14900, 10),
    ('monthly-retainer', 'Monthly Retainer Pack', 'both',
     '["instagram","reels","newsletter","client_portal"]',
     'four-week content rhythm from one food photography session', 9900, 20),
    ('delivery-app-refresh', 'Delivery App Refresh', 'restaurant',
     '["doordash","ubereats","google_business_profile"]',
     'menu hero direction plus conversion copy for ordering platforms', 7900, 30),
    ('photographer-upsell', 'Photographer Upsell Kit', 'photographer',
     '["proposal","shot_list","client_email","license_prompt"]',
     'pre-shoot plan, usage-rights prompt, and retainer upsell copy', 9900, 40),
    ('press-seasonal', 'Seasonal Press Kit', 'both',
     '["press","website","social_organic","email"]',
     'editorial angles, image needs, captions, and usage notes', 12900, 50);
