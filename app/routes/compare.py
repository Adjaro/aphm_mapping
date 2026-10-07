"""Onglet « Comparer » : différences entre deux releases, par code source."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_session
from app.routes.deps import attachment, parse_query, render
from app.schemas.compare import COMPARE_FACETS, CompareFilter
from app.services import compare_service, export_service
from app.services.errors import BusinessError

router = APIRouter(prefix="/compare")

SessionDep = Annotated[Session, Depends(get_session)]
ALIASES = {"from": "from_label", "to": "to_label"}


def _filter(request: Request) -> CompareFilter:
    return parse_query(request, CompareFilter, set(COMPARE_FACETS), ALIASES)


@router.get("", response_class=HTMLResponse)
def compare(request: Request, session: SessionDep) -> Response:
    flt = _filter(request)
    if not flt.from_label or not flt.to_label:
        flt.from_label, flt.to_label = compare_service.default_labels(session)
    result = None
    error = None
    if flt.from_label and flt.to_label:
        try:
            result = compare_service.compare(session, flt)
        except BusinessError as exc:
            error = exc.message
    context = {"flt": flt, "result": result, "compare_error": error, "nav": "compare"}
    return render(request, session, "pages/compare.html", context)


@router.get("/export.csv")
def export(request: Request) -> Response:
    flt = _filter(request)
    return StreamingResponse(
        export_service.compare_csv(flt),
        media_type="text/csv; charset=utf-8",
        headers=attachment(f"comparaison_{flt.from_label}_{flt.to_label}.csv"),
    )
