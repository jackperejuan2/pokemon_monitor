#!/usr/bin/env bash
# Provision the Pokemon Monitor to run inside THIS user's background GUI session.
#
# Purpose: run the headed-Chrome retailer checks in a dedicated macOS user's
# session so the browser windows never appear on your main desktop. Run this
# while logged in AS the dedicated monitor user, from the cloned repo root
# (~/pokemon-monitor). It is idempotent — safe to re-run.
#
# Prereqs (see the runbook): the monitor user exists, you're logged into it,
# Google Chrome is installed (system-wide is fine), and this repo is cloned to
# ~/pokemon-monitor.
set -euo pipefail

REPO="$HOME/pokemon-monitor"
LABEL="com.pokemonmonitor"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_NUM="$(id -u)"

cd "$REPO"

echo ">>> [1/6] Building the virtualenv"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
# The browser adapters use real Google Chrome (channel="chrome"); this pulls
# Playwright's bundled Chromium as a fallback and installs the driver.
.venv/bin/playwright install chromium

echo ">>> [2/6] Checking secrets (config.json)"
if [ ! -f config.json ]; then
  cat <<EOF
!! config.json is missing — it holds your Discord webhook + settings and is
   gitignored, so it did NOT come with the clone. Copy it from your main
   account, then re-run this script. From YOUR main account's Terminal:

     sudo cp "/Users/northandunder/pokemon-monitor/config.json" "$REPO/"
     sudo chown "$(id -un)" "$REPO/config.json"

EOF
  exit 1
fi

echo ">>> [3/6] GitHub auth (for publishing the dashboard to gh-pages)"
if ! gh auth status >/dev/null 2>&1; then
  echo "    Not authenticated. Launching 'gh auth login' (choose HTTPS + your account)..."
  gh auth login
  gh auth setup-git
fi

echo ">>> [4/6] Setting up the gh-pages worktree (dashboard publish target)"
if [ ! -d "$HOME/.pokemon-monitor/pages/.git" ] && [ ! -f "$HOME/.pokemon-monitor/pages/.git" ]; then
  bash scripts/setup-pages.sh || echo "    (setup-pages.sh reported an issue — dashboard publish may need attention)"
else
  echo "    pages worktree already present — skipping"
fi

echo ">>> [5/6] Installing the LaunchAgent for this user ($USER, uid $UID_NUM)"
mkdir -p logs "$HOME/Library/LaunchAgents"
sed "s|__HOME__|$HOME|g" launchd/com.pokemonmonitor.template.plist > "$PLIST"
launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NUM" "$PLIST"
launchctl kickstart -k "gui/$UID_NUM/$LABEL"

echo ">>> [6/6] Verifying"
sleep 5
if launchctl list | grep -q "$LABEL"; then
  echo "    LaunchAgent loaded:"
  launchctl list | grep "$LABEL"
else
  echo "    !! NOT LOADED — check logs/monitor.err.log"
fi
echo ""
echo ">>> Done. The monitor now runs in THIS user's session."
echo "    Tail activity:  tail -f \"$REPO/logs/monitor.log\""
echo "    IMPORTANT: disable the monitor on your MAIN account so it does not"
echo "    run twice (double alerts + dashboard push conflicts). From the main"
echo "    account: launchctl bootout gui/\$(id -u)/$LABEL"
