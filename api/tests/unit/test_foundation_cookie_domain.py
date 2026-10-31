"""The CSRF cookie is widened to the web app's domain; the session cookie never is (ADR 0031).

With the API on its own host, page script on the web app's origin must still be able to read the
CSRF token to send it back in `X-CSRF-Token` (the double-submit check). A host-only cookie set by
`api.avodeck.com` is invisible to `avodeck.com`, so that one cookie carries `Domain`. The session
cookie stays host-only: it is `httpOnly`, nothing reads it, and the narrower the better. Both hosts
are the same site, so `SameSite=Strict` is still sent either way.
"""

from __future__ import annotations

from fastapi import Response

from app.core.auth.session import CSRF_COOKIE, SESSION_COOKIE, NewTokens, clear_cookies, set_cookies

TOKENS = NewTokens("session-token", "csrf-token")


def _set_cookie_lines(response: Response) -> dict[str, str]:
    lines = response.headers.getlist("set-cookie")
    return {line.split("=", 1)[0]: line.lower() for line in lines}


def test_without_a_cookie_domain_both_cookies_stay_host_only() -> None:
    response = Response()
    set_cookies(response, TOKENS)
    lines = _set_cookie_lines(response)
    assert "domain" not in lines[SESSION_COOKIE]
    assert "domain" not in lines[CSRF_COOKIE]


def test_the_cookie_domain_widens_the_csrf_cookie_only() -> None:
    response = Response()
    set_cookies(response, TOKENS, domain="avodeck.com")
    lines = _set_cookie_lines(response)
    assert "domain=avodeck.com" in lines[CSRF_COOKIE]
    assert "domain" not in lines[SESSION_COOKIE], "the session cookie must stay host-only"
    # Everything else is unchanged: same site, secure, and only the session cookie is httpOnly.
    assert "samesite=strict" in lines[SESSION_COOKIE] and "samesite=strict" in lines[CSRF_COOKIE]
    assert "secure" in lines[SESSION_COOKIE] and "secure" in lines[CSRF_COOKIE]
    assert "httponly" in lines[SESSION_COOKIE] and "httponly" not in lines[CSRF_COOKIE]


def test_signing_out_clears_the_csrf_cookie_on_the_same_domain() -> None:
    """A cookie is only deleted by a `Set-Cookie` matching its domain, so sign-out must send it too."""
    response = Response()
    clear_cookies(response, domain="avodeck.com")
    lines = _set_cookie_lines(response)
    assert "domain=avodeck.com" in lines[CSRF_COOKIE]
    assert "domain" not in lines[SESSION_COOKIE]
