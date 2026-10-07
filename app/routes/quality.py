"""Synthèse du contrôle qualité des cibles (v_mapping_quality)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.db import get_session
from app.routes.deps import not_found, render
from app.services import release_service, search_service

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/quality", response_class=HTMLResponse)
def quality(request: Request, session: SessionDep, release: str | None = None) -> Response:
    current = release_service.resolve_release(session, release)
    if current is None:
        return not_found(request, session, "Release introuvable.")
    flags, lines = search_service.quality_summary(session, current)
    totals = {flag: sum(line.get(flag, 0) for line in lines) for flag in flags}
    totals["total"] = sum(line["total"] for line in lines)
    context = {"flags": flags, "lines": lines, "totals": totals, "release": current}
    return render(request, session, "pages/quality_summary.html", context, current)
