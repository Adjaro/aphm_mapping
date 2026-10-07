"""Onglet « Export » : properties, CSV CDM, CSV complet d'une release, avec filtres."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Release
from app.routes.deps import attachment, not_found, parse_query, redirect, render
from app.schemas.export import DEFAULT_STATUSES, FORMAT_LABELS, ExportFilter
from app.schemas.imports import MAPPING_STATUSES
from app.services import export_service, release_service

router = APIRouter(prefix="/export")

SessionDep = Annotated[Session, Depends(get_session)]
LIST_FIELDS = {"source_vocabulary", "mapping_status"}


def _filter(request: Request, form: dict[str, list[str]] | None = None) -> ExportFilter:
    """Filtre depuis l'URL (ou le formulaire) ; sans statut coché, les statuts par défaut s'appliquent."""
    flt = parse_query(request, ExportFilter, LIST_FIELDS)
    if form is not None:
        flt = ExportFilter.model_validate(
            {
                "release": next(iter(form.get("release", [])), None),
                "format": (form.get("format") or ["properties"])[0],
                "source_vocabulary": form.get("source_vocabulary", []),
                "mapping_status": form.get("mapping_status") or list(DEFAULT_STATUSES),
                "include_unmapped": bool(form.get("include_unmapped")),
            }
        )
    return flt


def _file_name(release: Release, flt: ExportFilter, extension: str) -> str:
    suffix = "_".join(flt.source_vocabulary) if flt.source_vocabulary else "complet"
    return f"source_to_concept_map_{release.label}_{flt.format}_{suffix}.{extension}"


@router.get("", response_class=HTMLResponse)
def export_page(request: Request, session: SessionDep) -> Response:
    flt = _filter(request)
    current = release_service.resolve_release(session, flt.release)
    if current is None:
        return not_found(request, session, "Aucune release à exporter.")
    flt.release = current.label
    summary = export_service.export_summary(session, current, flt)
    context = {
        "flt": flt,
        "summary": summary,
        "formats": FORMAT_LABELS,
        "statuses": MAPPING_STATUSES,
        "nav": "export",
    }
    return render(request, session, "pages/export.html", context, current)


@router.get("/download")
def download(request: Request, session: SessionDep) -> Response:
    flt = _filter(request)
    current = release_service.resolve_release(session, flt.release)
    if current is None:
        return not_found(request, session, "Release introuvable.")
    if flt.format == "properties":
        content = export_service.properties_zip(current.release_id, flt)
        return Response(
            content, media_type="application/zip", headers=attachment(_file_name(current, flt, "zip"))
        )
    stream = (
        export_service.release_cdm_csv(current.release_id, flt)
        if flt.format == "csv_cdm"
        else export_service.release_full_csv(current.release_id, flt)
    )
    return StreamingResponse(
        stream, media_type="text/csv; charset=utf-8", headers=attachment(_file_name(current, flt, "csv"))
    )


@router.post("/write")
async def write_to_folder(request: Request, session: SessionDep) -> Response:
    form = await request.form()
    values = {key: [str(v) for v in form.getlist(key)] for key in form}
    flt = _filter(request, values)
    current = release_service.resolve_release(session, flt.release)
    if current is None:
        return not_found(request, session, "Release introuvable.")
    result = export_service.write_properties(current.release_id, flt)
    notice = f"{len(result.files)} fichiers .properties de {current.label} écrits dans {result.directory}"
    if result.removed:
        notice += f" ; anciens fichiers supprimés : {', '.join(result.removed)}"
    return redirect(request, f"/export?{flt.query_string()}", notice=notice + ".")
