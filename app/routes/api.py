"""API JSON / CSV consommée par le pipeline ETL."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_session
from app.routes.deps import attachment
from app.services import export_service, release_service

router = APIRouter(prefix="/api")

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/releases")
def releases(session: SessionDep) -> list[dict[str, Any]]:
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
    if release_service.resolve_release(session, label) is None:
        raise HTTPException(status_code=404, detail=f"Release {label} introuvable")
    return StreamingResponse(
        export_service.cdm_csv(label),
        media_type="text/csv; charset=utf-8",
        headers=attachment(f"source_to_concept_map_{label}.csv"),
    )
