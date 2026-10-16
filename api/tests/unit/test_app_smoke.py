from fastapi import FastAPI


def test_app_main_exposes_a_fastapi_app() -> None:
    from app.main import app

    assert isinstance(app, FastAPI)
