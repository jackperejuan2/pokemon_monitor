#!/usr/bin/env bash
# Pause the restock monitor: stop its launchd job so it runs no checks and pops
# no Chrome windows -- e.g. while you're sitting in a purchase queue, where the
# monitor's headed-Chrome checks can compete for the session and cost you your
# spot. This is a hard stop (bootout), so KeepAlive will NOT respawn it. Bring
# it back with scripts/resume-monitor.sh.
#
# Run it AS the account the monitor runs under (its own GUI domain -> no sudo).
# If you run it from another admin account, it targets the monitor account and
# uses sudo (you'll be prompted for a password).
set -euo pipefail

LABEL="com.pokemonmonitor"
MONITOR_USER="${MONITOR_USER:-pmonitor}"

if [[ "$(id -un)" == "$MONITOR_USER" ]]; then
  TARGET_UID="$(id -u)"; SUDO=""            # our own GUI domain -> no sudo
else
  TARGET_UID="$(id -u "$MONITOR_USER" 2>/dev/null || true)"
  if [[ -z "$TARGET_UID" ]]; then
    echo "user '$MONITOR_USER' does not exist. set MONITOR_USER=<account>." >&2
    exit 1
  fi
  SUDO="sudo"                               # acting on another user's domain
fi

echo "pausing $LABEL (gui/$TARGET_UID)..."
$SUDO launchctl bootout "gui/$TARGET_UID/$LABEL" 2>/dev/null \
  && echo "booted out." \
  || echo "already stopped (nothing loaded)."

sleep 1
if pgrep -f "pokemon-monitor/monitor.py" >/dev/null; then
  echo "WARNING: a monitor.py process is still running:" >&2
  pgrep -fl "pokemon-monitor/monitor.py" >&2
  exit 1
fi
echo "paused. no monitor process running -- Chrome windows will stay closed."
echo "resume with: scripts/resume-monitor.sh"
