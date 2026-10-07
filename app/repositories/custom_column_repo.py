"""Déclaration des colonnes personnalisées (stockées dans source_to_concept_map.extra)."""

from collections.abc import Sequence

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import CustomColumn

USAGE_SQL = text(
    """
    SELECT count(*)
      FROM mapping.source_to_concept_map
     WHERE extra ? :name
    """
)


def list_columns(session: Session) -> Sequence[CustomColumn]:
    return session.scalars(
        select(CustomColumn).order_by(CustomColumn.created_at, CustomColumn.column_name)
    ).all()


def get_column(session: Session, column_name: str) -> CustomColumn | None:
    return session.get(CustomColumn, column_name)


def add_column(session: Session, column: CustomColumn) -> None:
    session.add(column)
    session.flush()


def delete_column(session: Session, column: CustomColumn) -> None:
    session.delete(column)
    session.flush()


def count_usage(session: Session, column_name: str) -> int:
    return int(session.execute(USAGE_SQL, {"name": column_name}).scalar_one())
