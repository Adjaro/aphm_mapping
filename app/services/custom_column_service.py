"""Colonnes personnalisées : déclaration et validation des valeurs stockées dans extra."""

import logging
import re
from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.models import CustomColumn
from app.repositories import custom_column_repo
from app.schemas.custom_column import CustomColumnIn
from app.schemas.imports import IMPORT_COLUMN_NAMES
from app.services.errors import BusinessError

logger = logging.getLogger(__name__)

RESERVED_NAMES = set(IMPORT_COLUMN_NAMES) | {
    "stcm_id",
    "release_id",
    "reviewed_at",
    "import_batch_id",
    "extra",
    "created_at",
    "updated_at",
}
TRUE_VALUES = {"true", "t", "vrai", "oui", "o", "yes", "y", "1"}
FALSE_VALUES = {"false", "f", "faux", "non", "n", "no", "0"}
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def list_columns(session: Session) -> Sequence[CustomColumn]:
    with session.begin():
        return custom_column_repo.list_columns(session)


def usage_counts(session: Session) -> dict[str, int]:
    with session.begin():
        return {
            column.column_name: custom_column_repo.count_usage(session, column.column_name)
            for column in custom_column_repo.list_columns(session)
        }


def create_column(session: Session, data: CustomColumnIn) -> None:
    if data.column_name in RESERVED_NAMES:
        raise BusinessError(f"« {data.column_name} » est déjà une colonne de source_to_concept_map.")
    with session.begin():
        if custom_column_repo.get_column(session, data.column_name) is not None:
            raise BusinessError(f"La colonne « {data.column_name} » existe déjà.")
        column = CustomColumn(
            column_name=data.column_name,
            label=data.label.strip(),
            data_type=data.data_type,
            allowed_values=data.allowed_values or None,
            is_required=data.is_required,
            description=(data.description or "").strip() or None,
        )
        custom_column_repo.add_column(session, column)
    logger.info("Colonne personnalisée %s créée", data.column_name)


def delete_column(session: Session, column_name: str) -> None:
    """Supprime la déclaration ; les valeurs déjà stockées dans extra sont conservées."""
    with session.begin():
        column = custom_column_repo.get_column(session, column_name)
        if column is None:
            raise BusinessError(f"Colonne « {column_name} » introuvable.")
        custom_column_repo.delete_column(session, column)
    logger.info("Colonne personnalisée %s supprimée", column_name)


def convert_value(column: CustomColumn, raw: str | None) -> Any:
    """Valide et convertit une valeur saisie selon data_type / allowed_values (None si vide)."""
    value = (raw or "").strip()
    if not value:
        if column.is_required:
            raise BusinessError(f"{column.label} : valeur obligatoire.")
        return None
    if column.allowed_values and value not in column.allowed_values:
        raise BusinessError(f"{column.label} : valeur non autorisée ({', '.join(column.allowed_values)}).")
    converters = {
        "integer": _to_int,
        "numeric": _to_numeric,
        "date": _to_date,
        "boolean": _to_boolean,
    }
    converter = converters.get(column.data_type)
    if converter is None:
        return value
    try:
        return converter(value)
    except ValueError as exc:
        raise BusinessError(f"{column.label} : {exc}") from exc


def _to_int(value: str) -> int:
    if not re.fullmatch(r"-?\d{1,9}", value):
        raise ValueError("entier attendu")
    return int(value)


def _to_numeric(value: str) -> float:
    normalized = value.replace(",", ".")
    if not re.fullmatch(r"-?\d+(\.\d+)?", normalized):
        raise ValueError("nombre attendu")
    return float(normalized)


def _to_date(value: str) -> str:
    if not DATE_PATTERN.match(value):
        raise ValueError("date AAAA-MM-JJ attendue")
    return date.fromisoformat(value).isoformat()


def _to_boolean(value: str) -> bool:
    lowered = value.lower()
    if lowered in TRUE_VALUES:
        return True
    if lowered in FALSE_VALUES:
        return False
    raise ValueError("oui / non attendu")
