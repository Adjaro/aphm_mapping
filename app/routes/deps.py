"""Outils communs aux routers : utilisateur courant, rendu, redirections compatibles HTMX."""

import re
from typing import Any
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.models import Release
from app.services import release_service
from app.templating import templates

USER_COOKIE = "ref_user"
DEFAULT_USER = "anonyme"


def current_user(request: Request) -> str:
    """Auteur saisi librement dans le bandeau (cookie) ; pas d'authentification."""
    return (request.cookies.get(USER_COOKIE) or "").strip()[:100] or DEFAULT_USER


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
        "current_release": release,
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
