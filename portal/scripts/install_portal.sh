#!/usr/bin/env bash
# Builds the portal and runs it as a launchd agent (always on, 127.0.0.1 only). Safe to re-run.
set -euo pipefail

LABEL="${JOB_RADAR_LABEL_PREFIX:-com.jobradar}.portal"
PORTAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_FILE="$HOME/Library/Logs/job-radar-portal.log"
PORT="${PORTAL_PORT:-4747}"
NODE="$(command -v node || true)"

if [ -z "$NODE" ]; then echo "ERROR: node not found (need Node >= 22.5)"; exit 1; fi
"$NODE" -e 'const [a,b]=process.versions.node.split(".").map(Number); if (a<22||(a===22&&b<5)) process.exit(1)' \
  || { echo "ERROR: Node >= 22.5 required (found $("$NODE" --version))"; exit 1; }
echo "Using $NODE ($("$NODE" --version)); if you switch Node versions with nvm, re-run this script."

cd "$PORTAL_DIR"
npm install --no-audit --no-fund
npm run build

sed -e "s|__LABEL__|$LABEL|g" -e "s|__NODE__|$NODE|g" -e "s|__PORTAL__|$PORTAL_DIR|g" -e "s|__LOG__|$LOG_FILE|g" \
  -e "s|__PORT__|$PORT|g" -e "s|__PREFIX__|${JOB_RADAR_LABEL_PREFIX:-com.jobradar}|g" \
  "$PORTAL_DIR/scripts/portal.plist.template" > "$PLIST"
plutil -lint "$PLIST" >/dev/null

DOMAIN="gui/$(id -u)"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl enable "$DOMAIN/$LABEL"
echo "Portal running at http://127.0.0.1:$PORT  (log: $LOG_FILE)"
echo "Stop it with: portal/scripts/uninstall_portal.sh"
