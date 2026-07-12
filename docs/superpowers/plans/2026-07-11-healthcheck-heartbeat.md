# Dead-man's-switch Heartbeat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ping an external heartbeat URL once per monitor loop so a dead-man's-switch service alerts the user when the monitor goes silent (crash / hang / Mac asleep / reboot-no-resume).

**Architecture:** A fail-safe `ping_healthcheck(client, url)` helper in `monitor.py`, called once per `run()` loop iteration right after the config reload; opt-in via a `healthcheck_url` config key, reusing the shared httpx client.

**Tech Stack:** Python 3.9 (`from __future__ import annotations`), pytest, asyncio, httpx.

---

## File Structure

- `monitor.py` — add `ping_healthcheck` helper + one call in `run()`.
- `tests/test_monitor_helpers.py` — helper unit tests.

---

## Task 1: `ping_healthcheck` helper

**Files:**
- Modify: `monitor.py`
- Test: `tests/test_monitor_helpers.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_monitor_helpers.py` (`asyncio` is already imported there):

```python
def test_ping_healthcheck_noop_on_empty_url():
    import monitor

    calls = []

    class Client:
        async def get(self, url, **kw):
            calls.append(url)

    asyncio.run(monitor.ping_healthcheck(Client(), None))
    asyncio.run(monitor.ping_healthcheck(Client(), ""))
    assert calls == []  # falsy url -> no request


def test_ping_healthcheck_gets_url():
    import monitor

    calls = []

    class Client:
        async def get(self, url, **kw):
            calls.append(url)

    asyncio.run(monitor.ping_healthcheck(Client(), "https://hc-ping.com/abc"))
    assert calls == ["https://hc-ping.com/abc"]


def test_ping_healthcheck_swallows_errors():
    import monitor

    class Client:
        async def get(self, url, **kw):
            raise RuntimeError("network down")

    # must NOT raise
    asyncio.run(monitor.ping_healthcheck(Client(), "https://hc-ping.com/abc"))
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_monitor_helpers.py -q -k ping_healthcheck`
Expected: FAIL — `AttributeError: module 'monitor' has no attribute 'ping_healthcheck'`.

- [ ] **Step 3: Implement in `monitor.py`**

Add near the other module-level helpers (e.g. just after `_config_float`):

```python
async def ping_healthcheck(client, url) -> None:
    """Best-effort liveness ping to an external dead-man's-switch (Healthchecks.io
    etc.). No-op when `url` is falsy. Never raises and must not meaningfully block
    — a failed ping must not disrupt checks; the whole point is that the EXTERNAL
    service notices the silence."""
    if not url:
        return
    try:
        await client.get(url, timeout=10)
    except Exception as exc:
        log.debug("healthcheck ping failed: %s", exc)
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_monitor_helpers.py -q -k ping_healthcheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add monitor.py tests/test_monitor_helpers.py
git commit -m "feat: ping_healthcheck liveness helper"
```

---

## Task 2: Wire the ping into the `run()` loop

**Files:**
- Modify: `monitor.py` (`run()`)

- [ ] **Step 1: Add the call**

In `run()`, find the config hot-reload line inside the `while not stop.is_set():` loop:

```python
                config = safe_reload(load_config, config, "config.json")
```

Add the heartbeat ping on the next line (same indentation):

```python
                config = safe_reload(load_config, config, "config.json")
                await ping_healthcheck(client, config.get("healthcheck_url"))
```

(`client` is the shared `httpx.AsyncClient` in scope for the loop.)

- [ ] **Step 2: Sanity-check it parses and the suite passes**

Run: `.venv/bin/python -c "import ast; ast.parse(open('monitor.py').read()); print('parses')"`
Run: `.venv/bin/python -m pytest tests/ -q`
Expected: `parses`, then all tests PASS (no behavior change when `healthcheck_url` is unset — `ping_healthcheck` no-ops).

- [ ] **Step 3: Commit**

```bash
git add monitor.py
git commit -m "feat: send a liveness heartbeat ping each loop"
```

---

## Deployment / setup (post-merge, on `pmonitor`)

1. Create a free [Healthchecks.io](https://healthchecks.io) check. Set its **period** and **grace** generously (e.g. period 1h, grace 30m) and attach a notification channel (email / SMS / push / Discord).
2. Add the check's ping URL to `pmonitor`'s `config.json`:
   ```json
   "healthcheck_url": "https://hc-ping.com/<your-uuid>"
   ```
   (Config hot-reloads, but the new *code* needs a restart.)
3. Deploy: `bash /Users/Shared/pmonitor-deploy.sh`.
4. Confirm on Healthchecks that the check flips to "up" within a couple minutes.

---

## Self-Review

- **Spec coverage:** helper (`ping_healthcheck`) → Task 1; loop wiring → Task 2; opt-in `healthcheck_url` config → Task 2 (`config.get`); fail-safe/no-op/GET behaviors → Task 1 tests; setup → Deployment section. All spec sections covered.
- **Type consistency:** `ping_healthcheck(client, url) -> None` used identically in the helper, tests, and the `run()` call.
- **Placeholders:** none — every step has concrete code/commands. (No unit test drives the infinite `run()` loop directly; the one-line wiring is verified by parse + full-suite green, and the helper logic is fully unit-tested — a reasonable boundary for a single fail-safe call.)
