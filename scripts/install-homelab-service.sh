#!/usr/bin/env bash
# Install user-level Dionysus homelab unit (:8450).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_SRC="$ROOT/ops/dionysus-homelab-user.service"
UNIT_DST="$HOME/.config/systemd/user/dionysus-homelab.service"

mkdir -p "$HOME/.config/systemd/user"
cp "$UNIT_SRC" "$UNIT_DST"
systemctl --user daemon-reload
systemctl --user enable --now dionysus-homelab
sleep 2
systemctl --user is-active dionysus-homelab
curl -sf http://127.0.0.1:8450/healthz | python3 -m json.tool | head -10
echo "==> Dionysus homelab :8450"