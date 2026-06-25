#!/usr/bin/env bash
# Homelab dogfood — Plutus pitch enrichment via Dionysus print-pitch API.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

PORT="${DIONYSUS_PORT:-8450}"
BASE="${DIONYSUS_BASE_URL:-http://127.0.0.1:${PORT}}"
BASE="${BASE%/}"
TOKEN="${DIONYSUS_MISE_IMPORT_TOKEN:?DIONYSUS_MISE_IMPORT_TOKEN required}"
ORG="${DIONYSUS_DEMO_ORG_SLUG:-blue-plate}"
PLUTUS_BASE="${PLUTUS_URL:-http://127.0.0.1:8030}"
PLUTUS_RUN_ID="${1:-7}"

echo "==> Dionysus health"
curl -sf "$BASE/healthz" | python3 -c "
import json, sys
h = json.load(sys.stdin)
print('  service:', h.get('service'), 'jobs_pending:', h.get('jobs_pending'))
studio = h.get('studio') or {}
print('  mise_bridge:', studio.get('mise_bridge_armed'))
assert studio.get('mise_bridge_armed'), 'set DIONYSUS_MISE_IMPORT_TOKEN'
"

echo "==> print-pitch API"
curl -sf -X POST "$BASE/api/mise/organizations/${ORG}/print-pitch" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "gallery_name": "Seasonal Tasting Menu",
    "photo_count": 6,
    "estimated_total_cents": 65500,
    "gallery_theme": "food",
    "argus_run_id": 219,
    "bundles": [{
      "title": "Statement wall piece",
      "pitch": "Lead with your strongest hero.",
      "items": [{
        "label": "Canvas",
        "photo": {"filename": "hero.jpg", "keywords": ["risotto", "chef", "warm light"]}
      }]
    }]
  }' | python3 -c "
import json, sys
body = json.load(sys.stdin)
assert body.get('ok'), body
assert 'Keywords that sell the story' in body['bundles'][0]['pitch'], body['bundles'][0]
print('  intro:', body['intro'][:72], '...')
print('  bundle pitch OK')
"

if curl -sf "${PLUTUS_BASE}/healthz" >/dev/null 2>&1; then
  echo "==> Plutus pitch.txt run #${PLUTUS_RUN_ID}"
  PITCH=$(curl -sf "${PLUTUS_BASE}/runs/${PLUTUS_RUN_ID}/pitch.txt" || true)
  if [[ -n "$PITCH" ]]; then
    echo "$PITCH" | head -12
    echo "$PITCH" | grep -qi "Keywords that sell the story" \
      || { echo "Plutus pitch missing Dionysus enrichment — wire PLUTUS_DIONYSUS_* on :8030" >&2; exit 1; }
    echo "  plutus pitch OK"
  else
    echo "  WARN: run ${PLUTUS_RUN_ID} not found on ${PLUTUS_BASE}"
  fi
fi

echo "==> Dionysus Plutus pitch dogfood OK"