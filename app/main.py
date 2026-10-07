"""Création de l'application FastAPI, montage des routers et des fichiers statiques."""

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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

logger = logging.getLogger(__name__)


def _warm_up() -> None:
    """Précalcule la recherche par défaut pour que la première visite soit immédiate."""
    from app.db import SessionLocal
    from app.services import search_service

    session = SessionLocal()
    try:
        search_service.warm_up(session)
    except Exception:  # le préchauffage ne doit jamais empêcher le démarrage
        logger.warning("Préchauffage de la recherche impossible", exc_info=True)
    finally:
        session.close()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if get_settings().warm_up_search:
        threading.Thread(target=_warm_up, name="warm-up-search", daemon=True).start()
    yield


def create_app() -> FastAPI:
    logging.basicConfig(
        level=get_settings().log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s : %(message)s",
    )
    application = FastAPI(
        title=APP_NAME,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
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
