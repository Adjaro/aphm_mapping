"""Base Athena : connexions enregistrées, synchronisation des « Maps to », comparaison.

La base Athena distante est ouverte en lecture seule. Le nom de schéma distant est un
identifiant validé (CHECK SQL + motif) puis cité par psycopg.sql.Identifier : seule partie
dynamique des requêtes distantes, toutes les valeurs restant des paramètres liés.
"""

import re
from collections.abc import Iterator, Sequence
from typing import Any

import psycopg
from psycopg import sql
from sqlalchemy import RowMapping, select, text
from sqlalchemy.orm import Session

from app.db import raw_connection
from app.models import AthenaConnection, AthenaVocabularyMap
from app.schemas.athena import SCHEMA_PATTERN, AthenaFilter

CONNECT_TIMEOUT_S = 10
FETCH_SIZE = 10_000

# ---------------------------------------------------------------------------
# Base distante (lecture seule)
# ---------------------------------------------------------------------------

REMOTE_VERSION_SQL = sql.SQL(
    """
    SELECT v.vocabulary_version
      FROM {schema}.vocabulary v
     WHERE v.vocabulary_id = 'None'
    """
)

REMOTE_VOCABULARIES_SQL = sql.SQL(
    """
    SELECT v.vocabulary_id
      FROM {schema}.vocabulary v
     ORDER BY v.vocabulary_id
    """
)

REMOTE_RELATIONSHIP_CHECK_SQL = sql.SQL(
    """
    SELECT 1
      FROM {schema}.concept_relationship
     LIMIT 1
    """
)

REMOTE_MAPS_TO_SQL = sql.SQL(
    """
    SELECT c1.vocabulary_id,
           c1.concept_code,
           c1.concept_id,
           c1.concept_name,
           c1.invalid_reason,
           cr.relationship_id,
           c2.concept_id       AS target_concept_id,
           c2.concept_name     AS target_concept_name,
           c2.vocabulary_id    AS target_vocabulary_id,
           c2.domain_id        AS target_domain_id,
           c2.standard_concept AS target_standard_concept
      FROM {schema}.concept c1
      LEFT JOIN {schema}.concept_relationship cr
             ON cr.concept_id_1    = c1.concept_id
            AND cr.relationship_id IN ('Maps to', 'Maps to value')
            AND cr.invalid_reason IS NULL
      LEFT JOIN {schema}.concept c2 ON c2.concept_id = cr.concept_id_2
     WHERE c1.vocabulary_id = ANY(%(vocabularies)s)
    """
)


def _schema(connection: AthenaConnection) -> sql.Identifier:
    if not re.fullmatch(SCHEMA_PATTERN, connection.schema_name):
        raise ValueError("Nom de schéma Athena invalide")
    return sql.Identifier(connection.schema_name)


def connect(connection: AthenaConnection) -> psycopg.Connection:
    """Connexion en lecture seule à la base Athena enregistrée."""
    return psycopg.connect(
        host=connection.host,
        port=connection.port,
        dbname=connection.database_name,
        user=connection.username,
        password=connection.password or None,
        connect_timeout=CONNECT_TIMEOUT_S,
        options="-c default_transaction_read_only=on",
    )


def remote_summary(conn: psycopg.Connection, connection: AthenaConnection) -> tuple[str | None, list[str]]:
    """(version du vocabulaire, liste des vocabulary_id) ; vérifie aussi concept_relationship."""
    schema = _schema(connection)
    with conn.cursor() as cur:
        cur.execute(REMOTE_RELATIONSHIP_CHECK_SQL.format(schema=schema))
        cur.execute(REMOTE_VERSION_SQL.format(schema=schema))
        row = cur.fetchone()
        cur.execute(REMOTE_VOCABULARIES_SQL.format(schema=schema))
        vocabularies = [str(r[0]) for r in cur.fetchall()]
    return (str(row[0]) if row and row[0] is not None else None), vocabularies


def iter_remote_maps_to(
    conn: psycopg.Connection, connection: AthenaConnection, vocabularies: list[str]
) -> Iterator[tuple[Any, ...]]:
    with conn.cursor(name="athena_maps_to_sync") as cur:
        cur.itersize = FETCH_SIZE
        cur.execute(REMOTE_MAPS_TO_SQL.format(schema=_schema(connection)), {"vocabularies": vocabularies})
        yield from cur


# ---------------------------------------------------------------------------
# Paramétrage local
# ---------------------------------------------------------------------------


def list_connections(session: Session) -> Sequence[AthenaConnection]:
    stmt = select(AthenaConnection).order_by(AthenaConnection.is_active.desc(), AthenaConnection.label)
    return session.scalars(stmt).all()


