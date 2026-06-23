"""Seed workspaces for production demos and end-to-end bridge checks."""

import os

from . import billing, config, db, jobs, packs as pack_utils, security


DEMO = {
    "slug": "blue-plate",
    "name": "Avery Demo",
    "email": "demo+blue-plate@platekit.local",
    "company": "Blue Plate",
    "audience": "restaurant",
    "plan": "restaurant_growth",
    "market": "Asheville",
    "service_mix": "dine-in, delivery, seasonal private events",
    "brand_voice": "warm, chef-led, practical, neighborhood-premium",
    "campaign_title": "Blue Plate June content pack",
    "campaign_goal": "fill weekday reservations and refresh delivery-app hero copy",
    "launch_date": "2026-07-01",
    "recipe_slug": "monthly-retainer",
    "items": [
        ("Spring agnolotti", "priority dish", "peas, ricotta, lemon, high-margin dinner hero"),
        ("Charred peach salad", "seasonal special", "lunch-friendly, bright overhead crop"),
        ("Golden hour dining room", "room", "hospitality frame for press, web, and Google profile"),
    ],
}


def seed_demo_workspace() -> dict:
    """Create or refresh the Blue Plate demo loop.

    The command is intentionally idempotent: it updates the workspace profile,
    fills in missing inputs, generates one pack if needed, then approves and
    shares the newest pack so Mise can read it immediately.
    """
    db.migrate()
    password = os.environ.get("DIONYSUS_DEMO_PASSWORD", security.new_token(24))
    user = db.one("SELECT * FROM users WHERE email=?", (DEMO["email"],))
    with db.tx() as con:
        if user:
            user_id = user["id"]
        else:
            cur = con.execute("""INSERT INTO users (email, name, password_hash)
                                 VALUES (?,?,?)""",
                              (DEMO["email"], DEMO["name"],
                               security.hash_password(password)))
            user_id = cur.lastrowid

        org = db.one("SELECT * FROM organizations WHERE slug=?", (DEMO["slug"],))
        if org:
            org_id = org["id"]
            con.execute("""UPDATE organizations
                           SET name=?, email=?, audience=?, company=?, plan=?,
                               market=?, service_mix=?, brand_voice=?
                           WHERE id=?""",
                        (DEMO["name"], DEMO["email"], DEMO["audience"],
                         DEMO["company"], DEMO["plan"], DEMO["market"],
                         DEMO["service_mix"], DEMO["brand_voice"], org_id))
        else:
            cur = con.execute("""INSERT INTO organizations
                                 (slug, name, email, audience, company, plan,
                                  access_token, market, service_mix, brand_voice)
                                 VALUES (?,?,?,?,?,?,?,?,?,?)""",
                              (DEMO["slug"], DEMO["name"], DEMO["email"],
                               DEMO["audience"], DEMO["company"], DEMO["plan"],
                               security.new_token(), DEMO["market"],
                               DEMO["service_mix"], DEMO["brand_voice"]))
            org_id = cur.lastrowid

        con.execute("""INSERT OR IGNORE INTO organization_members
                       (org_id, user_id, role) VALUES (?,?, 'owner')""",
                    (org_id, user_id))

        for name, category, notes in DEMO["items"]:
            exists = con.execute(
                "SELECT id FROM menu_items WHERE org_id=? AND name=?",
                (org_id, name)).fetchone()
            if not exists:
                con.execute("""INSERT INTO menu_items (org_id, name, category, notes)
                               VALUES (?,?,?,?)""",
                            (org_id, name, category, notes))

        campaign = con.execute(
            "SELECT * FROM campaigns WHERE org_id=? AND title=?",
            (org_id, DEMO["campaign_title"])).fetchone()
        if campaign:
            campaign_id = campaign["id"]
            con.execute("""UPDATE campaigns SET goal=?, launch_date=?
                           WHERE id=?""",
                        (DEMO["campaign_goal"], DEMO["launch_date"], campaign_id))
        else:
            cur = con.execute("""INSERT INTO campaigns
                                 (org_id, title, goal, launch_date)
                                 VALUES (?,?,?,?)""",
                              (org_id, DEMO["campaign_title"],
                               DEMO["campaign_goal"], DEMO["launch_date"]))
            campaign_id = cur.lastrowid

    billing.sync_trial_subscription(org_id, DEMO["plan"])
    recipe = db.one("SELECT * FROM content_recipes WHERE slug=?", (DEMO["recipe_slug"],))
    if not recipe:
        raise RuntimeError("monthly-retainer recipe is missing")
    pack = db.one("""SELECT * FROM content_packs
                     WHERE org_id=? AND campaign_id=?
                     ORDER BY created_at DESC LIMIT 1""",
                  (org_id, campaign_id))
    if not pack:
        jobs.enqueue_generate(campaign_id, recipe["id"])
        pack = db.one("""SELECT * FROM content_packs
                         WHERE org_id=? AND campaign_id=?
                         ORDER BY created_at DESC LIMIT 1""",
                      (org_id, campaign_id))
    if not pack:
        raise RuntimeError("demo pack was not generated")
    db.run("""UPDATE content_packs SET status='approved',
              approved_at=COALESCE(approved_at, datetime('now')),
              updated_at=datetime('now') WHERE id=?""", (pack["id"],))
    token = pack_utils.ensure_share_token(pack["id"])
    return {
        "org_id": org_id,
        "slug": DEMO["slug"],
        "pack_id": pack["id"],
        "share_url": f"{config.BASE_URL}/share/{token}",
    }
