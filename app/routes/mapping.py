"""Fiche d'un code source, correction manuelle et sélecteur de concept cible."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db import get_session
from app.routes.deps import current_user, not_found, redirect, render, render_fragment
from app.schemas.imports import EQUIVALENCES, MAPPING_STATUSES, RELATIONSHIPS
from app.schemas.mapping import MappingEditIn
from app.services import athena_service, mapping_service, release_service, search_service
from app.services.errors import BusinessError
from app.templating import quote_path

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]
CUSTOM_PREFIX = "custom__"


def _edit_form(
    request: Request,
    session: Session,
    stcm_id: int,
    values: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    message: str | None = None,
) -> Response:
    try:
        ctx = mapping_service.edit_context(session, stcm_id)
    except BusinessError as exc:
        return render_fragment(request, "partials/_alert.html", {"message": exc.message}, status_code=404)
    mapping = ctx.mapping
    defaults = {
        "target_concept_id": str(mapping.target_concept_id),
        "relationship_id": mapping.relationship_id,
        "mapping_status": mapping.mapping_status,
        "equivalence": mapping.equivalence or "",
        "mapping_comment": mapping.mapping_comment or "",
    }
    for column in ctx.custom_columns:
        value = (mapping.extra or {}).get(column.column_name)
        defaults[CUSTOM_PREFIX + column.column_name] = "" if value is None else str(value)
    context = {
        "ctx": ctx,
        "values": {**defaults, **(values or {})},
        "errors": errors or {},
        "message": message,
        "statuses": MAPPING_STATUSES,
        "equivalences": EQUIVALENCES,
        "relationships": RELATIONSHIPS,
        "custom_prefix": CUSTOM_PREFIX,
    }
    status = 422 if errors or message else 200
    return render_fragment(request, "partials/_mapping_edit_form.html", context, status_code=status)


@router.get("/mappings/{stcm_id:int}/edit", response_class=HTMLResponse)
def edit_form(request: Request, session: SessionDep, stcm_id: int) -> Response:
    return _edit_form(request, session, stcm_id)


@router.post("/mappings/{stcm_id:int}/edit", response_class=HTMLResponse)
async def edit_submit(request: Request, session: SessionDep, stcm_id: int) -> Response:
    form = await request.form()
    values = {k: str(v) for k, v in form.items()}
    custom_values = {k[len(CUSTOM_PREFIX) :]: v for k, v in values.items() if k.startswith(CUSTOM_PREFIX)}
    try:
        data = MappingEditIn.model_validate(
            {
                "target_concept_id": values.get("target_concept_id", "").strip() or None,
                "relationship_id": values.get("relationship_id", "Maps to"),
                "mapping_status": values.get("mapping_status", "UNCHECKED"),
                "equivalence": values.get("equivalence") or None,
                "mapping_comment": values.get("mapping_comment") or None,
                "custom_values": custom_values,
            }
        )
    except ValidationError as exc:
        errors = {str(e["loc"][0]): "Valeur invalide." for e in exc.errors()}
        return _edit_form(request, session, stcm_id, values, errors)
    field_errors = data.field_errors()
    if field_errors:
        return _edit_form(request, session, stcm_id, values, field_errors)
    try:
        mapping, release_label = mapping_service.update_mapping(session, stcm_id, data, current_user(request))
    except BusinessError as exc:
        return _edit_form(request, session, stcm_id, values, message=exc.message)
    url = f"/mappings/{quote_path(mapping.source_vocabulary_id)}/{quote_path(mapping.source_code)}"
    return redirect(request, url, release=release_label, notice="Mapping enregistré.")


@router.get("/vocab/concepts/lookup", response_class=HTMLResponse)
def concept_lookup(
    request: Request,
    session: SessionDep,
    q: str = "",
    domain: str = "",
    standard_only: str = "1",
) -> Response:
    standard = standard_only not in ("", "0", "false")
    concepts = mapping_service.lookup_concepts(session, q, domain or None, standard)
    return render_fragment(request, "partials/_concept_lookup.html", {"concepts": concepts, "q": q})


@router.get("/mappings/{source_vocabulary_id}/{source_code:path}", response_class=HTMLResponse)
def mapping_detail(
    request: Request,
    session: SessionDep,
    source_vocabulary_id: str,
    source_code: str,
    release: str | None = None,
) -> Response:
    current = release_service.resolve_release(session, release)
    if current is None:
        return not_found(request, session, "Release introuvable.")
    detail = search_service.mapping_detail(session, current, source_vocabulary_id, source_code)
    if detail is None:
        return not_found(request, session, f"Code {source_code} ({source_vocabulary_id}) introuvable.")
    athena_targets = athena_service.code_targets(session, source_vocabulary_id, source_code)
    context = {"detail": detail, "athena_targets": athena_targets}
    return render(request, session, "pages/mapping_detail.html", context, current)
