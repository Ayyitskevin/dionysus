# Dionysus / Platekit

Mise studio service for campaign copy and print-pitch enrichment.

**Default: `DIONYSUS_STUDIO_MODE=true`** — no public signup, Stripe, or workspace UI.
Kevin operates through Mise gallery admin; Dionysus exposes bearer-gated service APIs only.

- **Mise** — galleries, clients, proofing, delivery, invoices
- **Dionysus** — print pitch enrichment for Plutus `pitch.txt`, keyword campaign packs after Argus vision

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

### Local content model (optional)

Generation can call a **local** OpenAI-compatible endpoint for draft text
(Ollama / llama.cpp / vLLM / LM Studio). When unset, the deterministic templates
are used — so there is no cloud dependency and CI runs offline.

```bash
DIONYSUS_MODEL_ENDPOINT=http://localhost:11434/v1   # /chat/completions is appended
DIONYSUS_MODEL_NAME=llama3.1:8b
DIONYSUS_MODEL_API_KEY=        # optional bearer for the local endpoint
DIONYSUS_MODEL_TIMEOUT=30
```

On any error or timeout, generation falls back to the deterministic draft and
never crashes Mise's path. Every draft stays a reversible draft a human accepts;
`cost_usd` is `0.0` for local inference and the run reports `model`/`latency_ms`.

### Plutus studio pitch hand-off (homelab :8450)

Plutus homelab (`:8030`) enriches `pitch.txt` via:

```text
POST /api/mise/organizations/{slug}/print-pitch
Authorization: Bearer <DIONYSUS_MISE_IMPORT_TOKEN>
```

Wire from the Plutus tree: `scripts/wire-dionysus-homelab.sh` (sets
`PLUTUS_DIONYSUS_URL`, `PLUTUS_DIONYSUS_TOKEN`, `PLUTUS_DIONYSUS_ORG_SLUG`).

Homelab bring-up in this repo:

```bash
cp ops/homelab.env.example .env   # set DIONYSUS_MISE_IMPORT_TOKEN
bash scripts/install-homelab-service.sh
bash scripts/deploy-homelab.sh
bash scripts/dogfood-plutus-pitch.sh [plutus_run_id]
```



## Studio mode (default)

| API | Purpose |
|-----|---------|
| `POST /api/mise/organizations/{slug}/print-pitch` | Plutus client email enrichment |
| `POST /api/mise/organizations/{slug}/argus-pack` | Campaign pack draft after Argus run |
| `GET /api/mise/organizations/{slug}/packs` | Approved packs for Mise client admin |

Homelab: `ops/homelab.env.example`, `scripts/deploy-homelab.sh`, `scripts/dogfood-plutus-pitch.sh`

Legacy Platekit SaaS UI (signup, billing, `/w/{slug}`) remains in the tree but is **gated off**
when `DIONYSUS_STUDIO_MODE=true`. Set `DIONYSUS_STUDIO_MODE=false` only for a public SaaS deploy.



## Retire-readiness

Dionysus is a **stateless content worker** for Mise; see [`RETIRE.md`](RETIRE.md)
for the map of what is engine vs. authoritative SaaS state, the phased plan to
strip the SaaS layer, and the path to fully absorb the worker into Mise.

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

CI (`.github/workflows/ci.yml`) runs the suite on every push and pull request.
It is **mock-only and reproducible** — a guard step fails the build if
`DIONYSUS_MODEL_ENDPOINT` or `DIONYSUS_ARGUS_URL` is set, so there are no live
model or Argus calls.
