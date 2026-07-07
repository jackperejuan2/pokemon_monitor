from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from playwright.async_api import async_playwright

log = logging.getLogger("browser")

PROFILE_ROOT = Path.home() / ".pokemon-monitor"

# Markers must be specific to actual block/challenge pages. A bare "captcha"
# substring was too broad: real Pokemon Center product pages embed a legitimate
# i18n string ("reCaptchaError": "We encountered an issue with the page...") as
# normal content, which a raw match flagged as a challenge. Rely on
# Imperva/Cloudflare-specific markers instead of generic tokens like "captcha".
CHALLENGE_MARKERS = (
    "access denied",
    "_incapsula_",
    "pardon our interruption",
    "just a moment",
    "cf-challenge",
)

# One browser at a time: persistent Chromium profiles are locked per user-data-dir,
# and serializing keeps resource usage predictable.
# Created lazily so the Lock binds the running event loop (Python 3.9 binds eagerly
# at construction; a module-level Lock would bind the wrong loop under asyncio.run).
_BROWSER_LOCK: asyncio.Lock | None = None


def _browser_lock() -> asyncio.Lock:
    global _BROWSER_LOCK
    if _BROWSER_LOCK is None:
        _BROWSER_LOCK = asyncio.Lock()
    return _BROWSER_LOCK


def is_challenge_page(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in CHALLENGE_MARKERS)


async def _minimize_window(context, page) -> None:
    """Best-effort: minimize the browser window to the Dock so headed checks
    don't cover the user's screen.

    macOS clamps off-screen window positions back on-screen (a fully off-screen
    move leaves a ~40px sliver), so positioning can't hide the window. A
    *minimized* window leaves the screen entirely (into the Dock) while keeping
    document.visibilityState == "visible", so it doesn't read as a hidden/bot
    tab. Failure here must never break a check — a visible window beats a crash.
    """
    try:
        cdp = await context.new_cdp_session(page)
        win = await cdp.send("Browser.getWindowForTarget")
        await cdp.send(
            "Browser.setWindowBounds",
            {"windowId": win["windowId"], "bounds": {"windowState": "minimized"}},
        )
    except Exception as exc:  # best-effort; a visible window is fine, a crash is not
        log.debug("could not minimize browser window: %s", exc)


def build_launch_kwargs(
    headless: bool = False,
    channel: str | None = None,
    viewport: dict | None = None,
    locale: str = "en-CA",
) -> dict:
    kwargs = {
        "headless": headless,
        "viewport": viewport or {"width": 1280, "height": 900},
        "locale": locale,
    }
    if channel:
        # Use the real installed browser (e.g. Google Chrome) and reduce the
        # most obvious automation signals. Not a full evasion suite by design.
        #
        # Bot detection blocks *headless* Chrome, so we must run headed — but the
        # window need not be visible to the user. macOS clamps off-screen window
        # positions back on-screen, so instead we minimize the window to the Dock
        # right after it opens (see _minimize_window). A minimized window keeps
        # document.visibilityState == "visible" (no bot signal), but its renderer
        # can be throttled as occluded/backgrounded; these flags disable that so
        # the check still loads at full speed. --window-size sets the initial
        # bounds so the layout renders as expected before minimizing.
        vp = kwargs["viewport"]
        kwargs["channel"] = channel
        kwargs["args"] = [
            "--disable-blink-features=AutomationControlled",
            f"--window-size={vp['width']},{vp['height']}",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-background-timer-throttling",
        ]
        kwargs["ignore_default_args"] = ["--enable-automation"]
    return kwargs


async def _close_page(page) -> None:
    """Close a per-check tab, surviving cancellation.

    When monitor.py's asyncio.wait_for() times out, it cancels this coroutine
    mid-await. A bare `await page.close()` would then raise CancelledError before
    the tab actually closes, orphaning it (and its sockets) inside the long-lived
    context. Shielding lets the close finish in the background even though our
    await is interrupted, so the tab — and its connections — are always reclaimed.
    """
    try:
        await asyncio.shield(page.close())
    except Exception:  # closing a tab must never mask the real result/error
        pass


