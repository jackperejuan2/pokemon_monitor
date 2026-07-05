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


async def fetch_page_html(
    url: str,
    profile: str,
    settle_ms: int = 5_000,
    headless: bool = False,
    channel: str | None = None,
) -> str:
    """Load `url` in a real Chromium (or, if `channel` is given, that browser
    channel) with a persistent per-profile context and return the rendered
    HTML."""
    profile_dir = PROFILE_ROOT / profile
    profile_dir.mkdir(parents=True, exist_ok=True)
    async with _browser_lock():
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                str(profile_dir),
                **build_launch_kwargs(headless=headless, channel=channel),
            )
            try:
                page = await context.new_page()
                if not headless:
                    await _minimize_window(context, page)
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(settle_ms)
                return await page.content()
            finally:
                await context.close()


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
    """Like fetch_page_html, but if the initial settle still looks like a
    challenge page, keep the same page/session open and wait+re-read up to
    `retries` more times before giving up. Returns the last HTML read
    (challenge or not)."""
    profile_dir = PROFILE_ROOT / profile
    profile_dir.mkdir(parents=True, exist_ok=True)
    async with _browser_lock():
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                str(profile_dir),
                **build_launch_kwargs(headless=headless, channel=channel),
            )
            try:
                page = await context.new_page()
                if not headless:
                    await _minimize_window(context, page)
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
                await context.close()
