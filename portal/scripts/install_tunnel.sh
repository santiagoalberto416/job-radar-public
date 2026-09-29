#!/usr/bin/env bash
# Exposes the portal through ngrok, read-only, behind Google login restricted to the email(s) you pass.
# Usage: portal/scripts/install_tunnel.sh <your-static-domain.ngrok-free.dev> <you@gmail.com>[,<other@gmail.com>]
set -euo pipefail

PREFIX="${JOB_RADAR_LABEL_PREFIX:-com.jobradar}"
LABEL="$PREFIX.tunnel"
PORTAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POLICY="$PORTAL_DIR/ngrok/policy.yml"
TEMPLATE="$PORTAL_DIR/ngrok/policy.template.yml"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_FILE="$HOME/Library/Logs/job-radar-tunnel.log"
PORT="${PORTAL_PORT:-4747}"
DOMAIN="${1:-${NGROK_DOMAIN:-}}"
EMAILS="${2:-${NGROK_ALLOWED_EMAILS:-}}"
NGROK="${NGROK_BIN:-$HOME/.local/bin/ngrok}"
[ -x "$NGROK" ] || NGROK="$(command -v ngrok || true)"

if [ -z "$DOMAIN" ] || [[ ! "$DOMAIN" =~ ^[a-z0-9.-]+\.[a-z]+$ ]] || [ -z "$EMAILS" ]; then
  echo "Usage: $0 <static-domain> <google-email>[,<google-email>]"
  echo "  (claim the free static domain at https://dashboard.ngrok.com/domains)"; exit 1
fi
# Render the policy with the allowed emails (validated so nothing else can be injected into the expression).
QUOTED=""
IFS=',' read -ra LIST <<< "$EMAILS"
for email in "${LIST[@]}"; do
  email="$(echo "$email" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
  [[ "$email" =~ ^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]+$ ]] || { echo "ERROR: invalid email: $email"; exit 1; }
  QUOTED="${QUOTED:+$QUOTED, }'$email'"
done
sed "s|__ALLOWED_EMAILS__|$QUOTED|" "$TEMPLATE" > "$POLICY"
if [ -z "$NGROK" ]; then echo "ERROR: ngrok not found"; exit 1; fi
# Never start the tunnel without the login policy.
if ! grep -q "type: oauth" "$POLICY" || ! grep -q "type: deny" "$POLICY" || ! grep -q "identity.email" "$POLICY"; then
  echo "ERROR: $POLICY must contain the oauth + email allowlist + deny rules. Refusing to expose the portal."; exit 1
fi
"$NGROK" config check >/dev/null

# Tell the portal to accept (read-only) requests for this domain, then restart it.
echo "$DOMAIN" > "$PORTAL_DIR/.remote-hosts"
launchctl kickstart -k "gui/$(id -u)/$PREFIX.portal" 2>/dev/null \
  || echo "Note: the portal service isn't installed; run portal/scripts/install_portal.sh"

sed -e "s|__LABEL__|$LABEL|g" -e "s|__NGROK__|$NGROK|g" -e "s|__PORT__|$PORT|g" -e "s|__DOMAIN__|$DOMAIN|g" \
    -e "s|__POLICY__|$POLICY|g" -e "s|__LOG__|$LOG_FILE|g" \
    "$PORTAL_DIR/scripts/tunnel.plist.template" > "$PLIST"
plutil -lint "$PLIST" >/dev/null
DOMAIN_ID="gui/$(id -u)"
launchctl bootout "$DOMAIN_ID/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN_ID" "$PLIST"
launchctl enable "$DOMAIN_ID/$LABEL"
echo "Tunnel running: https://$DOMAIN  (Google login required for: $EMAILS; read-only)"
echo "Log: $LOG_FILE    Stop: portal/scripts/uninstall_tunnel.sh"
