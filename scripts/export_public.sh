#!/usr/bin/env bash
# Builds a clean copy of this repo for publishing: tracked files only, no personal config/profile, no git
# history, then checks it against the personal strings listed in .public-denylist (gitignored).
# Usage: scripts/export_public.sh <empty-target-dir>
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-}"
DENYLIST="$REPO_DIR/.public-denylist"

[ -n "$TARGET" ] || { echo "Usage: $0 <empty-target-dir>"; exit 1; }
if [ -e "$TARGET" ] && [ -n "$(ls -A "$TARGET" 2>/dev/null)" ]; then echo "ERROR: $TARGET is not empty"; exit 1; fi
[ -f "$DENYLIST" ] || { echo "ERROR: create $DENYLIST with your personal strings first (one per line)"; exit 1; }
mkdir -p "$TARGET"

# Personal files that stay in your private repo.
EXCLUDE=("config.yaml" "profile.md" ".public-denylist")

cd "$REPO_DIR"
git ls-files -z | while IFS= read -r -d '' file; do
  for skip in "${EXCLUDE[@]}"; do [ "$file" = "$skip" ] && continue 2; done
  mkdir -p "$TARGET/$(dirname "$file")"
  cp -p "$file" "$TARGET/$file"
done

# In the public repo, each user's personal files are ignored.
printf '\n# your personal setup (copy from the .example files)\nconfig.yaml\nprofile.md\n' >> "$TARGET/.gitignore"

# Refuse to hand over a copy that still contains personal data.
patterns=$(grep -Ev '^\s*(#|$)' "$DENYLIST" | paste -sd'|' -)
if hits=$(grep -rniE "$patterns" "$TARGET" --exclude=LICENSE -l 2>/dev/null); then
  echo "ERROR: personal data found in the export (fix these files first):"
  grep -rniE "$patterns" "$TARGET" --exclude=LICENSE | cut -c1-160
  exit 1
fi
for bad in .env data jobs.db portal/node_modules portal/dist portal/.remote-hosts portal/ngrok/policy.yml; do
  [ ! -e "$TARGET/$bad" ] || { echo "ERROR: $bad must not be exported"; exit 1; }
done
echo "Clean export in $TARGET ($(find "$TARGET" -type f | wc -l | tr -d ' ') files). Next:"
echo "  cd $TARGET && git init && git add . && git commit -m 'Initial commit'"
echo "  gh repo create <name> --public --source . --push"