def get_connection(session: Session, connection_id: int) -> AthenaConnection | None:
    return session.get(AthenaConnection, connection_id)


def get_connection_by_label(session: Session, label: str) -> AthenaConnection | None:
    return session.scalars(select(AthenaConnection).where(AthenaConnection.label == label)).first()


def active_connection(session: Session) -> AthenaConnection | None:
    return session.scalars(select(AthenaConnection).where(AthenaConnection.is_active)).first()


def add(session: Session, obj: AthenaConnection | AthenaVocabularyMap) -> None:
    session.add(obj)
    session.flush()


def delete(session: Session, obj: AthenaConnection | AthenaVocabularyMap) -> None:
    session.delete(obj)
    session.flush()


def deactivate_all(session: Session) -> None:
    session.execute(text("UPDATE mapping.athena_connection SET is_active = false WHERE is_active"))
    session.flush()


def list_vocabulary_maps(session: Session) -> Sequence[AthenaVocabularyMap]:
    return session.scalars(
        select(AthenaVocabularyMap).order_by(AthenaVocabularyMap.source_vocabulary_id)
    ).all()


def get_vocabulary_map(session: Session, source_vocabulary_id: str) -> AthenaVocabularyMap | None:
    return session.get(AthenaVocabularyMap, source_vocabulary_id)


def local_source_vocabularies(session: Session) -> list[str]:
    stmt = text(
        """
        SELECT DISTINCT s.source_vocabulary_id
          FROM mapping.source_to_concept_map s
         ORDER BY 1
        """
    )
    return [row[0] for row in session.execute(stmt)]


SNAPSHOT_STATS_SQL = text(
    """
    SELECT a.vocabulary_id,
           count(DISTINCT a.concept_id)                                           AS n_concepts,
           count(DISTINCT a.concept_id) FILTER (WHERE a.target_concept_id IS NOT NULL) AS n_mapped,
           count(*) FILTER (WHERE a.target_concept_id IS NOT NULL)                AS n_relations
      FROM mapping.athena_maps_to a
     GROUP BY a.vocabulary_id
     ORDER BY a.vocabulary_id
    """
)


def snapshot_stats(session: Session) -> Sequence[RowMapping]:
    return session.execute(SNAPSHOT_STATS_SQL).mappings().all()


# ---------------------------------------------------------------------------
# Copie locale des « Maps to » (dans la transaction ouverte par le service)
# ---------------------------------------------------------------------------

COPY_MAPS_TO = """
COPY mapping.athena_maps_to (
    vocabulary_id, concept_code, concept_id, concept_name, invalid_reason, relationship_id,
    target_concept_id, target_concept_name, target_vocabulary_id, target_domain_id, target_standard_concept
) FROM STDIN
"""


def clear_snapshot(session: Session) -> None:
    session.execute(text("DELETE FROM mapping.athena_maps_to"))


def copy_snapshot(session: Session, rows: Iterator[tuple[Any, ...]]) -> int:
    count = 0
    with raw_connection(session).cursor() as cur, cur.copy(COPY_MAPS_TO) as copy:
        for row in rows:
            copy.write_row(row)
            count += 1
    session.execute(text("ANALYZE mapping.athena_maps_to"))
    return count


# ---------------------------------------------------------------------------
# Comparaison d'une release avec Athena (mapping.compare_athena)
# ---------------------------------------------------------------------------

MATERIALIZE_SQL = text(
    """
    CREATE TEMP TABLE tmp_athena_cmp ON COMMIT DROP AS
    SELECT *
      FROM mapping.compare_athena(:release_label)
    """
)

FILTER_CLAUSE = """
     WHERE (:query = '' OR c.source_code ILIKE :pattern ESCAPE '\\'
            OR c.source_code_description ILIKE :pattern ESCAPE '\\')
       AND (:skip_status OR cardinality(CAST(:status AS text[])) = 0
            OR c.comparison_status = ANY(:status))
       AND (:skip_vocab OR cardinality(CAST(:source_vocabulary AS text[])) = 0
            OR c.source_vocabulary_id = ANY(:source_vocabulary))
       AND (:skip_mapping_status OR cardinality(CAST(:mapping_status AS text[])) = 0
            OR c.mapping_status = ANY(:mapping_status))
"""

