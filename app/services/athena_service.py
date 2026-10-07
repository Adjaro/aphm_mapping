"""Base Athena : paramétrage, test de connexion, synchronisation et comparaison des mappings."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
from sqlalchemy import RowMapping
from sqlalchemy.orm import Session

from app.models import AthenaConnection, AthenaVocabularyMap, Release
from app.repositories import athena_repo
from app.schemas.athena import (
    ATHENA_FACETS,
    COMPARISON_STATUS_LABELS,
    PAGE_SIZE,
    AthenaConnectionIn,
    AthenaFilter,
    AthenaVocabularyMapIn,
)
from app.schemas.search import FacetOut, FacetValueOut
from app.services.errors import BusinessError

logger = logging.getLogger(__name__)


@dataclass
class SettingsOut:
    connections: Sequence[AthenaConnection]
    vocabulary_maps: Sequence[AthenaVocabularyMap]
    local_vocabularies: list[str]
    snapshot: Sequence[RowMapping]


@dataclass
class ComparisonOut:
    filter: AthenaFilter
    connection: AthenaConnection | None
    configured: bool
    status_totals: dict[str, int]
    matrix: list[dict[str, Any]]
    unconfigured: Sequence[RowMapping]
    facets: list[FacetOut]
    rows: Sequence[RowMapping]
    total: int
    pages: int


# ---------------------------------------------------------------------------
# Paramétrage
# ---------------------------------------------------------------------------


def settings(session: Session) -> SettingsOut:
    with session.begin():
        return SettingsOut(
            connections=athena_repo.list_connections(session),
            vocabulary_maps=athena_repo.list_vocabulary_maps(session),
            local_vocabularies=athena_repo.local_source_vocabularies(session),
            snapshot=athena_repo.snapshot_stats(session),
        )


def create_connection(session: Session, data: AthenaConnectionIn) -> None:
    with session.begin():
        if athena_repo.get_connection_by_label(session, data.label.strip()) is not None:
            raise BusinessError(f"Une connexion « {data.label} » existe déjà.")
        first = not athena_repo.list_connections(session)
        connection = AthenaConnection(
            label=data.label.strip(),
            host=data.host.strip(),
            port=data.port,
            database_name=data.database_name.strip(),
            username=data.username.strip(),
            password=data.password or None,
            schema_name=data.schema_name,
            is_active=first,
        )
        athena_repo.add(session, connection)
    logger.info("Connexion Athena %s enregistrée", data.label)


def _connection(session: Session, connection_id: int) -> AthenaConnection:
    connection = athena_repo.get_connection(session, connection_id)
    if connection is None:
        raise BusinessError(f"Connexion Athena n° {connection_id} introuvable.")
    return connection


def activate_connection(session: Session, connection_id: int) -> None:
    with session.begin():
        connection = _connection(session, connection_id)
        athena_repo.deactivate_all(session)
        connection.is_active = True


def delete_connection(session: Session, connection_id: int) -> None:
    with session.begin():
        athena_repo.delete(session, _connection(session, connection_id))


def test_connection(session: Session, connection_id: int) -> str:
    """Message décrivant la base Athena (version, nombre de vocabulaires)."""
    with session.begin():
        connection = _connection(session, connection_id)
    try:
        with athena_repo.connect(connection) as conn:
            version, vocabularies = athena_repo.remote_summary(conn, connection)
    except psycopg.Error as exc:
        raise BusinessError(
            f"Connexion Athena « {connection.label} » impossible : {_pg_message(exc)}"
        ) from exc
    return (
        f"Connexion « {connection.label} » réussie : version {version or 'inconnue'}, "
        f"{len(vocabularies)} vocabulaires disponibles."
    )


def _pg_message(exc: psycopg.Error) -> str:
    primary = getattr(exc.diag, "message_primary", None)
    return str(primary or exc).strip().splitlines()[0]


def save_vocabulary_map(session: Session, data: AthenaVocabularyMapIn) -> None:
    with session.begin():
        existing = athena_repo.get_vocabulary_map(session, data.source_vocabulary_id.strip())
        if existing is None:
            athena_repo.add(
                session,
                AthenaVocabularyMap(
                    source_vocabulary_id=data.source_vocabulary_id.strip(),
                    athena_vocabulary_id=data.athena_vocabulary_id.strip(),
                    ignore_dots=data.ignore_dots,
                    ignore_case=data.ignore_case,
                ),
            )
        else:
            existing.athena_vocabulary_id = data.athena_vocabulary_id.strip()
            existing.ignore_dots = data.ignore_dots
            existing.ignore_case = data.ignore_case


def delete_vocabulary_map(session: Session, source_vocabulary_id: str) -> None:
    with session.begin():
        mapping = athena_repo.get_vocabulary_map(session, source_vocabulary_id)
        if mapping is None:
            raise BusinessError(f"Correspondance {source_vocabulary_id} introuvable.")
        athena_repo.delete(session, mapping)


# ---------------------------------------------------------------------------
# Synchronisation
# ---------------------------------------------------------------------------


def synchronize(session: Session) -> str:
    """Remplace la copie locale des « Maps to » par celle de la base Athena active."""
    with session.begin():
        connection = athena_repo.active_connection(session)
        if connection is None:
            raise BusinessError("Aucune connexion Athena active : en enregistrer une dans les paramètres.")
        vocabularies = sorted({m.athena_vocabulary_id for m in athena_repo.list_vocabulary_maps(session)})
        connection_id = connection.athena_connection_id
    if not vocabularies:
        raise BusinessError("Aucune correspondance de vocabulaire : en ajouter au moins une.")
    try:
        with athena_repo.connect(connection) as remote, session.begin():
            version, _ = athena_repo.remote_summary(remote, connection)
            athena_repo.clear_snapshot(session)
            count = athena_repo.copy_snapshot(
                session, athena_repo.iter_remote_maps_to(remote, connection, vocabularies)
            )
            _record_sync(session, connection_id, "ok", f"{len(vocabularies)} vocabulaire(s)", count, version)
    except psycopg.Error as exc:
        message = _pg_message(exc)
        with session.begin():
            _record_sync(session, connection_id, "failed", message, None, None)
        raise BusinessError(f"Synchronisation impossible : {message}") from exc
    logger.info("Synchronisation Athena : %s lignes (%s)", count, ", ".join(vocabularies))
    return f"Synchronisation terminée : {count} lignes copiées pour {', '.join(vocabularies)}."


def _record_sync(
    session: Session, connection_id: int, status: str, message: str, rows: int | None, version: str | None
) -> None:
    connection = _connection(session, connection_id)
    connection.last_sync_at = datetime.now(UTC)
    connection.last_sync_status = status
    connection.last_sync_message = message
    if status == "ok":
        connection.last_sync_rows = rows
        connection.athena_vocabulary_version = version
    session.flush()


# ---------------------------------------------------------------------------
# Comparaison
# ---------------------------------------------------------------------------


def compare(session: Session, release: Release, flt: AthenaFilter) -> ComparisonOut:
    with session.begin():
        connection = athena_repo.active_connection(session)
        configured = bool(athena_repo.list_vocabulary_maps(session))
        athena_repo.materialize_comparison(session, release.label)
        matrix_rows = athena_repo.comparison_matrix(session)
        unconfigured = athena_repo.unconfigured_vocabularies(session, release.label)
        facet_rows = athena_repo.comparison_facets(session, flt)
        rows = athena_repo.comparison_rows(session, flt, PAGE_SIZE, (flt.page - 1) * PAGE_SIZE)
    total = int(rows[0]["total"]) if rows else 0
    status_totals, matrix = _matrix(matrix_rows)
    return ComparisonOut(
        filter=flt,
        connection=connection,
        configured=configured,
        status_totals=status_totals,
        matrix=matrix,
        unconfigured=unconfigured,
        facets=_facets(flt, facet_rows),
        rows=rows,
        total=total,
        pages=max(1, -(-total // PAGE_SIZE)),
    )


def code_targets(session: Session, source_vocabulary_id: str, source_code: str) -> Sequence[RowMapping]:
    with session.begin():
        return athena_repo.code_targets(session, source_vocabulary_id, source_code)


def _matrix(rows: Sequence[RowMapping]) -> tuple[dict[str, int], list[dict[str, Any]]]:
    totals = dict.fromkeys(COMPARISON_STATUS_LABELS, 0)
    lines: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row["source_vocabulary_id"]
        line = lines.setdefault(
            key, {"source_vocabulary_id": key, "athena_vocabulary_id": row["athena_vocabulary_id"]}
        )
        line[row["comparison_status"]] = int(row["n"])
        totals[row["comparison_status"]] += int(row["n"])
    for line in lines.values():
        line["total"] = sum(line.get(status, 0) for status in COMPARISON_STATUS_LABELS)
    return totals, sorted(lines.values(), key=lambda line: str(line["source_vocabulary_id"]))


def _facets(flt: AthenaFilter, rows: Sequence[RowMapping]) -> list[FacetOut]:
    facets = {name: FacetOut(name=name, label=label) for name, label in ATHENA_FACETS.items()}
    for row in rows:
        selected = row["value"] in getattr(flt, row["facet"])
        label = (
            COMPARISON_STATUS_LABELS.get(row["value"], row["value"])
            if row["facet"] == "status"
            else row["value"]
        )
        facets[row["facet"]].values.append(FacetValueOut(row["value"], label, int(row["n"]), selected))
    return list(facets.values())
