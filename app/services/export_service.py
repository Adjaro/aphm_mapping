"""Exports CSV en streaming (recherche, diff, release CDM, rejets d'import).

Chaque export ouvre sa propre session : le flux est consommé après la fin de la requête.
"""

import csv
import io
import json
import logging
import re
import zipfile
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.models import Release
from app.repositories import (
    athena_repo,
    compare_repo,
    custom_column_repo,
    export_repo,
    import_repo,
    release_repo,
    stcm_repo,
)
from app.schemas.athena import AthenaFilter
from app.schemas.compare import CompareFilter
from app.schemas.export import ExportFilter
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
COMPARE_HEADERS = (
    "change_kind",
    "source_vocabulary_id",
    "source_code",
    "source_code_description",
    "n_added",
    "n_removed",
    "n_modified",
    "changed_fields",
    "old_targets",
    "new_targets",
)
ATHENA_HEADERS = (
    "comparison_status",
    "source_vocabulary_id",
    "source_code",
    "source_code_description",
    "relationship_id",
    "target_concept_id",
    "target_concept_name",
    "mapping_status",
    "athena_vocabulary_id",
    "athena_concept_id",
    "athena_concept_code",
    "athena_concept_name",
    "athena_target_ids",
    "athena_targets",
)
BATCH_ROWS = 2000
PROPERTIES_SUFFIX = ".properties"
FULL_HEADERS = (
    "source_code",
    "source_concept_id",
    "source_vocabulary_id",
    "source_code_description",
    "target_concept_id",
    "target_concept_name",
    "target_vocabulary_id",
    "target_domain_id",
    "valid_start_date",
    "valid_end_date",
    "invalid_reason",
    "domain_id",
    "relationship_id",
    "source_frequency",
    "mapping_status",
    "equivalence",
    "mapping_comment",
    "mapped_by",
    "reviewed_by",
    "reviewed_at",
    "quality_flag",
    "import_batch_id",
)

logger = logging.getLogger(__name__)


@dataclass
class ExportSummaryOut:
    n_mappings: int
    n_codes: int
    n_domains: int
    vocabularies: list[tuple[str, int]]
    target_dir: Path


@dataclass
class WrittenFilesOut:
    directory: Path
    files: dict[str, int]
    removed: list[str]


def _format(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, list):
        if value and isinstance(value[0], dict):
            return json.dumps(value, ensure_ascii=False, default=str)
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


def compare_csv(flt: CompareFilter) -> Iterator[str]:
    def rows(session: Session) -> Iterator[Sequence[Any]]:
        compare_repo.materialize(session, flt.from_label, flt.to_label)
        for row in compare_repo.iter_rows(session, flt):
            yield [row[h] for h in COMPARE_HEADERS]

    return _with_session(rows, COMPARE_HEADERS)


def athena_csv(release_label: str, flt: AthenaFilter) -> Iterator[str]:
    def rows(session: Session) -> Iterator[Sequence[Any]]:
        athena_repo.materialize_comparison(session, release_label)
        for row in athena_repo.iter_comparison(session, flt):
            yield [row[h] for h in ATHENA_HEADERS]

    return _with_session(rows, ATHENA_HEADERS)


# ---------------------------------------------------------------------------
# Export d'une release (page Export, API, script)
# ---------------------------------------------------------------------------


def properties_dir() -> Path:
    settings = get_settings()
    return settings.export_dir / settings.properties_subdir


def export_summary(session: Session, release: Release, flt: ExportFilter) -> ExportSummaryOut:
    with session.begin():
        counts = export_repo.summary(session, release.release_id, flt)
        vocabularies = export_repo.vocabularies(session, release.release_id)
    return ExportSummaryOut(
        n_mappings=int(counts["n_mappings"]),
        n_codes=int(counts["n_codes"]),
        n_domains=int(counts["n_domains"]),
        vocabularies=[(row["source_vocabulary_id"], int(row["n"])) for row in vocabularies],
        target_dir=properties_dir(),
    )


def _domain_file_name(domain: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]", "_", domain) + PROPERTIES_SUFFIX


def properties_files(release_id: int, flt: ExportFilter) -> dict[str, str]:
    """Contenu de chaque fichier <Domaine>.properties (lignes code=cible[,cible…], LF final)."""
    lines: dict[str, list[str]] = {}
    session = SessionLocal()
    try:
        with session.begin():
            for row in export_repo.iter_properties(session, release_id, flt):
                lines.setdefault(_domain_file_name(row["domain"]), []).append(
                    f"{row['source_code']}={row['targets']}"
                )
    finally:
        session.close()
    return {name: "\n".join(content) + "\n" for name, content in lines.items()}


def properties_zip(release_id: int, flt: ExportFilter) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(properties_files(release_id, flt).items()):
            archive.writestr(name, content.encode("utf-8"))
    return buffer.getvalue()


def write_properties(release_id: int, flt: ExportFilter) -> WrittenFilesOut:
    """Écrit les fichiers dans le dossier configuré.

    Les anciens .properties absents de l'export sont supprimés ; les autres fichiers sont conservés.
    """
    directory = properties_dir()
    directory.mkdir(parents=True, exist_ok=True)
    files = properties_files(release_id, flt)
    for name, content in files.items():
        temp = directory / f".{name}.tmp"
        temp.write_bytes(content.encode("utf-8"))
        temp.replace(directory / name)
    removed = sorted(p.name for p in directory.glob("*" + PROPERTIES_SUFFIX) if p.name not in files)
    for name in removed:
        (directory / name).unlink()
    logger.info(
        "Export properties : %s fichiers écrits dans %s, %s supprimés", len(files), directory, len(removed)
    )
    return WrittenFilesOut(directory, {n: c.count("\n") for n, c in sorted(files.items())}, removed)


def release_cdm_csv(release_id: int, flt: ExportFilter) -> Iterator[str]:
    """CDM v5.4 strict filtré (séparateur virgule, sans BOM)."""
    session = SessionLocal()
    try:
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=",", lineterminator="\n")
        writer.writerow(stcm_repo.CDM_COLUMNS)
        with session.begin():
            for count, row in enumerate(export_repo.iter_cdm(session, release_id, flt), start=1):
                writer.writerow([_format(row[c]) for c in stcm_repo.CDM_COLUMNS])
                if count % BATCH_ROWS == 0:
                    yield buffer.getvalue()
                    buffer.seek(0)
                    buffer.truncate()
        yield buffer.getvalue()
    finally:
        session.close()


def release_full_csv(release_id: int, flt: ExportFilter) -> Iterator[str]:
    """Toutes les colonnes + colonnes personnalisées (une colonne par clé de extra déclarée)."""
    session = SessionLocal()
    try:
        with session.begin():
            custom = [c.column_name for c in custom_column_repo.list_columns(session)]

            def rows() -> Iterator[Sequence[Any]]:
                for row in export_repo.iter_full(session, release_id, flt):
                    extra = row["extra"] or {}
                    yield [row[h] for h in FULL_HEADERS] + [extra.get(name) for name in custom]

            yield from _csv_stream((*FULL_HEADERS, *custom), rows())
    finally:
        session.close()
