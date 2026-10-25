"""Email carries checked parameters only; `help.request` alone may carry free text (docs/decisions/0022)."""

from __future__ import annotations

from app.core.email import HELP_REQUEST, TEMPLATE_PARAMS

SAMPLE = "Mrs Zed Quillfeather, born 01/02/1960, cannot open her pages."


def test_only_the_help_request_template_accepts_free_text() -> None:
    free = {
        (template, name)
        for template, checks in TEMPLATE_PARAMS.items()
        for name, check in checks.items()
        if check(SAMPLE)
    }
    assert free == {(HELP_REQUEST, "message")}


def test_the_help_request_template_is_sent_only_to_our_own_address_by_its_route() -> None:
    import inspect

    from app.core.routes import help as route

    source = inspect.getsource(route)
    assert "support_email()" in source and "body.to," not in source and "to=body" not in source
