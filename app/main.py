"""Création de l'application FastAPI, montage des routers et des fichiers statiques."""

import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import APP_NAME, BASE_DIR, get_settings
from app.routes import api, imports, mapping, quality, releases, search, settings


def create_app() -> FastAPI:
    logging.basicConfig(
        level=get_settings().log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s : %(message)s",
    )
    application = FastAPI(
        title=APP_NAME, docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json"
    )
    application.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
    for router in (
        search.router,
        mapping.router,
        imports.router,
        releases.router,
        quality.router,
        settings.router,
        api.router,
    ):
        application.include_router(router)
    return application


app = create_app()
