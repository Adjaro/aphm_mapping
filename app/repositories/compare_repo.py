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


# ---------------------------------------------------------------------------
# Métriques ligne à ligne (mapping.diff_releases), matérialisées dans tmp_diff_lines
# ---------------------------------------------------------------------------

MATERIALIZE_LINES_SQL = text(
    """
    CREATE TEMP TABLE tmp_diff_lines ON COMMIT DROP AS
    SELECT *
      FROM mapping.diff_releases(:from_label, :to_label)
    """
)

LINE_STATS_SQL = text(
    """
    SELECT 'total' AS metric, d.change_type AS key, d.change_type AS change_type, count(*) AS n
      FROM tmp_diff_lines d
     GROUP BY d.change_type
    UNION ALL
    SELECT 'vocabulary', d.source_vocabulary_id, d.change_type, count(*)
      FROM tmp_diff_lines d
     GROUP BY d.source_vocabulary_id, d.change_type
    UNION ALL
    SELECT 'domain',
           coalesce(d.new_row ->> 'domain_id', d.old_row ->> 'domain_id', '(vide)'),
           d.change_type,
           count(*)
      FROM tmp_diff_lines d
     GROUP BY 2, d.change_type
    UNION ALL
    SELECT 'field', f, 'MODIFIED', count(*)
      FROM tmp_diff_lines d
     CROSS JOIN LATERAL unnest(d.changed_fields) AS f
     GROUP BY f
    UNION ALL
    SELECT 'review', d.new_row ->> 'mapping_status', d.change_type, count(*)
      FROM tmp_diff_lines d
     WHERE d.change_type IN ('ADDED', 'MODIFIED')
       AND d.new_row ->> 'mapping_status' <> 'APPROVED'
     GROUP BY 2, d.change_type
    """
)

# Origine d'une ligne ajoutée ou modifiée : l'import qui l'a écrite (import_batch_id changé ou ligne
# ajoutée), sinon une correction manuelle ; les suppressions sont comptées à part.
# import_batch_id est exclu des lignes JSON de diff_releases : il est relu dans les deux releases.
LINE_ORIGINS_SQL = text(
    """
    WITH ids AS (
        SELECT (SELECT release_id FROM mapping.release WHERE label = :from_label) AS id_from,
               (SELECT release_id FROM mapping.release WHERE label = :to_label)   AS id_to
    ),
    o AS (
        SELECT d.change_type,
               CASE
                   WHEN d.change_type = 'REMOVED' THEN NULL
                   WHEN d.change_type = 'ADDED' OR nb.import_batch_id IS DISTINCT FROM ob.import_batch_id
                   THEN nb.import_batch_id
               END AS import_batch_id
          FROM tmp_diff_lines d
         CROSS JOIN ids
          LEFT JOIN mapping.source_to_concept_map nb
                 ON nb.release_id           = ids.id_to
                AND nb.source_vocabulary_id = d.source_vocabulary_id
                AND nb.source_code          = d.source_code
                AND nb.target_concept_id    = d.target_concept_id
                AND nb.relationship_id      = d.relationship_id
          LEFT JOIN mapping.source_to_concept_map ob
                 ON ob.release_id           = ids.id_from
                AND ob.source_vocabulary_id = d.source_vocabulary_id
                AND ob.source_code          = d.source_code
                AND ob.target_concept_id    = d.target_concept_id
                AND ob.relationship_id      = d.relationship_id
    )
    SELECT o.import_batch_id,
           b.file_name,
           b.created_by,
           b.loaded_at,
           b.status,
           count(*) FILTER (WHERE o.change_type = 'ADDED')    AS n_added,
           count(*) FILTER (WHERE o.change_type = 'MODIFIED') AS n_modified,
           count(*) FILTER (WHERE o.change_type = 'REMOVED')  AS n_removed
      FROM o
      LEFT JOIN mapping.import_batch b ON b.import_batch_id = o.import_batch_id
     GROUP BY o.import_batch_id, b.file_name, b.created_by, b.loaded_at, b.status
     ORDER BY (o.import_batch_id IS NULL), b.loaded_at DESC NULLS LAST
    """
)


def materialize_lines(session: Session, from_label: str, to_label: str) -> None:
    session.execute(MATERIALIZE_LINES_SQL, {"from_label": from_label, "to_label": to_label})


def line_stats(session: Session) -> Sequence[RowMapping]:
    return session.execute(LINE_STATS_SQL).mappings().all()


def line_origins(session: Session, from_label: str, to_label: str) -> Sequence[RowMapping]:
    params = {"from_label": from_label, "to_label": to_label}
    return session.execute(LINE_ORIGINS_SQL, params).mappings().all()


DATA_VERSION_SQL = text(
    """
    SELECT r.status,
           count(s.stcm_id)  AS n,
           max(s.stcm_id)    AS max_id,
           max(s.updated_at) AS max_updated
      FROM mapping.release r
      LEFT JOIN mapping.source_to_concept_map s ON s.release_id = r.release_id
     WHERE r.label = :label
     GROUP BY r.status
    """
)


def data_version(session: Session, label: str) -> str:
    """Empreinte d'une release : « figée » si publiée / archivée, sinon volume et dernière modification."""
    row = session.execute(DATA_VERSION_SQL, {"label": label}).first()
    if row is None:
        return "absente"
    if row.status in ("published", "archived"):
        return "figée"
    return f"{row.n}:{row.max_id}:{row.max_updated}"
