#!/usr/bin/env bash
# Stops and removes the job-radar LaunchAgents (search + Telegram bot). Keeps the repo, .env, database and logs.
set -euo pipefail

PREFIX="${JOB_RADAR_LABEL_PREFIX:-com.jobradar}"
for LABEL in "$PREFIX.search" "$PREFIX.bot"; do
  PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && echo "Stopped $LABEL" || echo "$LABEL was not loaded"
  if [ -f "$PLIST" ]; then
    rm "$PLIST"
    echo "Removed $PLIST"
  fi
done
echo "Database (data/jobs.db), .env and ~/Library/Logs/job-radar.log were kept."
