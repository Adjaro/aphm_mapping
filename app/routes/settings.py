"""Gestion des colonnes personnalisées de source_to_concept_map."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db import get_session
from app.routes.deps import redirect, render
from app.schemas.custom_column import DATA_TYPE_LABELS, CustomColumnIn
from app.services import custom_column_service
from app.services.errors import BusinessError

router = APIRouter(prefix="/settings")

SessionDep = Annotated[Session, Depends(get_session)]

FIELD_MESSAGES = {
    "column_name": "Nom technique : minuscules, chiffres et _ ; commence par une lettre.",
    "label": "Libellé obligatoire.",
    "data_type": "Type non autorisé.",
}


def _page(
    request: Request,
    session: Session,
    values: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    form_error: str | None = None,
) -> Response:
    context = {
        "columns": custom_column_service.list_columns(session),
        "usage": custom_column_service.usage_counts(session),
        "data_types": DATA_TYPE_LABELS,
        "values": values or {},
        "errors": errors or {},
        "form_error": form_error,
    }
    status = 422 if errors or form_error else 200
    return render(request, session, "pages/custom_columns.html", context, status_code=status)


@router.get("/custom-columns", response_class=HTMLResponse)
def custom_columns(request: Request, session: SessionDep) -> Response:
    return _page(request, session)


@router.post("/custom-columns", response_class=HTMLResponse)
async def custom_columns_submit(request: Request, session: SessionDep) -> Response:
    form = await request.form()
    values = {k: str(v) for k, v in form.items()}
    if values.get("action") == "delete":
        try:
            custom_column_service.delete_column(session, values.get("column_name", ""))
        except BusinessError as exc:
            return _page(request, session, form_error=exc.message)
        return redirect(request, "/settings/custom-columns", notice="Colonne supprimée.")
    try:
        data = CustomColumnIn.model_validate({**values, "is_required": values.get("is_required") == "on"})
    except ValidationError as exc:
        errors = {
            str(e["loc"][0]): FIELD_MESSAGES.get(str(e["loc"][0]), "Valeur invalide.") for e in exc.errors()
        }
        return _page(request, session, values, errors)
    try:
        custom_column_service.create_column(session, data)
    except BusinessError as exc:
        return _page(request, session, values, form_error=exc.message)
    return redirect(request, "/settings/custom-columns", notice=f"Colonne {data.column_name} créée.")
