"""App factory. Routers and middleware are added by the foundation steps that follow."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    return FastAPI(title="Avodeck API")


app = create_app()
