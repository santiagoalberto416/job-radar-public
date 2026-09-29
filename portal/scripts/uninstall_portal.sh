#!/usr/bin/env bash
# Stops and removes the portal's launchd agent.
set -euo pipefail
LABEL="${JOB_RADAR_LABEL_PREFIX:-com.jobradar}.portal"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && echo "Stopped $LABEL" || echo "$LABEL was not loaded"
[ -f "$PLIST" ] && rm "$PLIST" && echo "Removed $PLIST"
exit 0
