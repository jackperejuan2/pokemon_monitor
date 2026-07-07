"""Lifecycle guarantees for the shared browser: launch once per profile, reuse
across every check (this is the fix for the socket/port-exhaustion leak), close
per-check tabs, relaunch a dead context, and shut everything down cleanly."""
import asyncio

import pytest

import adapters.browser as browser


class FakePage:
    def __init__(self):
        self.closed = False
        self.goto_calls = 0

    async def goto(self, *a, **k):
        self.goto_calls += 1

    async def wait_for_timeout(self, ms):
        pass

    async def content(self):
        return "<html>ok</html>"

    async def close(self):
        self.closed = True


class FakeContext:
    def __init__(self):
        self.pages = [FakePage()]  # persistent context opens with one blank page
        self.closed = False
        self.new_page_calls = 0
        self._handlers = {}
        self.fail_new_page = False

    def once(self, event, cb):
        self._handlers[event] = cb

    async def new_page(self):
        if self.fail_new_page:
            raise RuntimeError("target closed")
        self.new_page_calls += 1
        page = FakePage()
        self.pages.append(page)
        return page

    async def close(self):
        self.closed = True
        cb = self._handlers.get("close")
        if cb:
            cb(self)


class FakeChromium:
    def __init__(self):
        self.launches = 0
        self.contexts = []

    async def launch_persistent_context(self, profile_dir, **kwargs):
        self.launches += 1
        ctx = FakeContext()
        self.contexts.append(ctx)
        return ctx


class FakePlaywright:
    def __init__(self):
        self.chromium = FakeChromium()
        self.stopped = False

    async def stop(self):
        self.stopped = True


class FakePlaywrightFactory:
    """Stands in for async_playwright(): calling it returns an object whose
    start() yields the (single) fake Playwright driver."""
    def __init__(self):
        self.pw = FakePlaywright()
        self.start_calls = 0

    def __call__(self):
        return self

    async def start(self):
        self.start_calls += 1
        return self.pw


@pytest.fixture
def fake_pw(monkeypatch, tmp_path):
    factory = FakePlaywrightFactory()
    monkeypatch.setattr(browser, "async_playwright", factory)
    monkeypatch.setattr(browser, "PROFILE_ROOT", tmp_path)
    monkeypatch.setattr(browser, "_BROWSER_LOCK", None)  # rebind lock to this loop
    return factory


def test_same_profile_launches_browser_once(fake_pw):
    mgr = browser.BrowserManager()

    async def main():
        for _ in range(5):
            html = await mgr.fetch("https://x", profile="p", headless=True)
            assert html == "<html>ok</html>"
        await mgr.aclose()

    asyncio.run(main())
    chromium = fake_pw.pw.chromium
    # The whole point: 5 checks, ONE browser launch, ONE driver start.
    assert chromium.launches == 1
    assert fake_pw.start_calls == 1
    ctx = chromium.contexts[0]
    # Each check opened and closed its own throwaway tab.
    assert ctx.new_page_calls == 5
    assert all(p.closed for p in ctx.pages[1:])
    # Shutdown closed the context and stopped the driver.
    assert ctx.closed is True
    assert fake_pw.pw.stopped is True


def test_distinct_profiles_get_distinct_contexts(fake_pw):
    mgr = browser.BrowserManager()

    async def main():
        await mgr.fetch("https://a", profile="walmart", headless=True)
        await mgr.fetch("https://b", profile="pc", headless=True)
        await mgr.fetch("https://a", profile="walmart", headless=True)
        await mgr.aclose()

    asyncio.run(main())
    # Two profiles -> two contexts; the repeat walmart check reuses the first.
    assert fake_pw.pw.chromium.launches == 2


def test_dead_context_is_relaunched(fake_pw):
    mgr = browser.BrowserManager()

    async def main():
        await mgr.fetch("https://x", profile="p", headless=True)
        # Simulate the browser dying: the next new_page() on the cached context
        # raises, so the manager must discard it and relaunch exactly once.
        fake_pw.pw.chromium.contexts[0].fail_new_page = True
        html = await mgr.fetch("https://x", profile="p", headless=True)
        assert html == "<html>ok</html>"
        await mgr.aclose()

    asyncio.run(main())
    assert fake_pw.pw.chromium.launches == 2


def test_shutdown_is_idempotent_and_safe_when_unused(fake_pw):
    mgr = browser.BrowserManager()

    async def main():
        await mgr.aclose()  # nothing launched yet
        await mgr.fetch("https://x", profile="p", headless=True)
        await mgr.aclose()
        await mgr.aclose()  # second close must not raise

    asyncio.run(main())
    assert fake_pw.pw.chromium.contexts[0].closed is True
