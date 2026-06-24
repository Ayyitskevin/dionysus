# Dionysus / Platekit

Photography AI SaaS for food, beverage, and restaurant content.

Dionysus is designed to synergize with Mise without becoming Mise:

- Mise remains the operating system for Kevin Lee Photography: inquiries, clients,
  proposals, contracts, invoices, galleries, proofing, usage rights, and delivery.
- Dionysus is the subscription product around the shoot: restaurant owners and
  photographers turn one food session into campaign briefs, shot lists, captions,
  delivery-app copy, social exports, and retainer upsells.

## Product Shape

For restaurant owners:

- Menu-launch and seasonal campaign packs
- Social captions from real menu/brand inputs
- DoorDash/Uber Eats/Google Business Profile refresh prompts
- Press and email angles for new dishes or events

For photographers:

- Client intake workspaces
- Food shoot shot lists and campaign strategy
- Usage-rights prompts and add-on language
- Retainer upsell scripts that convert prep work into paid deliverables

## Run Locally

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8450
```

Open `http://127.0.0.1:8450`.

## Environment

```bash
DIONYSUS_SECRET_KEY=change-me
DIONYSUS_DATA_DIR=/opt/dionysus/data
DIONYSUS_BASE_URL=https://your-domain.example
DIONYSUS_MISE_IMPORT_TOKEN=optional-service-token
```

`DIONYSUS_MISE_IMPORT_TOKEN` arms the dormant service API:

```text
GET /api/mise/organizations/{slug}/latest-pack
POST /api/mise/organizations/{slug}/argus-pack
Authorization: Bearer <token>
```

`latest-pack` gives Mise a read bridge to approved Dionysus content. The bridge
returns `share_url` only when an owner/admin has explicitly shared the pack; read
requests never create public share links. When Mise's Argus callback fires, it can
`POST argus-pack` with `argus_run_id` (and optional `mise_gallery_id`) to draft
keyword-enriched captions without coupling the apps.



## SaaS Foundation

The current MVP includes the first paid-product boundary:

- Account signup and login with PBKDF2 password hashes
- Workspace ownership through `organization_members`
- Trial subscription rows per workspace
- Plan-gated content recipes and monthly pack limits
- Billing page that stays in trial mode until Stripe keys and price IDs are configured

Stripe checkout now creates real subscription Checkout Sessions when
`DIONYSUS_STRIPE_SECRET_KEY` and the active plan's Stripe price ID are configured.
`POST /stripe/webhook` verifies Stripe signatures with
`DIONYSUS_STRIPE_WEBHOOK_SECRET` and syncs subscription status from Stripe events.

Production starting points:

- `ops/env.example`
- `ops/dionysus.service`
- `ops/dionysus-worker.service`
- `ops/backup-restore.md`



## Production Readiness

Use the deployment runbook before putting a domain in front of the app:

- `ops/deploy.md`
- `ops/env.example`
- `ops/dionysus.service`
- `ops/dionysus-worker.service`

The app exposes `/readiness` and a CLI gate:

```bash
python -m app.cli check-production
```

The gate fails until production secrets, HTTPS base URL, secure cookies, Stripe
keys, Stripe price IDs, webhook secret, and Mise bridge token are configured.
Run the queue worker separately from the web process:

```bash
python -m app.cli worker
python -m app.cli worker --once  # process one queued job and exit
```

Create a verified SQLite backup before production deploys and before any restore:

```bash
python -m app.cli backup ./data/backups
python -m app.cli verify-backup ./data/backups/<backup>.db
```

Inspect recent auth-abuse buckets without printing emails, IPs, or invite tokens:

```bash
python -m app.cli rate-limits --window 900 --limit 20
```

## Verification

```bash
pytest
```
