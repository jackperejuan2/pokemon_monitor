import asyncio

import pytest

from adapters.pokemoncenter import _browser_lock, is_challenge_page


@pytest.mark.parametrize(
    "html",
    [
        "<html><body>Access Denied</body></html>",
        "<html><script src='/_Incapsula_Resource?x=1'></script></html>",
        "<html><body>Pardon Our Interruption</body></html>",
    ],
)
def test_detects_challenge_pages(html):
    assert is_challenge_page(html)


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