CMP_FACETS_SQL = text(
    """
    SELECT 'status' AS facet, c.comparison_status AS value, count(*) AS n
      FROM tmp_athena_cmp c
    """
    + FILTER_CLAUSE.replace(":skip_status", "true")
    + """
     GROUP BY c.comparison_status
    UNION ALL
    SELECT 'source_vocabulary', c.source_vocabulary_id, count(*)
      FROM tmp_athena_cmp c
    """
    + FILTER_CLAUSE.replace(":skip_vocab", "true")
    + """
     GROUP BY c.source_vocabulary_id
    UNION ALL
    SELECT 'mapping_status', c.mapping_status, count(*)
      FROM tmp_athena_cmp c
    """
    + FILTER_CLAUSE.replace(":skip_mapping_status", "true")
    + """
     GROUP BY c.mapping_status
    ORDER BY facet, n DESC, value
    """
)

CMP_ROWS_SQL = text(
    """
    SELECT c.*,
           count(*) OVER () AS total
      FROM tmp_athena_cmp c
    """
    + FILTER_CLAUSE
    + """
     ORDER BY c.source_vocabulary_id, c.source_code, c.relationship_id, c.target_concept_id
     LIMIT :limit OFFSET :offset
    """
)

CMP_EXPORT_SQL = text(
    """
    SELECT c.*
      FROM tmp_athena_cmp c
    """
    + FILTER_CLAUSE
    + """
     ORDER BY c.source_vocabulary_id, c.source_code, c.relationship_id, c.target_concept_id
    """
).execution_options(yield_per=5000)

CMP_MATRIX_SQL = text(
    """
    SELECT c.source_vocabulary_id,
           c.athena_vocabulary_id,
           c.comparison_status,
           count(*) AS n
      FROM tmp_athena_cmp c
     GROUP BY 1, 2, 3
     ORDER BY 1
    """
)

UNCONFIGURED_SQL = text(
    """
    SELECT s.source_vocabulary_id,
           count(*) AS n
      FROM mapping.source_to_concept_map s
      JOIN mapping.release r ON r.release_id = s.release_id
     WHERE r.label = :release_label
       AND NOT EXISTS (
            SELECT 1
              FROM mapping.athena_vocabulary_map m
             WHERE m.source_vocabulary_id = s.source_vocabulary_id)
     GROUP BY s.source_vocabulary_id
     ORDER BY n DESC
    """
)

CODE_TARGETS_SQL = text(
    """
    SELECT a.vocabulary_id,
           a.concept_id,
           a.concept_code,
           a.concept_name,
           a.relationship_id,
           a.target_concept_id,
           a.target_concept_name,
           a.target_vocabulary_id,
           a.target_domain_id
      FROM mapping.athena_vocabulary_map m
      JOIN mapping.athena_maps_to a
        ON a.vocabulary_id = m.athena_vocabulary_id
       AND mapping.normalize_code(a.concept_code, m.ignore_dots, m.ignore_case)
         = mapping.normalize_code(:source_code, m.ignore_dots, m.ignore_case)
     WHERE m.source_vocabulary_id = :source_vocabulary_id
     ORDER BY a.relationship_id, a.target_concept_id
    """
)


def _cmp_params(flt: AthenaFilter) -> dict[str, Any]:
    escaped = flt.query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return {
        "query": flt.query,
        "pattern": f"%{escaped}%",
        "status": list(flt.status),
        "source_vocabulary": list(flt.source_vocabulary),
        "mapping_status": list(flt.mapping_status),
        "skip_status": False,
        "skip_vocab": False,
        "skip_mapping_status": False,
    }


def materialize_comparison(session: Session, release_label: str) -> None:
    session.execute(MATERIALIZE_SQL, {"release_label": release_label})


def comparison_facets(session: Session, flt: AthenaFilter) -> Sequence[RowMapping]:
    return session.execute(CMP_FACETS_SQL, _cmp_params(flt)).mappings().all()


def comparison_rows(session: Session, flt: AthenaFilter, limit: int, offset: int) -> Sequence[RowMapping]:
    return (
        session.execute(CMP_ROWS_SQL, {**_cmp_params(flt), "limit": limit, "offset": offset}).mappings().all()
    )


def iter_comparison(session: Session, flt: AthenaFilter) -> Iterator[RowMapping]:
    yield from session.execute(CMP_EXPORT_SQL, _cmp_params(flt)).mappings()


def comparison_matrix(session: Session) -> Sequence[RowMapping]:
    return session.execute(CMP_MATRIX_SQL).mappings().all()


def unconfigured_vocabularies(session: Session, release_label: str) -> Sequence[RowMapping]:
    return session.execute(UNCONFIGURED_SQL, {"release_label": release_label}).mappings().all()


def code_targets(session: Session, source_vocabulary_id: str, source_code: str) -> Sequence[RowMapping]:
    params = {"source_vocabulary_id": source_vocabulary_id, "source_code": source_code}
    return session.execute(CODE_TARGETS_SQL, params).mappings().all()
