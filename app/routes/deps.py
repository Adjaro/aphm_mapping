"""Outils communs aux routers : utilisateur courant, rendu, redirections compatibles HTMX."""

import re
from typing import Any, TypeVar
from urllib.parse import unquote, urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.models import Release
from app.services import release_service
from app.templating import templates

ModelT = TypeVar("ModelT", bound=BaseModel)

USER_COOKIE = "ref_user"
DEFAULT_USER = "anonyme"


def current_user(request: Request) -> str:
    """Auteur saisi librement dans le bandeau (cookie) ; pas d'authentification."""
    return unquote(request.cookies.get(USER_COOKIE) or "").strip()[:100] or DEFAULT_USER


def is_htmx(request: Request) -> bool:
    """Requête HTMX demandant un fragment (hors restauration d'historique)."""
    return (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-History-Restore-Request") != "true"
    )


def render(
    request: Request,
    session: Session,
    template: str,
    context: dict[str, Any],
    release: Release | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Page complète : ajoute la liste des releases (sélecteur du bandeau) et les messages."""
    full_context = {
        "releases": release_service.list_releases(session),
        "current_release": release or release_service.resolve_release(session, None),
        "user": current_user(request),
        "notice": request.query_params.get("notice"),
        "error": request.query_params.get("error"),
        **context,
    }
    return templates.TemplateResponse(request, template, full_context, status_code=status_code)


def render_fragment(
    request: Request, template: str, context: dict[str, Any], status_code: int = 200
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, template, {"user": current_user(request), **context}, status_code=status_code
    )


def redirect(request: Request, url: str, **params: str) -> Response:
    """Redirection après action : HX-Redirect pour HTMX, 303 sinon."""
    if params:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urlencode(params)}"
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=204, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


def attachment(file_name: str) -> dict[str, str]:
    """En-tête de téléchargement avec un nom de fichier assaini."""
    safe = re.sub(r"[^\w.\-]", "_", file_name)
    return {"Content-Disposition": f'attachment; filename="{safe}"'}


def not_found(request: Request, session: Session, message: str) -> HTMLResponse:
    return render(request, session, "pages/error.html", {"message": message}, status_code=404)


def parse_query(
    request: Request, model: type[ModelT], list_fields: set[str], aliases: dict[str, str] | None = None
) -> ModelT:
    """Modèle de filtre depuis l'URL ; les paramètres invalides sont ignorés plutôt que rejetés."""
    data: dict[str, Any] = {}
    names = {**{name: name for name in model.model_fields}, **(aliases or {})}
    for param, field_name in names.items():
        values = [v for v in request.query_params.getlist(param) if v != ""]
        if values:
            data[field_name] = values if field_name in list_fields else values[-1]
    for _ in range(len(data) + 1):
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            for error in exc.errors():
                data.pop(str(error["loc"][0]), None)
    return model()
