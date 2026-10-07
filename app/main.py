"""Création de l'application FastAPI, montage des routers et des fichiers statiques."""

import logging

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import APP_NAME, BASE_DIR, get_settings
from app.routes import (
    api,
    athena,
    compare,
    export,
    help,
    imports,
    mapping,
    quality,
    releases,
    search,
    settings,
)


def create_app() -> FastAPI:
    logging.basicConfig(
        level=get_settings().log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s : %(message)s",
    )
    application = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None, openapi_url="/api/openapi.json")
    application.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
    for router in (
        search.router,
        mapping.router,
        imports.router,
        releases.router,
        export.router,
        compare.router,
        athena.router,
        quality.router,
        settings.router,
        api.router,
        help.router,
    ):
        application.include_router(router)
    application.add_api_route(
        "/favicon.ico",
        lambda: RedirectResponse("/static/img/favicon.svg", status_code=301),
        include_in_schema=False,
    )
    return application


app = create_app()
