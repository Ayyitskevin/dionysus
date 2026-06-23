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
Authorization: Bearer <token>
```

That endpoint gives Mise/Odysseus a clean bridge to the latest Dionysus content
pack without coupling the two apps.

## Verification

```bash
pytest
```