class BrowserManager:
    """Owns a single long-lived Playwright driver and one long-lived persistent
    browser context per profile.

    The old code launched a fresh Playwright driver *and* a fresh Chromium on
    every single check, then tore both down — so each check cold-started a
    browser, reloaded a heavy tracker-laden page, and opened dozens of outbound
    connections that never got reused across checks. That churn piled up sockets
    in TIME_WAIT until the machine ran out of ephemeral ports.

    Here the driver starts once and each profile's persistent context is created
    lazily and kept alive for the process lifetime. Each check opens a throwaway
    tab in the existing (warm) context and closes it afterwards, so Chrome's
    connection pool / keep-alive is reused across checks and nothing leaks.
    Per-retailer profiles still give cookie/session isolation, exactly as before.
    """

    def __init__(self) -> None:
        self._pw = None
        self._contexts = {}  # profile name -> live persistent BrowserContext

    async def _ensure_pw(self):
        if self._pw is None:
            self._pw = await async_playwright().start()
        return self._pw

    async def _launch_context(self, profile: str, headless: bool, channel: str | None):
        profile_dir = PROFILE_ROOT / profile
        profile_dir.mkdir(parents=True, exist_ok=True)
        pw = await self._ensure_pw()
        context = await pw.chromium.launch_persistent_context(
            str(profile_dir), **build_launch_kwargs(headless=headless, channel=channel)
        )
        # If the browser dies (crash, user quit, profile-lock loss), drop it from
        # the cache so the next check relaunches instead of using a dead handle.
        context.once("close", lambda *_: self._contexts.pop(profile, None))
        # launch_persistent_context opens with one blank page/window. Keep it
        # alive for the process lifetime (closing the last page closes the whole
        # context) and minimize it once; per-check tabs are opened/closed on top.
        keep = context.pages[0] if context.pages else await context.new_page()
        if not headless:
            await _minimize_window(context, keep)
        self._contexts[profile] = context
        return context

    async def _acquire_page(self, profile: str, headless: bool, channel: str | None):
        """Return (context, fresh page), relaunching once if the cached context
        has died out from under us."""
        for attempt in (0, 1):
            context = self._contexts.get(profile)
            if context is None:
                context = await self._launch_context(profile, headless, channel)
            try:
                return context, await context.new_page()
            except Exception:
                # Cached context is dead/unusable; discard and retry once.
                self._contexts.pop(profile, None)
                try:
                    await context.close()
                except Exception:
                    pass
                if attempt == 1:
                    raise
        raise RuntimeError("unreachable")

    async def fetch(
        self,
        url: str,
        profile: str,
        settle_ms: int = 5_000,
        headless: bool = False,
        channel: str | None = None,
    ) -> str:
        """Load `url` in the warm per-profile context and return rendered HTML."""
        async with _browser_lock():
            _context, page = await self._acquire_page(profile, headless, channel)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(settle_ms)
                return await page.content()
            finally:
                await _close_page(page)

    async def fetch_with_challenge_retry(
        self,
        url: str,
        profile: str,
        is_challenge,
        settle_ms: int = 5_000,
        retries: int = 2,
        retry_wait_ms: int = 10_000,
        headless: bool = False,
        channel: str | None = None,
    ) -> str:
        """Like fetch(), but if the settled page still looks like a challenge,
        keep the same tab/session open and wait+re-read up to `retries` more
        times before giving up. Returns the last HTML read."""
        async with _browser_lock():
            _context, page = await self._acquire_page(profile, headless, channel)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(settle_ms)
                html = await page.content()
                attempt = 0
                while is_challenge(html) and attempt < retries:
                    await page.wait_for_timeout(retry_wait_ms)
                    html = await page.content()
                    attempt += 1
                return html
            finally:
                await _close_page(page)

    async def aclose(self) -> None:
        """Close every live context and stop the Playwright driver. Idempotent
        and exception-safe so it can run from a finally block or signal handler."""
        for profile, context in list(self._contexts.items()):
            try:
                await context.close()
            except Exception as exc:
                log.debug("error closing context %s: %s", profile, exc)
        self._contexts.clear()
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception as exc:
                log.debug("error stopping playwright: %s", exc)
            self._pw = None


# Process-wide singleton. Adapters call the module-level helpers below, so their
# call sites are unchanged; monitor.py drives startup implicitly (lazy) and
# shutdown explicitly via shutdown_browser().
_MANAGER = BrowserManager()


async def fetch_page_html(
    url: str,
    profile: str,
    settle_ms: int = 5_000,
    headless: bool = False,
    channel: str | None = None,
) -> str:
    return await _MANAGER.fetch(
        url, profile, settle_ms=settle_ms, headless=headless, channel=channel
    )


async def fetch_page_html_with_challenge_retry(
    url: str,
    profile: str,
    is_challenge,
    settle_ms: int = 5_000,
    retries: int = 2,
    retry_wait_ms: int = 10_000,
    headless: bool = False,
    channel: str | None = None,
) -> str:
    return await _MANAGER.fetch_with_challenge_retry(
        url,
        profile,
        is_challenge,
        settle_ms=settle_ms,
        retries=retries,
        retry_wait_ms=retry_wait_ms,
        headless=headless,
        channel=channel,
    )


async def shutdown_browser() -> None:
    """Close the shared browser(s) and stop the Playwright driver. Safe to call
    more than once and even if nothing was ever launched."""
    await _MANAGER.aclose()
