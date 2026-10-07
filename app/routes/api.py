"""API JSON / CSV / ZIP consommée par le pipeline ETL, et page de documentation locale."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Release
from app.routes.deps import attachment, render
from app.schemas.export import ExportFilter
from app.services import export_service, release_service

router = APIRouter(prefix="/api")

SessionDep = Annotated[Session, Depends(get_session)]


def _release(session: Session, label: str) -> Release:
    release = release_service.resolve_release(session, label)
    if release is None:
        raise HTTPException(status_code=404, detail=f"Release {label} introuvable")
    return release


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def api_page(request: Request, session: SessionDep) -> Response:
    """Documentation de l'API servie localement (pas de Swagger UI : il dépend d'un CDN)."""
    latest = release_service.resolve_release(session, None)
    context = {"example": latest.label if latest else "v1.0", "nav": "advanced"}
    return render(request, session, "pages/api.html", context, latest)


@router.get("/releases")
def releases(session: SessionDep) -> list[dict[str, Any]]:
    """Liste des releases avec statut, lignée et volumétrie."""
    return [
        {
            "label": row["label"],
            "kind": row["kind"],
            "status": row["status"],
            "parent_label": row["parent_label"],
            "vocabulary_version": row["vocabulary_version"],
            "cdm_build_ref": row["cdm_build_ref"],
            "created_at": row["created_at"],
            "published_at": row["published_at"],
            "n_mappings": row["n_mappings"],
        }
        for row in release_service.lineage(session)
    ]


@router.get("/releases/{label}/source_to_concept_map.csv")
def cdm_export(session: SessionDep, label: str) -> Response:
    """source_to_concept_map au format CDM v5.4 strict (mappings IGNORED exclus)."""
    _release(session, label)
    return StreamingResponse(
        export_service.cdm_csv(label),
        media_type="text/csv; charset=utf-8",
        headers=attachment(f"source_to_concept_map_{label}.csv"),
    )


@router.get("/releases/{label}/properties.zip")
def properties_export(session: SessionDep, label: str) -> Response:
    """Fichiers <Domaine>.properties (mappings IGNORED et cibles 0 exclus)."""
    release = _release(session, label)
    content = export_service.properties_zip(release.release_id, ExportFilter(release=label))
    return Response(content, media_type="application/zip", headers=attachment(f"properties_{label}.zip"))
