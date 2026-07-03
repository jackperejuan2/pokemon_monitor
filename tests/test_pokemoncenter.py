import pytest

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
