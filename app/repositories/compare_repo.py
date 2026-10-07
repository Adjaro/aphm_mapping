"""Comparaison de deux releases par code source (mapping.diff_codes).

Le résultat est matérialisé une fois par transaction dans tmp_diff_codes, puis agrégé.
"""

from collections.abc import Iterator, Sequence
from typing import Any

from sqlalchemy import RowMapping, text
from sqlalchemy.orm import Session

from app.schemas.compare import CompareFilter

MATERIALIZE_SQL = text(
    """
    CREATE TEMP TABLE tmp_diff_codes ON COMMIT DROP AS
    SELECT *
      FROM mapping.diff_codes(:from_label, :to_label)
    """
)

# Filtres statiques : listes vides = pas de filtre ; chaque facette peut être ignorée (:skip_*)
FILTER_CLAUSE = """
     WHERE (:query = '' OR c.source_code ILIKE :pattern ESCAPE '\\'
            OR c.source_code_description ILIKE :pattern ESCAPE '\\')
       AND (:skip_kind OR cardinality(CAST(:change_kind AS text[])) = 0
            OR c.change_kind = ANY(:change_kind))
       AND (:skip_vocab OR cardinality(CAST(:source_vocabulary AS text[])) = 0
            OR c.source_vocabulary_id = ANY(:source_vocabulary))
       AND (:skip_field OR cardinality(CAST(:field AS text[])) = 0
            OR c.changed_fields && CAST(:field AS text[]))
"""

FACETS_SQL = text(
    """
    SELECT 'change_kind' AS facet, c.change_kind AS value, count(*) AS n
      FROM tmp_diff_codes c
    """
    + FILTER_CLAUSE.replace(":skip_kind", "true")
    + """
     GROUP BY c.change_kind
    UNION ALL
    SELECT 'source_vocabulary', c.source_vocabulary_id, count(*)
      FROM tmp_diff_codes c
    """
    + FILTER_CLAUSE.replace(":skip_vocab", "true")
    + """
     GROUP BY c.source_vocabulary_id
    UNION ALL
    SELECT 'field', f, count(*)
      FROM tmp_diff_codes c
     CROSS JOIN LATERAL unnest(c.changed_fields) AS f
    """
    + FILTER_CLAUSE.replace(":skip_field", "true")
    + """
     GROUP BY f
    ORDER BY facet, n DESC, value
    """
)

ROWS_SQL = text(
    """
    SELECT c.*,
           count(*) OVER () AS total
      FROM tmp_diff_codes c
    """
    + FILTER_CLAUSE
    + """
     ORDER BY c.source_vocabulary_id, c.source_code
     LIMIT :limit OFFSET :offset
    """
)

EXPORT_SQL = text(
    """
    SELECT c.*
      FROM tmp_diff_codes c
    """
    + FILTER_CLAUSE
    + """
     ORDER BY c.source_vocabulary_id, c.source_code
    """
).execution_options(yield_per=5000)

MATRIX_SQL = text(
    """
    SELECT c.source_vocabulary_id,
           c.change_kind,
           count(*) AS n
      FROM tmp_diff_codes c
     GROUP BY c.source_vocabulary_id, c.change_kind
     ORDER BY c.source_vocabulary_id
    """
)

STATUS_TRANSITIONS_SQL = text(
    """
    SELECT o ->> 'mapping_status' AS old_status,
           n ->> 'mapping_status' AS new_status,
           count(*)               AS n
      FROM tmp_diff_codes c
     CROSS JOIN LATERAL jsonb_array_elements(c.old_targets) AS o
     CROSS JOIN LATERAL jsonb_array_elements(c.new_targets) AS n
     WHERE c.old_targets IS NOT NULL
       AND c.new_targets IS NOT NULL
       AND o ->> 'target_concept_id' = n ->> 'target_concept_id'
       AND o ->> 'relationship_id'   = n ->> 'relationship_id'
       AND o ->> 'mapping_status'   <> n ->> 'mapping_status'
     GROUP BY 1, 2
     ORDER BY n DESC
    """
)

RELEASE_SIZES_SQL = text(
    """
    SELECT r.label,
           count(s.stcm_id)                                         AS n_mappings,
           count(DISTINCT (s.source_vocabulary_id, s.source_code)) AS n_codes
      FROM mapping.release r
      LEFT JOIN mapping.source_to_concept_map s ON s.release_id = r.release_id
     WHERE r.label IN (:from_label, :to_label)
     GROUP BY r.label
    """
)


def _params(flt: CompareFilter) -> dict[str, Any]:
    escaped = flt.query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return {
        "query": flt.query,
        "pattern": f"%{escaped}%",
        "change_kind": list(flt.change_kind),
        "source_vocabulary": list(flt.source_vocabulary),
        "field": list(flt.field),
        "skip_kind": False,
        "skip_vocab": False,
        "skip_field": False,
    }


def materialize(session: Session, from_label: str, to_label: str) -> None:
    session.execute(MATERIALIZE_SQL, {"from_label": from_label, "to_label": to_label})


def facets(session: Session, flt: CompareFilter) -> Sequence[RowMapping]:
    return session.execute(FACETS_SQL, _params(flt)).mappings().all()


def rows(session: Session, flt: CompareFilter, limit: int, offset: int) -> Sequence[RowMapping]:
    params = {**_params(flt), "limit": limit, "offset": offset}
    return session.execute(ROWS_SQL, params).mappings().all()


def iter_rows(session: Session, flt: CompareFilter) -> Iterator[RowMapping]:
    yield from session.execute(EXPORT_SQL, _params(flt)).mappings()


def matrix(session: Session) -> Sequence[RowMapping]:
    return session.execute(MATRIX_SQL).mappings().all()


def status_transitions(session: Session) -> Sequence[RowMapping]:
    return session.execute(STATUS_TRANSITIONS_SQL).mappings().all()


def release_sizes(session: Session, from_label: str, to_label: str) -> dict[str, RowMapping]:
    result = session.execute(RELEASE_SIZES_SQL, {"from_label": from_label, "to_label": to_label})
    return {row["label"]: row for row in result.mappings()}
