import asyncio

import pytest

from adapters.browser import _browser_lock as browser_module_lock
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
