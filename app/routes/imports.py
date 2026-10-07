"""Assistant d'import CSV / Excel en trois étapes et rapport d'import."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_session
from app.routes.deps import attachment, current_user, not_found, redirect, render
from app.schemas.imports import LOAD_MODE_LABELS, ColumnChoiceIn, ColumnMappingIn
from app.services import export_service, import_service
from app.services.errors import BusinessError

router = APIRouter(prefix="/imports")

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("", response_class=HTMLResponse)
def list_imports(request: Request, session: SessionDep) -> Response:
    batches = import_service.list_batches(session)
    return render(request, session, "pages/imports_list.html", {"batches": batches})


@router.get("/new", response_class=HTMLResponse)
def upload_form(request: Request, session: SessionDep) -> Response:
    release = import_service.open_release(session)
    return render(request, session, "pages/import_upload.html", {"target": release, "form_error": None})


@router.post("/new", response_class=HTMLResponse)
def upload_submit(
    request: Request,
    session: SessionDep,
    file: Annotated[UploadFile, File()],
    source_vocabulary_id: Annotated[str, Form()] = "",
) -> Response:
    try:
        batch_id = import_service.create_batch(
            session, file.file, file.filename or "", source_vocabulary_id[:20], current_user(request)
        )
    except BusinessError as exc:
        release = import_service.open_release(session)
        context = {"target": release, "form_error": exc.message}
        return render(request, session, "pages/import_upload.html", context, status_code=422)
    return redirect(request, f"/imports/{batch_id}/columns")


@router.get("/{batch_id:int}/columns", response_class=HTMLResponse)
def columns_form(request: Request, session: SessionDep, batch_id: int, sheet: str | None = None) -> Response:
    return _columns_page(request, session, batch_id, sheet)


def _columns_page(
    request: Request,
    session: Session,
    batch_id: int,
    sheet: str | None,
    form_error: str | None = None,
    posted: ColumnMappingIn | None = None,
) -> Response:
    try:
        wizard = import_service.wizard(session, batch_id, sheet, current_user(request))
    except BusinessError as exc:
        return not_found(request, session, exc.message)
    if posted is not None:
        wizard.choices = {
            k: {"file_column": v.file_column, "default_value": v.default_value}
            for k, v in posted.choices.items()
        }
        wizard.batch.load_mode = posted.load_mode
    context = {"wizard": wizard, "load_modes": LOAD_MODE_LABELS, "form_error": form_error}
    status = 422 if form_error else 200
    return render(request, session, "pages/import_columns.html", context, wizard.release, status_code=status)


@router.post("/{batch_id:int}/columns", response_class=HTMLResponse)
async def columns_submit(request: Request, session: SessionDep, batch_id: int) -> Response:
    form = await request.form()
    choices: dict[str, ColumnChoiceIn] = {}
    for key, value in form.items():
        for prefix, attr in (("file__", "file_column"), ("default__", "default_value")):
            if key.startswith(prefix):
                choice = choices.setdefault(key[len(prefix) :], ColumnChoiceIn())
                setattr(choice, attr, str(value) or None)
    load_mode = str(form.get("load_mode") or "upsert")
    data = ColumnMappingIn(
        sheet_name=str(form.get("sheet_name") or "") or None,
        load_mode=load_mode if load_mode in LOAD_MODE_LABELS else "upsert",
        choices=choices,
    )
    try:
        import_service.save_mapping(session, batch_id, data)
    except BusinessError as exc:
        return _columns_page(request, session, batch_id, data.sheet_name, exc.message, data)
    return redirect(request, f"/imports/{batch_id}/validate")


@router.get("/{batch_id:int}/validate", response_class=HTMLResponse)
def validate_page(request: Request, session: SessionDep, batch_id: int) -> Response:
    try:
        summary = import_service.validation_summary(session, batch_id)
    except BusinessError as exc:
        return redirect(request, f"/imports/{batch_id}/columns", error=exc.message)
    context = {"summary": summary, "load_modes": LOAD_MODE_LABELS}
    return render(request, session, "pages/import_validate.html", context, summary.release)


@router.post("/{batch_id:int}/validate", response_class=HTMLResponse)
def validate_submit(request: Request, session: SessionDep, batch_id: int) -> Response:
    try:
        result = import_service.run_import(session, batch_id, current_user(request))
    except BusinessError as exc:
        return redirect(request, f"/imports/{batch_id}", error=exc.message)
    notice = (
        f"Import terminé : {result.rows_inserted} insérées, {result.rows_updated} mises à jour, "
        f"{result.rows_unchanged} inchangées, "
        f"{result.rows_deleted} supprimées, {result.rows_rejected} rejetées."
    )
    return redirect(request, f"/imports/{batch_id}", notice=notice)


@router.get("/{batch_id:int}", response_class=HTMLResponse)
def report_page(request: Request, session: SessionDep, batch_id: int, page: int = 1) -> Response:
    try:
        report = import_service.report(session, batch_id, page)
    except BusinessError as exc:
        return not_found(request, session, exc.message)
    context = {
        "report": report,
        "state": import_service.batch_state(report.batch, report.release),
        "can_rollback": import_service.can_rollback(report.batch, report.release),
        "can_delete": import_service.can_delete(report.batch),
    }
    return render(request, session, "pages/import_report.html", context, report.release)


@router.post("/{batch_id:int}/rollback")
def rollback(request: Request, session: SessionDep, batch_id: int) -> Response:
    try:
        result = import_service.rollback(session, batch_id, current_user(request))
    except BusinessError as exc:
        return redirect(request, f"/imports/{batch_id}", error=exc.message)
    notice = (
        f"Import n° {batch_id} annulé : {result.deleted} ligne(s) ajoutée(s) retirée(s), "
        f"{result.restored} ligne(s) modifiée(s) restaurée(s), "
        f"{result.reinserted} ligne(s) supprimée(s) remise(s)."
    )
    return redirect(request, f"/imports/{batch_id}", notice=notice)


@router.post("/{batch_id:int}/delete")
def delete(request: Request, session: SessionDep, batch_id: int) -> Response:
    try:
        file_name = import_service.delete_batch(session, batch_id)
    except BusinessError as exc:
        return redirect(request, "/imports", error=exc.message)
    return redirect(request, "/imports", notice=f"Import n° {batch_id} ({file_name}) supprimé.")


@router.get("/{batch_id:int}/errors.csv")
def errors_csv(batch_id: int) -> Response:
    return StreamingResponse(
        export_service.import_errors_csv(batch_id),
        media_type="text/csv; charset=utf-8",
        headers=attachment(f"import_{batch_id}_rejets.csv"),
    )
