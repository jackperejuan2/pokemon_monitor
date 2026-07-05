import asyncio

import pytest

from adapters.browser import _browser_lock as browser_module_lock
from adapters.browser import build_launch_kwargs
from adapters.browser import is_challenge_page as browser_module_is_challenge_page
from adapters.pokemoncenter import _browser_lock, is_challenge_page


@pytest.mark.parametrize(
    "html",
    [
        "<html><body>Access Denied</body></html>",
        "<html><script src='/_Incapsula_Resource?x=1'></script></html>",
        "<html><body>Pardon Our Interruption</body></html>",
        "<html><body>Just a moment...</body></html>",
        "<html><body>cf-challenge running</body></html>",
    ],
)
def test_detects_challenge_pages(html):
    assert is_challenge_page(html)


def test_is_challenge_page_importable_from_browser_module():
    assert browser_module_is_challenge_page("<html>Access Denied</html>")
    assert browser_module_is_challenge_page is is_challenge_page


def test_browser_lock_importable_from_browser_module():
    assert browser_module_lock is _browser_lock


def test_normal_page_is_not_challenge():
    assert not is_challenge_page("<html><body>Pokemon TCG: Add to Cart $64.99</body></html>")


# Live testing (2026-07-04) found this legitimate i18n string embedded in real
# Pokemon Center product pages. A bare "captcha" substring match flagged it as a
# challenge even though the product title/price/availability parsed fine.
POKEMONCENTER_RECAPTCHA_ERROR_STRING = (
    '<html><body>'
    '<script>window.__NEXT_DATA__ = {"props":{"messages":{'
    '"reCaptchaError":"We encountered an issue with the page you have requested. '
    'This could be a browser issue or a temporary problem."}}}</script>'
    'Pokemon TCG: Chaos Rising Booster Bundle - Add to Cart $64.99'
    '</body></html>'
)


def test_real_recaptcha_error_i18n_string_is_not_challenge():
    # Regression: the embedded "reCaptchaError" i18n string is normal page
    # content, not an actual CAPTCHA challenge, and must not be flagged.
    assert not is_challenge_page(POKEMONCENTER_RECAPTCHA_ERROR_STRING)
    assert not browser_module_is_challenge_page(POKEMONCENTER_RECAPTCHA_ERROR_STRING)


def test_imperva_access_denied_incident_snippet_is_challenge():
    # Real Imperva/Incapsula block page body.
    html = "Access denied Error 15 ... Incident ID"
    assert is_challenge_page(html)


def test_imperva_incapsula_resource_snippet_is_challenge():
    html = "some page containing _Incapsula_Resource somewhere in the body"
    assert is_challenge_page(html)


def test_concurrent_checks_serialize_on_browser_lock():
    order = []

    async def critical_section(name):
        async with _browser_lock():
            order.append(f"{name}:enter")
            await asyncio.sleep(0.01)
            order.append(f"{name}:exit")

    async def main():
        lock = _browser_lock()
        assert isinstance(lock, asyncio.Lock)
        assert _browser_lock() is lock  # singleton
        await asyncio.gather(critical_section("a"), critical_section("b"))

    asyncio.run(main())
    assert order == ["a:enter", "a:exit", "b:enter", "b:exit"]


def test_build_launch_kwargs_default():
    kwargs = build_launch_kwargs()
    assert kwargs["headless"] is False
    assert "channel" not in kwargs
    assert "args" not in kwargs
    assert "ignore_default_args" not in kwargs
    assert kwargs["viewport"] == {"width": 1280, "height": 900}
    assert kwargs["locale"] == "en-CA"


def test_build_launch_kwargs_headless():
    kwargs = build_launch_kwargs(headless=True)
    assert kwargs["headless"] is True


def test_build_launch_kwargs_channel_chrome():
    kwargs = build_launch_kwargs(channel="chrome")
    assert kwargs["channel"] == "chrome"
    assert "--disable-blink-features=AutomationControlled" in kwargs["args"]
    assert "--enable-automation" in kwargs["ignore_default_args"]


def test_build_launch_kwargs_channel_parks_window_offscreen():
    # Real headed Chrome is required (headless is blocked), but the window does
    # not need to be visible to the user. Park it far off-screen so checks run
    # without popping windows in front of the user's work.
    kwargs = build_launch_kwargs(channel="chrome")
    assert "--window-position=-3000,-3000" in kwargs["args"]
    assert "--window-size=1280,900" in kwargs["args"]


def test_build_launch_kwargs_offscreen_matches_viewport():
    kwargs = build_launch_kwargs(channel="chrome", viewport={"width": 800, "height": 600})
    assert "--window-size=800,600" in kwargs["args"]


def test_build_launch_kwargs_no_channel_has_no_window_args():
    # Off-screen positioning only applies to the real-browser channel path;
    # the default headless/httpx path must be untouched.
    kwargs = build_launch_kwargs()
    assert "args" not in kwargs
