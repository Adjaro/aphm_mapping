"""Exports CSV en streaming (recherche, diff, release CDM, rejets d'import).

Chaque export ouvre sa propre session : le flux est consommé après la fin de la requête.
"""

import csv
import io
import json
from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.repositories import import_repo, release_repo, stcm_repo
from app.schemas.search import SearchFilter

SEARCH_HEADERS = (
    "source_code",
    "source_code_description",
    "source_vocabulary_id",
    "domain_id",
    "target_concept_id",
    "concept_name",
    "target_vocabulary_id",
    "relationship_id",
    "mapping_status",
    "equivalence",
    "quality_flag",
    "validity",
)
DIFF_HEADERS = (
    "change_type",
    "source_vocabulary_id",
    "source_code",
    "target_concept_id",
    "relationship_id",
    "changed_fields",
    "old_row",
    "new_row",
)
ERROR_HEADERS = ("row_number", "error_message", "raw_row")
BATCH_ROWS = 2000


def _format(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, list):
        return ";".join(str(v) for v in value)
    return str(value)


def _csv_stream(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> Iterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    buffer.write("﻿")  # BOM : ouverture correcte dans Excel
    writer.writerow(headers)
    for count, row in enumerate(rows, start=1):
        writer.writerow([_format(v) for v in row])
        if count % BATCH_ROWS == 0:
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate()
    yield buffer.getvalue()


def _with_session(
    producer: Callable[[Session], Iterable[Sequence[Any]]], headers: Sequence[str]
) -> Iterator[str]:
    session = SessionLocal()
    try:
        with session.begin():
            yield from _csv_stream(headers, producer(session))
    finally:
        session.close()


def search_csv(release_id: int, flt: SearchFilter) -> Iterator[str]:
    flt = flt.normalized()

    def rows(session: Session) -> Iterator[Sequence[Any]]:
        for row in stcm_repo.iter_search(session, release_id, flt):
            yield [row[h] for h in SEARCH_HEADERS]

    return _with_session(rows, SEARCH_HEADERS)


def diff_csv(
    from_label: str, to_label: str, change_types: list[str], source_vocabularies: list[str]
) -> Iterator[str]:
    def rows(session: Session) -> Iterator[Sequence[Any]]:
        for row in release_repo.iter_diff(session, from_label, to_label, change_types, source_vocabularies):
            yield [row[h] for h in DIFF_HEADERS]

    return _with_session(rows, DIFF_HEADERS)


def cdm_csv(label: str) -> Iterator[str]:
    """Export CDM v5.4 strict (séparateur virgule, sans BOM) pour le pipeline."""
    session = SessionLocal()
    try:
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=",", lineterminator="\n")
        writer.writerow(stcm_repo.CDM_COLUMNS)
        with session.begin():
            for count, row in enumerate(stcm_repo.iter_cdm_export(session, label), start=1):
                writer.writerow([_format(row[c]) for c in stcm_repo.CDM_COLUMNS])
                if count % BATCH_ROWS == 0:
                    yield buffer.getvalue()
                    buffer.seek(0)
                    buffer.truncate()
        yield buffer.getvalue()
    finally:
        session.close()


def import_errors_csv(batch_id: int) -> Iterator[str]:
    def rows(session: Session) -> Iterator[Sequence[Any]]:
        for error in import_repo.iter_errors(session, batch_id):
            yield [error.row_number, error.error_message, error.raw_row]

    return _with_session(rows, ERROR_HEADERS)
