"""Environnement Jinja2 et filtres d'affichage."""

import hashlib
from datetime import date, datetime
from typing import Any
from urllib.parse import quote, unquote

from fastapi.templating import Jinja2Templates

from app.config import APP_NAME, BASE_DIR

templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


def quote_path(value: Any) -> str:
    """Segment d'URL (les codes source peuvent contenir « / », « % », espaces…)."""
    return quote(str(value), safe="")


def fmt_int(value: Any) -> str:
    if value is None:
        return ""
    return f"{int(value):,}".replace(",", " ")


def fmt_dt(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, datetime):
        return value.astimezone().strftime("%d/%m/%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return str(value)


def fmt_value(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "oui" if value else "non"
    if isinstance(value, (datetime, date)):
        return fmt_dt(value)
    return str(value)


RELEASE_STATUS_LABELS = {"open": "ouverte", "frozen": "figée", "published": "publiée", "archived": "archivée"}
RELEASE_KIND_LABELS = {"major": "majeure", "staging": "staging"}
LOAD_MODE_SHORT = {"insert": "insertion", "upsert": "mise à jour", "replace_vocabulary": "remplacement"}


def release_status(value: Any) -> str:
    return RELEASE_STATUS_LABELS.get(str(value), str(value))


def release_kind(value: Any) -> str:
    return RELEASE_KIND_LABELS.get(str(value), str(value))


def load_mode(value: Any) -> str:
    return LOAD_MODE_SHORT.get(str(value), str(value))


def user_label(value: Any) -> str:
    """Nom d'auteur lisible (les premières versions enregistraient le cookie encodé : %20…)."""
    return unquote(str(value)) if value else ""


templates.env.filters["release_status"] = release_status
templates.env.filters["release_kind"] = release_kind
templates.env.filters["load_mode"] = load_mode
templates.env.filters["user_label"] = user_label
templates.env.filters["quote_path"] = quote_path
templates.env.filters["fmt_int"] = fmt_int
templates.env.filters["fmt_dt"] = fmt_dt
templates.env.filters["fmt_value"] = fmt_value
templates.env.globals["APP_NAME"] = APP_NAME


def _static_version() -> str:
    """Empreinte des CSS/JS de l'appli : force le rechargement par le navigateur après mise à jour."""
    digest = hashlib.sha256()
    for path in sorted((BASE_DIR / "app" / "static").glob("[cj]s*/app.*")):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


templates.env.globals["STATIC_VERSION"] = _static_version()
