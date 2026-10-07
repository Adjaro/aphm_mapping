"""Onglet « Athena » (comparaison aux « Maps to » natifs) et paramétrage de la base Athena."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db import get_session
from app.routes.deps import attachment, not_found, parse_query, redirect, render
from app.schemas.athena import (
    ATHENA_FACETS,
    COMPARISON_STATUS_LABELS,
    AthenaConnectionIn,
    AthenaFilter,
    AthenaVocabularyMapIn,
)
from app.services import athena_service, export_service, release_service
from app.services.errors import BusinessError

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]
SETTINGS_URL = "/settings/athena"
CONNECTION_FIELDS = {
    "label": "Libellé obligatoire.",
    "host": "Hôte obligatoire.",
    "port": "Port invalide (1 à 65535).",
    "database_name": "Base obligatoire.",
    "username": "Utilisateur obligatoire.",
    "schema_name": "Schéma : lettres, chiffres et _ uniquement.",
}


@router.get("/athena", response_class=HTMLResponse)
def comparison(request: Request, session: SessionDep) -> Response:
    flt = parse_query(request, AthenaFilter, set(ATHENA_FACETS))
    current = release_service.resolve_release(session, flt.release)
    if current is None:
        return not_found(request, session, "Release introuvable.")
    flt.release = current.label
    result = athena_service.compare(session, current, flt)
    context = {"result": result, "flt": flt, "labels": COMPARISON_STATUS_LABELS, "nav": "athena"}
    return render(request, session, "pages/athena_compare.html", context, current)


@router.get("/athena/export.csv")
def comparison_export(request: Request, session: SessionDep) -> Response:
    flt = parse_query(request, AthenaFilter, set(ATHENA_FACETS))
    current = release_service.resolve_release(session, flt.release)
    if current is None:
        return not_found(request, session, "Release introuvable.")
    return StreamingResponse(
        export_service.athena_csv(current.label, flt),
        media_type="text/csv; charset=utf-8",
        headers=attachment(f"comparaison_athena_{current.label}.csv"),
    )


def _settings_page(
    request: Request,
    session: Session,
    values: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    form_error: str | None = None,
) -> Response:
    context = {
        "settings": athena_service.settings(session),
        "values": values or {},
        "errors": errors or {},
        "form_error": form_error,
        "nav": "settings",
    }
    status = 422 if errors or form_error else 200
    return render(request, session, "pages/athena_settings.html", context, status_code=status)


@router.get(SETTINGS_URL, response_class=HTMLResponse)
def settings_page(request: Request, session: SessionDep) -> Response:
    return _settings_page(request, session)


@router.post(SETTINGS_URL + "/connections", response_class=HTMLResponse)
async def create_connection(request: Request, session: SessionDep) -> Response:
    values = {k: str(v) for k, v in (await request.form()).items()}
    try:
        data = AthenaConnectionIn.model_validate({k: v for k, v in values.items() if v != ""})
    except ValidationError as exc:
        errors = {
            str(e["loc"][0]): CONNECTION_FIELDS.get(str(e["loc"][0]), "Valeur invalide.")
            for e in exc.errors()
        }
        return _settings_page(request, session, values, errors)
    try:
        athena_service.create_connection(session, data)
    except BusinessError as exc:
        return _settings_page(request, session, values, form_error=exc.message)
    return redirect(request, SETTINGS_URL, notice=f"Connexion « {data.label} » enregistrée.")


@router.post(SETTINGS_URL + "/connections/{connection_id:int}/{action}")
def connection_action(request: Request, session: SessionDep, connection_id: int, action: str) -> Response:
    try:
        if action == "test":
            message = athena_service.test_connection(session, connection_id)
        elif action == "activate":
            athena_service.activate_connection(session, connection_id)
            message = "Connexion activée."
        elif action == "delete":
            athena_service.delete_connection(session, connection_id)
            message = "Connexion supprimée."
        else:
            return not_found(request, session, "Action inconnue.")
    except BusinessError as exc:
        return redirect(request, SETTINGS_URL, error=exc.message)
    return redirect(request, SETTINGS_URL, notice=message)


@router.post(SETTINGS_URL + "/vocabulary-map")
async def save_vocabulary_map(request: Request, session: SessionDep) -> Response:
    form = await request.form()
    try:
        data = AthenaVocabularyMapIn(
            source_vocabulary_id=str(form.get("source_vocabulary_id") or ""),
            athena_vocabulary_id=str(form.get("athena_vocabulary_id") or ""),
            ignore_dots=form.get("ignore_dots") == "on",
            ignore_case=form.get("ignore_case") == "on",
        )
    except ValidationError:
        return redirect(
            request, SETTINGS_URL, error="Vocabulaires source et Athena obligatoires (20 caractères max)."
        )
    athena_service.save_vocabulary_map(session, data)
    return redirect(request, SETTINGS_URL, notice=f"Correspondance {data.source_vocabulary_id} enregistrée.")


@router.post(SETTINGS_URL + "/vocabulary-map/{source_vocabulary_id}/delete")
def delete_vocabulary_map(request: Request, session: SessionDep, source_vocabulary_id: str) -> Response:
    try:
        athena_service.delete_vocabulary_map(session, source_vocabulary_id)
    except BusinessError as exc:
        return redirect(request, SETTINGS_URL, error=exc.message)
    return redirect(request, SETTINGS_URL, notice=f"Correspondance {source_vocabulary_id} supprimée.")


@router.post(SETTINGS_URL + "/sync")
def synchronize(request: Request, session: SessionDep) -> Response:
    try:
        message = athena_service.synchronize(session)
    except BusinessError as exc:
        return redirect(request, SETTINGS_URL, error=exc.message)
    return redirect(request, SETTINGS_URL, notice=message)
