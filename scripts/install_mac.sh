#!/usr/bin/env bash
# Installs job-radar: Python venv + dependencies, then two launchd LaunchAgents (the search every 2 hours and
# the Telegram command bot). Safe to re-run: it updates the packages and reloads both agents.
#   scripts/install_mac.sh --deps-only   # only the venv and dependencies (first-time setup)
#   scripts/install_mac.sh               # dependencies + start the background services
set -euo pipefail
DEPS_ONLY=false
[ "${1:-}" = "--deps-only" ] && DEPS_ONLY=true

PREFIX="${JOB_RADAR_LABEL_PREFIX:-com.jobradar}"
LABEL="$PREFIX.search"
BOT_LABEL="$PREFIX.bot"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST="$PLIST_DIR/$LABEL.plist"
LOG_FILE="$HOME/Library/Logs/job-radar.log"
BOT_PLIST="$PLIST_DIR/$BOT_LABEL.plist"
BOT_LOG_FILE="$HOME/Library/Logs/job-radar-bot.log"
VENV="$REPO_DIR/.venv"
PY_VERSION="3.12"  # python-jobspy pins numpy 1.26, which has no wheels for Python >= 3.13

echo "Repo: $REPO_DIR"

# launchd jobs can't read TCC-protected folders.
case "$REPO_DIR" in
  "$HOME/Documents"*|"$HOME/Desktop"*|"$HOME/Downloads"*|"$HOME/Library/Mobile Documents"*)
    echo "ERROR: the repo is inside a macOS-protected folder (Documents/Desktop/Downloads/iCloud)."
    echo "launchd background jobs can't read it. Move it (e.g. to ~/job-radar) and run this again."
    exit 1
    ;;
esac

# Exact tested versions (requirements.lock, generated from requirements.txt with `uv pip compile`).
REQS="$REPO_DIR/requirements.lock"
[ -f "$REQS" ] || REQS="$REPO_DIR/requirements.txt"

# 1. Python venv (native arm64/x86_64 to match the Mac; Python 3.10-3.12).
if command -v uv >/dev/null 2>&1; then
  echo "Using uv to provide Python $PY_VERSION"
  uv python install "$PY_VERSION"
  [ -x "$VENV/bin/python" ] || uv venv --python "$PY_VERSION" "$VENV"
  uv pip install --python "$VENV/bin/python" -q -r "$REQS"
else
  PYTHON=""
  for candidate in python3.12 python3.11 python3.10 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11; do
    if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$(command -v "$candidate")"; break; fi
  done
  if [ -z "$PYTHON" ]; then
    echo "ERROR: need Python 3.10-3.12. Install uv (https://docs.astral.sh/uv/) or: brew install python@3.12"
    exit 1
  fi
  echo "Using $PYTHON"
  [ -x "$VENV/bin/python" ] || "$PYTHON" -m venv "$VENV"
  "$VENV/bin/python" -m pip install -q --upgrade pip
  "$VENV/bin/python" -m pip install -q -r "$REQS"
fi
"$VENV/bin/python" -c "import jobspy, anthropic, yaml, dotenv" \
  || { echo "ERROR: dependencies failed to import (wrong CPU architecture?). Delete .venv and retry."; exit 1; }

# 2. Secrets file.
if [ ! -f "$REPO_DIR/.env" ]; then
  cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
  echo "Created .env from .env.example. Fill in your keys before the first run."
fi
chmod 600 "$REPO_DIR/.env"

if $DEPS_ONLY; then
  echo
  echo "Dependencies installed. Next: create config.yaml and profile.md, fill in .env (see README), then run"
  echo "  scripts/install_mac.sh"
  exit 0
fi

# 3. Don't start background services until the setup is complete.
missing=()
[ -f "$REPO_DIR/config.yaml" ] || missing+=("config.yaml   (cp config.example.yaml config.yaml)")
[ -f "$REPO_DIR/profile.md" ] || missing+=("profile.md    (cp profile.example.md profile.md)")
for key in ANTHROPIC_API_KEY TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID; do
  grep -Eq "^${key}=.+" "$REPO_DIR/.env" || missing+=("$key in .env")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo
  echo "Not starting the background services yet. Missing:"
  printf '  - %s\n' "${missing[@]}"
  echo "Complete these steps (see README), then run scripts/install_mac.sh again."
  exit 1
fi

# 4. Render the plists with this repo's absolute paths (XML-escaped).
mkdir -p "$PLIST_DIR" "$(dirname "$LOG_FILE")"
render() {  # render <template> <output> <label>
  "$VENV/bin/python" - "$1" "$2" "$3" "$VENV/bin/python" "$REPO_DIR" "$LOG_FILE" "$BOT_LOG_FILE" <<'PY'
import sys
from xml.sax.saxutils import escape
template, out, label, python, repo, log, bot_log = sys.argv[1:]
text = open(template, encoding="utf-8").read()
for key, value in {"__LABEL__": label, "__PYTHON__": python, "__REPO__": repo, "__LOG__": log, "__BOTLOG__": bot_log}.items():
    text = text.replace(key, escape(value))
open(out, "w", encoding="utf-8").write(text)
PY
  plutil -lint "$2" >/dev/null
}
render "$REPO_DIR/scripts/search.plist.template" "$PLIST" "$LABEL"
render "$REPO_DIR/scripts/bot.plist.template" "$BOT_PLIST" "$BOT_LABEL"

# 5. (Re)load both agents. The scheduled one runs once right away (RunAtLoad); the bot stays running.
DOMAIN="gui/$(id -u)"
for label in "$LABEL" "$BOT_LABEL"; do
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$PLIST_DIR/$label.plist"
  launchctl enable "$DOMAIN/$label"
done

echo
echo "Installed $PLIST and $BOT_PLIST"
echo "Searches run now, then every 2 hours (missed runs happen on wake)."
echo "The Telegram bot answers commands like /ultimos (send /ayuda to your bot)."
echo "Logs:   tail -f \"$LOG_FILE\"   (bot: $BOT_LOG_FILE)"
echo "Status: launchctl print $DOMAIN/$LABEL | grep -E 'state|last exit'"
echo "Stop:   scripts/uninstall_mac.sh"
