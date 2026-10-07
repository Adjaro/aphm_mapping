"""Releases : lignée, création de la staging, promotion, publication et diff."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_session
from app.routes.deps import attachment, current_user, redirect, render
from app.services import export_service, release_service
from app.services.errors import BusinessError

router = APIRouter(prefix="/releases")

SessionDep = Annotated[Session, Depends(get_session)]
ListQuery = Annotated[list[str], Query()]


@router.get("", response_class=HTMLResponse)
def list_releases(request: Request, session: SessionDep) -> Response:
    overview = release_service.overview(session)
    return render(request, session, "pages/releases_list.html", {"overview": overview})


@router.post("/initial")
def create_initial(
    request: Request,
    session: SessionDep,
    label: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
) -> Response:
    try:
        release_service.create_initial(session, label, description or None, current_user(request))
    except BusinessError as exc:
        return redirect(request, "/releases", error=exc.message)
    return redirect(request, "/releases", notice=f"Release {label.strip()} créée.")


@router.post("/staging")
def create_staging(
    request: Request,
    session: SessionDep,
    label: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
) -> Response:
    try:
        release_service.create_staging(session, label, description or None, current_user(request))
    except BusinessError as exc:
        return redirect(request, "/releases", error=exc.message)
    return redirect(request, "/releases", notice=f"Staging {label.strip()} créée.")


@router.post("/{label}/promote")
def promote(request: Request, session: SessionDep, label: str, new_label: Annotated[str, Form()]) -> Response:
    try:
        release_service.promote(session, label, new_label, current_user(request))
    except BusinessError as exc:
        return redirect(request, "/releases", error=exc.message)
    return redirect(
        request, "/releases", notice=f"Staging {label} promue en {new_label.strip()} ; {label} archivée."
    )


@router.post("/{label}/publish")
def publish(
    request: Request, session: SessionDep, label: str, cdm_build_ref: Annotated[str, Form()] = ""
) -> Response:
    try:
        release_service.publish(session, label, cdm_build_ref, current_user(request))
    except BusinessError as exc:
        return redirect(request, "/releases", error=exc.message)
    return redirect(request, "/releases", notice=f"Release {label} publiée.")


def _diff_params(request: Request) -> tuple[str, str]:
    return request.query_params.get("from", ""), request.query_params.get("to", "")


@router.get("/diff", response_class=HTMLResponse)
def diff(
    request: Request,
    session: SessionDep,
    change_type: ListQuery = [],  # noqa: B006 (valeur par défaut des paramètres de requête FastAPI)
    source_vocabulary: ListQuery = [],  # noqa: B006
    page: int = 1,
) -> Response:
    from_label, to_label = _diff_params(request)
    releases = release_service.list_releases(session)
    result = None
    error = None
    if from_label and to_label:
        try:
            result = release_service.diff(session, from_label, to_label, change_type, source_vocabulary, page)
        except BusinessError as exc:
            error = exc.message
    elif len(releases) >= 2:
        from_label, to_label = releases[1].label, releases[0].label
    context = {"result": result, "from_label": from_label, "to_label": to_label, "diff_error": error}
    return render(request, session, "pages/release_diff.html", context)


@router.get("/diff/export.csv")
def diff_export(
    request: Request,
    change_type: ListQuery = [],  # noqa: B006
    source_vocabulary: ListQuery = [],  # noqa: B006
) -> Response:
    from_label, to_label = _diff_params(request)
    return StreamingResponse(
        export_service.diff_csv(from_label, to_label, change_type, source_vocabulary),
        media_type="text/csv; charset=utf-8",
        headers=attachment(f"diff_{from_label}_{to_label}.csv"),
    )
