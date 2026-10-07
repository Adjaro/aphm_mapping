"""Accueil et recherche à facettes dans source_to_concept_map (ergonomie Athena)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db import get_session
from app.routes.deps import attachment, is_htmx, not_found, render, render_fragment
from app.schemas.search import FACET_LABELS, PAGE_SIZES, SCOPE_LABELS, SORT_LABELS, SearchFilter
from app.services import export_service, release_service, search_service

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]
LIST_FIELDS = set(FACET_LABELS)


def parse_filter(request: Request) -> SearchFilter:
    """Filtre depuis l'URL ; les paramètres invalides sont ignorés plutôt que de provoquer une erreur."""
    data: dict[str, Any] = {}
    for key in SearchFilter.model_fields:
        values = [v for v in request.query_params.getlist(key) if v != ""]
        if not values:
            continue
        data[key] = values if key in LIST_FIELDS else values[-1]
    for _ in range(len(data) + 1):
        try:
            return SearchFilter.model_validate(data)
        except ValidationError as exc:
            for error in exc.errors():
                data.pop(str(error["loc"][0]), None)
    return SearchFilter()


@router.get("/", response_class=HTMLResponse)
def home(request: Request, session: SessionDep, release: str | None = None) -> Response:
    current = release_service.resolve_release(session, release)
    if release and current is None:
        return not_found(request, session, f"Release {release} introuvable.")
    figures = search_service.key_figures(session, current) if current else None
    return render(request, session, "pages/home.html", {"figures": figures}, current)


@router.get("/search-terms/terms", response_class=HTMLResponse)
def search_terms(request: Request, session: SessionDep) -> Response:
    flt = parse_filter(request)
    current = release_service.resolve_release(session, flt.release)
    if current is None:
        return not_found(request, session, "Aucune release : charger des mappings ou créer une release.")
    flt.release = current.label
    result = search_service.search(session, current, flt)
    context = {
        "result": result,
        "flt": result.filter,
        "sort_labels": SORT_LABELS,
        "scope_labels": SCOPE_LABELS,
        "page_sizes": PAGE_SIZES,
        "release": current,
    }
    if is_htmx(request):
        return render_fragment(request, "partials/_search_body.html", context)
    return render(request, session, "pages/search_results.html", context, current)


@router.get("/search-terms/suggest", response_class=HTMLResponse)
def suggest(request: Request, session: SessionDep, query: str = "", scope: str = "all") -> Response:
    """Fragment : suggestions instantanées (codes source, concepts cibles) pendant la saisie."""
    current = release_service.resolve_release(session, request.query_params.get("release") or None)
    if current is None or scope not in SCOPE_LABELS:
        return render_fragment(request, "partials/_suggestions.html", {"suggestions": None})
    found = search_service.suggestions(session, current, query, scope)
    context = {"suggestions": found, "query": query, "release_label": current.label}
    return render_fragment(request, "partials/_suggestions.html", context)


@router.get("/search-terms/terms/export.csv")
def export_search(request: Request, session: SessionDep) -> Response:
    flt = parse_filter(request)
    current = release_service.resolve_release(session, flt.release)
    if current is None:
        return not_found(request, session, "Release introuvable.")
    return StreamingResponse(
        export_service.search_csv(current.release_id, flt),
        media_type="text/csv; charset=utf-8",
        headers=attachment(f"source_to_concept_map_{current.label}_recherche.csv"),
    )
