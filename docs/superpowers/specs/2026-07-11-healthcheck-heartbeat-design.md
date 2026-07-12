# Dead-man's-switch Heartbeat — design

**Date:** 2026-07-11
**Status:** Approved design, pending implementation

## Problem

The monitor is well-instrumented for *its own* errors (retailer/product health,
socket canary, daily heartbeat — all to Discord) and `launchd KeepAlive`
restarts a crashed process. But nothing detects the monitor **going silent**:
a hung process, the Mac asleep, or (a known gap) `pmonitor`'s session not
auto-resuming after a reboot. In all of those, the monitor can't alert you —
it's dead — and silence looks identical to "no restocks," so a missed drop goes
unnoticed. The fix is an **external** watcher that alerts on *absence* of a
liveness signal.

## Goal

A liveness-only "dead-man's-switch": the monitor pings an external heartbeat URL
(Healthchecks.io or any equivalent) once per loop iteration; the external
service alerts the user (email/SMS/push) when it stops hearing pings. No health
semantics — retailer/product health stays on Discord.

## Design

### 1. Ping helper — `ping_healthcheck(client, url)` in `monitor.py`

```python
async def ping_healthcheck(client, url) -> None:
    """Best-effort liveness ping to an external dead-man's-switch (Healthchecks.io
    etc.). No-op when `url` is falsy. Never raises and must not meaningfully block:
    a failed ping must not disrupt checks, and the whole point is that the
    EXTERNAL service notices the silence."""
    if not url:
        return
    try:
        await client.get(url, timeout=10)
    except Exception as exc:
        log.debug("healthcheck ping failed: %s", exc)
```

- Reuses the existing shared `httpx.AsyncClient` (line ~512 in `run()`).
- 10s per-request timeout so a slow/hanging endpoint can't stall the loop for
  long; all exceptions swallowed to `log.debug`.

### 2. Wiring — one call in the `run()` loop

Immediately after the config hot-reload (`config = safe_reload(load_config, ...)`,
~line 517), add:

```python
                await ping_healthcheck(client, config.get("healthcheck_url"))
```

Fires **every iteration**, before quiet-hours/heartbeat/product logic, so it
purely signals "the process is alive and looping." Because `healthcheck_url` is
read from the hot-reloaded `config`, enabling/disabling needs no restart.

### 3. Config (opt-in, per-deployment)

A single key in `pmonitor`'s (gitignored) `config.json`:
`"healthcheck_url": "https://hc-ping.com/<uuid>"`. Absent/empty → `ping_healthcheck`
no-ops, feature off. No new dependency; no committed config change.

### 4. Cadence / operator note

The loop iterates every ~5s (active) to ~60s (quiet); a full cycle with
serialized browser checks can take a few minutes. So on the Healthchecks side,
set a **generous period + grace** (e.g. period 1h / grace 30m) so transient
slowness never false-alarms. A real death (crash, hang, Mac asleep/rebooted)
stops pings entirely → the service alerts.

## Testing

Unit tests for `ping_healthcheck` (a fake async client):
- falsy `url` (None / "") → `client.get` is NOT called, returns without error.
- `url` set → `client.get(url, ...)` IS called with that url.
- `client.get` raising → the exception is swallowed (helper never raises).

## Setup (one-time, documented in deploy notes)

1. Create a free Healthchecks.io check; set its **period + grace** and a
   notification channel (email / SMS / push / Discord).
2. Put the check's ping URL into `pmonitor`'s `config.json` as `healthcheck_url`.
3. Deploy (`bash /Users/Shared/pmonitor-deploy.sh`) — restart picks up the new
   code; the config hot-reloads.

## Non-goals

- No `/fail` or `/start` pings — liveness only (health stays on Discord).
- No new dependency (uses the existing shared httpx client).
- No changes to the existing Discord heartbeat / health alerts.
- Not a local watcher — must be an EXTERNAL service (a local watcher dies with
  the Mac and can't detect Mac-down).

## Where things live

- Committed (PR): `monitor.py` (`ping_healthcheck` + one call), tests.
- Per-deployment (`pmonitor` `config.json`): `healthcheck_url`.
