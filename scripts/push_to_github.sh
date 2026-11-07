#!/bin/bash
# Run this in Terminal on the Mac (not through Claude). Packs the versioned Line Watch files,
# copies them into a local clone of the GitHub repo, commits, and pushes with YOUR GitHub login.
# One-time setup:
#   git clone https://github.com/neuegraphics-dev/sportsbook-nfl-research-sklill.git ~/linewatch-repo
# (sign in when prompted, or run `gh auth login` first if you have the GitHub CLI)
set -e
SRC="$(cd "$(dirname "$0")/.." && pwd)"
REPO="${LINEWATCH_REPO:-$HOME/linewatch-repo}"
[ -d "$REPO/.git" ] || { echo "Clone the repo first: git clone https://github.com/neuegraphics-dev/sportsbook-nfl-research-sklill.git $REPO"; exit 1; }
bash "$SRC/scripts/repo_pack.sh" >/dev/null
git -C "$REPO" pull --rebase --quiet || true
tar xzf "$SRC/_sync/linewatch-sync.tar.gz" -C "$REPO"
cd "$REPO"
git add -A
if git diff --cached --quiet; then echo "Nothing new to push."; exit 0; fi
git commit -q -m "Line Watch sync $(date '+%Y-%m-%d %H:%M')"
git push origin main
echo "Pushed to GitHub."
