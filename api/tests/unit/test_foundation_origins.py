"""APP_ORIGIN is an allow-list, and COOKIE_DOMAIN may only widen a cookie within it (ADR 0031).

The browser reaches the API on its own origin (`api.avodeck.com`) rather than through a rewrite on
the web app's origin, so the API must name every origin allowed to send it a credentialed request,
and the CSRF cookie must be readable by page script on the web app's origin. Both come from the
cell's environment: nothing here is written in code (rule 5).
"""

from __future__ import annotations

import pytest

from app.core.config import ConfigError, auth_settings

ENV = {
    "SUPABASE_URL": "https://zz.supabase.invalid",
    "SUPABASE_ANON_KEY": "anon",
    "SUPABASE_SERVICE_ROLE_KEY": "service",
    "APP_ORIGIN": "https://avodeck.com",
}


def test_one_origin_is_the_allow_list_and_the_origin_links_are_built_from() -> None:
    settings = auth_settings(ENV)
    assert settings.app_origins == ("https://avodeck.com",)
    assert settings.app_origin == "https://avodeck.com"
    assert settings.cookie_domain is None


def test_several_origins_are_allowed_and_the_first_builds_links() -> None:
    settings = auth_settings({**ENV, "APP_ORIGIN": "https://avodeck.com, https://staging.avodeck.com/"})
    assert settings.app_origins == ("https://avodeck.com", "https://staging.avodeck.com")
    assert settings.app_origin == "https://avodeck.com"


def test_a_wildcard_origin_is_refused() -> None:
    for value in ("*", "https://*.avodeck.com", "https://avodeck.com, *"):
        with pytest.raises(ConfigError):
            auth_settings({**ENV, "APP_ORIGIN": value})


def test_the_cookie_domain_is_read_and_a_leading_dot_is_dropped() -> None:
    settings = auth_settings({**ENV, "COOKIE_DOMAIN": ".avodeck.com"})
    assert settings.cookie_domain == "avodeck.com"


def test_the_cookie_domain_must_cover_every_allowed_origin() -> None:
    """Otherwise a cell could scope its cookie to a domain it does not own, or leave an origin unable
    to read it."""
    with pytest.raises(ConfigError):
        auth_settings({**ENV, "COOKIE_DOMAIN": "example.com"})
    with pytest.raises(ConfigError):
        auth_settings({**ENV, "APP_ORIGIN": "https://avodeck.com, https://other.test", "COOKIE_DOMAIN": "avodeck.com"})
    # A sibling host is covered by the parent domain.
    settings = auth_settings(
        {**ENV, "APP_ORIGIN": "https://avodeck.com, https://staging.avodeck.com", "COOKIE_DOMAIN": "avodeck.com"}
    )
    assert settings.cookie_domain == "avodeck.com"


def test_a_cookie_domain_without_a_dot_is_refused() -> None:
    with pytest.raises(ConfigError):
        auth_settings({**ENV, "APP_ORIGIN": "https://localhost", "COOKIE_DOMAIN": "localhost"})
