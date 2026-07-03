import asyncio

import pytest

import adapters.pokemoncenter
from adapters.pokemoncenter import is_challenge_page


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
    lock = adapters.pokemoncenter._BROWSER_LOCK
    assert isinstance(lock, asyncio.Lock)

    async def holds_and_blocks_second_acquire():
        async with lock:
            assert lock.locked()
            # A second acquire must not complete while the lock is held.
            waiter = asyncio.ensure_future(lock.acquire())
            await asyncio.sleep(0)
            assert not waiter.done()
            waiter.cancel()
            try:
                await waiter
            except asyncio.CancelledError:
                pass
        assert not lock.locked()

    asyncio.run(holds_and_blocks_second_acquire())
