import sqlite3
import stat

from fastapi.testclient import TestClient

import os

os.environ["DIONYSUS_DATA_DIR"] = "/tmp/dionysus-test-data"
os.environ["DIONYSUS_SECRET_KEY"] = "test-secret"
os.environ["DIONYSUS_MISE_IMPORT_TOKEN"] = "mise-test"
os.environ["DIONYSUS_STUDIO_MODE"] = "false"

from app import db, jobs, rate_limit  # noqa: E402
from app.main import app  # noqa: E402


def configure_tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DIONYSUS_DATA_DIR", str(tmp_path))
    from app import config
    config.DATA_DIR = tmp_path
    config.DB_PATH = tmp_path / "dionysus.db"
    # Self-contained: STUDIO_MODE is read once at import (default True). Without
    # this, the SaaS routes (signup) 404 unless test_app is collected first, so
    # other test modules would pass only by alphabetical collection-order luck.
    config.STUDIO_MODE = False
    config.MISE_IMPORT_TOKEN = "mise-test"
    config.BASE_URL = "http://localhost:8450"
    config.COOKIE_SECURE = False
    db.migrate()


def signup(client=None, *, drain=True, audience="restaurant", **overrides):
    """Provision the blue-plate subject (org) + a draft pack. No HTTP/signup
    route exists anymore; Mise/CLI provision subjects now."""
    from app import db, jobs, security
    slug = overrides.get("slug", "blue-plate")
    org = db.one("SELECT * FROM organizations WHERE slug=?", (slug,))
    if not org:
        with db.tx() as con:
            cur = con.execute(
                """INSERT INTO organizations
                   (slug,name,email,audience,company,plan,access_token,market,service_mix,brand_voice)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (slug, "Avery", "avery@example.com", audience, "Blue Plate",
                 "restaurant_growth", security.new_token(), "Asheville",
                 "dine-in and delivery", "warm and chef-led"))
            org_id = cur.lastrowid
            con.execute("INSERT INTO menu_items (org_id,name,category,notes) VALUES (?,?,?,?)",
                        (org_id, "Spring agnolotti", "priority dish", "peas, ricotta, lemon"))
            cur = con.execute("INSERT INTO campaigns (org_id,title,goal,launch_date) VALUES (?,?,?,?)",
                              (org_id, "First monthly content pack", "fill weekday reservations", "2026-07-01"))
            campaign_id = cur.lastrowid
        recipe = db.one("SELECT id FROM content_recipes WHERE slug='monthly-retainer'")
        jobs.enqueue_generate(campaign_id, recipe["id"])
    if drain:
        jobs.drain()


def _approve_pack(pack_id):
    """The /w approve route is gone; Mise/CLI approve via direct DB update."""
    db.run("""UPDATE content_packs SET status='approved',
              approved_at=datetime('now') WHERE id=?""", (pack_id,))


def test_mise_api_is_dormant_or_bearer_gated(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    assert client.get("/api/mise/organizations/nope/latest-pack").status_code == 401
    assert client.get(
        "/api/mise/organizations/nope/latest-pack",
        headers={"Authorization": "Bearer mise-test"},
    ).status_code == 404


def test_healthz_reports_global_queue_health(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client, drain=False)
    org = db.one("SELECT * FROM organizations WHERE slug='blue-plate'")
    job = db.one("SELECT * FROM jobs WHERE kind='generate_pack'")

    res = client.get("/healthz")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["jobs_pending"] == 1
    assert body["jobs_failed"] == 0
    assert body["queue"]["queued"] == 1
    assert body["queue"]["running"] == 0
    assert body["queue"]["failed"] == 0
    assert body["queue"]["health_label"] == "working"
    assert body["queue"]["oldest_active_job_id"] == job["id"]

    db.run("""UPDATE jobs
              SET status='running',
                  updated_at=datetime('now', '-2 hours'),
                  created_at=datetime('now', '-2 hours')
              WHERE id=?""", (job["id"],))
    db.run("""INSERT INTO jobs (kind, payload, status, attempts, error, org_id)
              VALUES (?,?,?,?,?,?)""",
           ("generate_pack", "{}", "failed", 1, "model unavailable", org["id"]))
    from app import config
    monkeypatch.setattr(config, "JOB_STALE_SECONDS", 60)

    res = client.get("/healthz")
    queue = res.json()["queue"]
    assert res.json()["jobs_pending"] == 1
    assert res.json()["jobs_failed"] == 1
    assert queue["running"] == 1
    assert queue["stale_running"] == 1
    assert queue["failed"] == 1
    assert queue["health_label"] == "stale"

    org_queue = jobs.queue_stats_for_org(org["id"])
    assert org_queue["stale_running"] == 1
    assert org_queue["failed"] == 1


def test_readiness_fails_with_default_dev_config(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    from app import config
    config.SECRET_KEY = "dev-dionysus-secret"
    config.BASE_URL = "http://localhost:8450"
    config.COOKIE_SECURE = False
    config.MISE_IMPORT_TOKEN = ""
    client = TestClient(app)
    res = client.get("/readiness")
    assert res.status_code == 200
    body = res.json()
    assert body["ready"] is False
    assert any(c["key"] == "secret_key" and not c["ok"] for c in body["checks"])


def test_readiness_passes_when_production_env_is_armed(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    from app import config
    config.SECRET_KEY = "a-real-secret-value"
    config.BASE_URL = "https://platekit.example.com"
    config.COOKIE_SECURE = True
    config.MISE_IMPORT_TOKEN = "mise-token"
    client = TestClient(app)
    res = client.get("/readiness")
    assert res.status_code == 200
    assert res.json()["ready"] is True


def test_latest_pack_api_hides_newer_drafts_by_default(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    first = db.one("SELECT * FROM content_packs")

    hidden = client.get(
        "/api/mise/organizations/blue-plate/latest-pack",
        headers={"Authorization": "Bearer mise-test"},
    )
    assert hidden.status_code == 200
    assert hidden.json()["pack"] is None

    _approve_pack(first["id"])
    # A newer DRAFT for the same campaign: enqueue another generate with a
    # different recipe (idempotency is keyed on campaign+recipe+run).
    campaign = db.one("SELECT id FROM campaigns LIMIT 1")
    recipe = db.one("SELECT id FROM content_recipes WHERE slug='menu-launch'")
    jobs.enqueue_generate(campaign["id"], recipe["id"])
    jobs.drain()
    newest_draft = db.one("SELECT * FROM content_packs ORDER BY id DESC LIMIT 1")
    assert newest_draft["id"] != first["id"]
    assert newest_draft["status"] == "draft"

    latest = client.get(
        "/api/mise/organizations/blue-plate/latest-pack",
        headers={"Authorization": "Bearer mise-test"},
    )
    assert latest.status_code == 200
    assert latest.json()["pack"]["id"] == first["id"]
    assert latest.json()["pack"]["status"] == "approved"

    drafts = client.get(
        "/api/mise/organizations/blue-plate/latest-pack?include_drafts=true",
        headers={"Authorization": "Bearer mise-test"},
    )
    assert drafts.status_code == 200
    assert drafts.json()["pack"]["id"] == newest_draft["id"]
    assert drafts.json()["pack"]["status"] == "draft"


def test_mise_packs_api_returns_only_approved_or_exported_by_default(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    pack = db.one("SELECT * FROM content_packs")

    hidden = client.get(
        "/api/mise/organizations/blue-plate/packs",
        headers={"Authorization": "Bearer mise-test"},
    )
    assert hidden.status_code == 200
    assert hidden.json()["packs"] == []

    _approve_pack(pack["id"])
    shown = client.get(
        "/api/mise/organizations/blue-plate/packs",
        headers={"Authorization": "Bearer mise-test"},
    )
    body = shown.json()
    assert body["matched"] is True
    assert len(body["packs"]) == 1
    api_pack = body["packs"][0]
    assert api_pack["title"] == "First monthly content pack"
    assert api_pack["status"] == "approved"
    assert api_pack["campaign"]["title"] == "First monthly content pack"
    assert "## Shot List" in api_pack["markdown"]
    assert "Spring agnolotti" in api_pack["markdown"]


def test_mise_packs_api_can_include_drafts(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    res = client.get(
        "/api/mise/organizations/blue-plate/packs?include_drafts=true",
        headers={"Authorization": "Bearer mise-test"},
    )
    assert res.status_code == 200
    assert len(res.json()["packs"]) == 1
    assert res.json()["packs"][0]["status"] == "draft"


def test_cli_seed_demo_creates_approved_pack_for_mise_bridge(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    from app import cli, config
    config.BASE_URL = "https://platekit.example.com"
    assert cli.main(["seed-demo"]) == 0
    org = db.one("SELECT * FROM organizations WHERE slug='blue-plate'")
    assert org and org["company"] == "Blue Plate"
    pack = db.one("SELECT * FROM content_packs WHERE org_id=?", (org["id"],))
    assert pack["status"] == "approved"
    client = TestClient(app)
    res = client.get(
        "/api/mise/organizations/blue-plate/packs",
        headers={"Authorization": "Bearer mise-test"},
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["packs"]) == 1
    assert body["packs"][0]["id"] == pack["id"]
    assert body["packs"][0]["status"] == "approved"


def test_cli_worker_once_processes_queued_signup_job(tmp_path, monkeypatch, capsys):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client, drain=False)
    from app import cli

    assert cli.main(["worker", "--once"]) == 0
    output = capsys.readouterr().out
    assert "worker\tprocessed=1\tpending=0\tfailed=0" in output
    job = db.one("SELECT * FROM jobs WHERE kind='generate_pack'")
    assert job["status"] == "done"
    assert job["result_pack_id"]
    assert db.one("SELECT COUNT(*) AS n FROM content_packs")["n"] == 1


def test_cli_rate_limits_summarizes_without_sensitive_identities(tmp_path, monkeypatch, capsys):
    configure_tmp_db(tmp_path, monkeypatch)
    from app import cli

    sensitive_login = rate_limit.identity("203.0.113.9", "leakme@example.com")
    sensitive_invite = rate_limit.identity("198.51.100.2", "super-secret-token")
    rate_limit.check("login:subject_ip", sensitive_login, limit=20, window_seconds=900)
    rate_limit.check("login:subject_ip", sensitive_login, limit=20, window_seconds=900)
    rate_limit.check("invite_accept:subject_ip", sensitive_invite,
                     limit=20, window_seconds=900)
    db.run("""INSERT INTO rate_limit_events (action, identity, created_at)
              VALUES (?, ?, datetime('now', '-2 hours'))""",
           ("signup:subject_ip", rate_limit.identity("old@example.com")))

    assert cli.main(["rate-limits", "--window", "900", "--limit", "5"]) == 0
    output = capsys.readouterr().out
    assert "rate_limits\twindow=900\taction=all\trows=2" in output
    assert "login:subject_ip\tattempts=2\tbucket=rl_" in output
    assert "invite_accept:subject_ip\tattempts=1\tbucket=rl_" in output
    assert "leakme@example.com" not in output
    assert "super-secret-token" not in output
    assert "203.0.113.9" not in output
    assert "old@example.com" not in output

    assert cli.main(["rate-limits", "--action", "login:subject_ip"]) == 0
    filtered = capsys.readouterr().out
    assert "rate_limits\twindow=900\taction=login:subject_ip\trows=1" in filtered
    assert "login:subject_ip\tattempts=2" in filtered
    assert "invite_accept:subject_ip" not in filtered


def test_cli_backup_creates_private_verified_snapshot(tmp_path, monkeypatch, capsys):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    from app import cli

    destination = tmp_path / "snapshots"
    assert cli.main(["backup", str(destination)]) == 0
    output = capsys.readouterr().out
    assert "backup\t" in output
    assert "restore_check\tok\tintegrity=ok\tmigrations=12" in output

    snapshots = list(destination.glob("dionysus-*.db"))
    assert len(snapshots) == 1
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700
    assert stat.S_IMODE(snapshots[0].stat().st_mode) == 0o600

    con = sqlite3.connect(snapshots[0])
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT slug FROM organizations").fetchone()[0] == "blue-plate"
        assert con.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] >= 0
    finally:
        con.close()

    assert cli.main(["verify-backup", str(snapshots[0])]) == 0
    verify_output = capsys.readouterr().out
    assert "verify\tok\tintegrity=ok\tmigrations=12\ttables=10" in verify_output


def test_enqueue_reuses_legacy_active_jobs_without_idempotency_key(tmp_path, monkeypatch):
    import json
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    org = db.one("SELECT * FROM organizations WHERE slug='blue-plate'")
    campaign = db.one("SELECT * FROM campaigns LIMIT 1")
    recipe = db.one("SELECT * FROM content_recipes WHERE slug='menu-launch'")
    pack = db.one("SELECT * FROM content_packs")

    legacy_generate = db.run(
        """INSERT INTO jobs (kind, payload, status, org_id)
           VALUES (?,?,?,?)""",
        (
            "generate_pack",
            json.dumps({
                "campaign_id": campaign["id"],
                "recipe_id": recipe["id"],
                "argus_run_id": None,
            }),
            "queued",
            org["id"],
        ),
    )
    assert jobs.enqueue_generate(campaign["id"], recipe["id"]) == legacy_generate

    legacy_regenerate = db.run(
        """INSERT INTO jobs (kind, payload, status, org_id, source_pack_id)
           VALUES (?,?,?,?,?)""",
        (
            "regenerate_pack",
            json.dumps({
                "source_pack_id": pack["id"],
                "feedback": "  Make   It Premium.  ",
            }),
            "queued",
            org["id"],
            pack["id"],
        ),
    )
    assert jobs.enqueue_regenerate(pack["id"], "make it premium.") == legacy_regenerate
    assert db.one(
        "SELECT COUNT(*) AS n FROM jobs WHERE status='queued'"
    )["n"] == 2
