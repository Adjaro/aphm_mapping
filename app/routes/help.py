"""Page d'aide : cycle de travail et rôle de chaque onglet."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.db import get_session
from app.routes.deps import render
from app.services import release_service

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/aide", response_class=HTMLResponse)
def help_page(request: Request, session: SessionDep) -> Response:
    context = {"editable": release_service.editable_release(session), "nav": "help"}
    return render(request, session, "pages/help.html", context)
