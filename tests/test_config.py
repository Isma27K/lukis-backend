import pytest

from utils.config import Settings


@pytest.mark.parametrize(
    ("origin", "secure"),
    [
        ("https://lukis.example.com", True),
        ("http://localhost:3000", False),
        ("http://192.168.1.20:3000", False),
        # The first origin is the public address; it decides.
        ("https://lukis.example.com,http://localhost:3000", True),
    ],
)
def test_secure_cookies_follow_the_public_address(origin, secure):
    settings = Settings(_env_file=None, frontend_origin=origin)

    assert settings.secure_cookies is secure


@pytest.mark.parametrize("explicit", [True, False])
def test_cookie_secure_overrides_the_guess(explicit):
    settings = Settings(
        _env_file=None,
        frontend_origin="https://lukis.example.com",
        cookie_secure=explicit,
    )

    assert settings.secure_cookies is explicit
