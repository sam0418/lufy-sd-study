#!/usr/bin/env bash
# Start the web study on the Mac mini: two engines (localhost only) + the portal.
# Expose only the portal:  tailscale funnel --bg 8000
# Keep the Mac awake:      caffeinate -dims &   (or turn off sleep in System Settings)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/runs/logs"
for d in A B portal; do
  [ -f "$ROOT/runs/$d/.env" ] || { echo "missing runs/$d/.env (copy .env.example)"; exit 1; }
done
[ -f "$ROOT/runs/participants.json" ] || { echo "missing runs/participants.json"; exit 1; }
start() {  # name, module
  (cd "$ROOT/runs/$1" && PYTHONPATH="$ROOT" nohup python -m "$2" >> "$ROOT/runs/logs/$1.out" 2>&1 &)
  echo "started $1 ($2)"
}
start A study.engine_server
start B study.engine_server
start portal study.portal
