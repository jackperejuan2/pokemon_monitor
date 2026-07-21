#!/usr/bin/env bash
# Resume the restock monitor after scripts/pause-monitor.sh: reload its launchd
# job so checks (and the daily heartbeat) start again.
#
# Run it AS the account the monitor runs under (its own GUI domain -> no sudo).
# If you run it from another admin account, it targets the monitor account and
# uses sudo (you'll be prompted for a password).
set -euo pipefail

LABEL="com.pokemonmonitor"
MONITOR_USER="${MONITOR_USER:-pmonitor}"

if [[ "$(id -un)" == "$MONITOR_USER" ]] || [[ -z "${MONITOR_USER}" ]]; then
  TARGET_UID="$(id -u)"; TARGET_HOME="$HOME"; SUDO=""
else
  TARGET_UID="$(id -u "$MONITOR_USER" 2>/dev/null || true)"
  if [[ -z "$TARGET_UID" ]]; then
    echo "user '$MONITOR_USER' does not exist. set MONITOR_USER=<account>." >&2
    exit 1
  fi
  TARGET_HOME="$(dscl . -read "/Users/$MONITOR_USER" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
  TARGET_HOME="${TARGET_HOME:-/Users/$MONITOR_USER}"
  SUDO="sudo"
fi

PLIST="$TARGET_HOME/Library/LaunchAgents/$LABEL.plist"
if [[ ! -f "$PLIST" ]]; then
  echo "launchd plist not found at $PLIST" >&2
  echo "the monitor may not be installed for '$MONITOR_USER' (see scripts/setup-monitor-user.sh)." >&2
  exit 1
fi

echo "resuming $LABEL (gui/$TARGET_UID)..."
$SUDO launchctl bootstrap "gui/$TARGET_UID" "$PLIST" 2>/dev/null \
  || echo "already loaded (bootstrap skipped)."
$SUDO launchctl kickstart -k "gui/$TARGET_UID/$LABEL" 2>/dev/null || true

sleep 1
if pgrep -f "pokemon-monitor/monitor.py" >/dev/null; then
  echo "resumed. monitor is running:"
  pgrep -fl "pokemon-monitor/monitor.py"
else
  echo "WARNING: bootstrap succeeded but no monitor.py process is visible yet." >&2
  echo "check: launchctl print gui/$TARGET_UID/$LABEL  and  tail -5 $TARGET_HOME/pokemon-monitor/logs/monitor.err.log" >&2
  exit 1
fi
