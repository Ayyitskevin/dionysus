#!/usr/bin/env bash
# Pull latest main and restart Dionysus homelab (:8450).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PORT="${DIONYSUS_PORT:-8450}"
BRANCH="${DIONYSUS_DEPLOY_BRANCH:-main}"

echo "==> pytest gate"
if [[ -x "$ROOT/.venv/bin/pytest" ]]; then
  "$ROOT/.venv/bin/python" -m pytest -q \
    tests/test_studio_mode.py \
    tests/test_print_pitch_api.py \
    tests/test_mise_argus_hook.py \
    tests/test_print_pitch.py \
    tests/test_argus_packs.py
else
  echo "WARN: .venv missing — run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
fi

echo "==> git pull origin $BRANCH"
git pull origin "$BRANCH"

echo "==> migrate + seed demo org"
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"
export DIONYSUS_DATA_DIR="${DIONYSUS_DATA_DIR:-$ROOT/data}"
python -m app.cli migrate
python -m app.cli seed-demo >/dev/null

if systemctl --user is-enabled dionysus-homelab.service >/dev/null 2>&1; then
  echo "==> restart dionysus-homelab"
  systemctl --user restart dionysus-homelab
else
  echo "==> service not installed — run: bash scripts/install-homelab-service.sh" >&2
  exit 1
fi

sleep 2
systemctl --user is-active dionysus-homelab
curl -sf "http://127.0.0.1:${PORT}/healthz" | python3 -m json.tool | head -12
echo "==> Deploy OK — Dionysus homelab :${PORT}"