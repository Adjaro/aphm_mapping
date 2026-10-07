"""Environnement Jinja2 et filtres d'affichage."""

from datetime import date, datetime
from typing import Any
from urllib.parse import quote

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


templates.env.filters["quote_path"] = quote_path
templates.env.filters["fmt_int"] = fmt_int
templates.env.filters["fmt_dt"] = fmt_dt
templates.env.filters["fmt_value"] = fmt_value
templates.env.globals["APP_NAME"] = APP_NAME
