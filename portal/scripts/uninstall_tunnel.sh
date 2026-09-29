#!/usr/bin/env bash
# Closes the ngrok tunnel and stops the portal from accepting the remote domain.
set -euo pipefail
PREFIX="${JOB_RADAR_LABEL_PREFIX:-com.jobradar}"
LABEL="$PREFIX.tunnel"
PORTAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && echo "Stopped $LABEL" || echo "$LABEL was not loaded"
[ -f "$PLIST" ] && rm "$PLIST" && echo "Removed $PLIST"
rm -f "$PORTAL_DIR/.remote-hosts"
launchctl kickstart -k "gui/$(id -u)/$PREFIX.portal" 2>/dev/null || true
echo "The portal is local-only again."
