"""Comparaison de deux releases : lignes (mapping.diff_releases) et vue par code source.

Le diff des lignes est calculé UNE fois par paire de releases (et par empreinte de leurs données),
stocké dans le cache mapping.compare_cache_lines (migration 14), puis relu pour chaque page,
filtre ou export. La vue par code (compare_cache_codes) en est déduite avec les mêmes règles que
mapping.diff_codes (08) — vérifié par les tests. Les cibles avant / après ne sont chargées que
pour les codes affichés ou exportés.
"""

from collections.abc import Iterator, Sequence
from typing import Any

from sqlalchemy import RowMapping, text
from sqlalchemy.orm import Session

from app.schemas.compare import CompareFilter

MATERIALIZE_SQL = text(
    """
    INSERT INTO mapping.compare_cache_codes (
        cache_key, change_kind, source_vocabulary_id, source_code, source_code_description,
        n_added, n_removed, n_modified, changed_fields
    )
    WITH ids AS (
        SELECT (SELECT release_id FROM mapping.release WHERE label = :from_label) AS id_from,
               (SELECT release_id FROM mapping.release WHERE label = :to_label)   AS id_to
    ),
    c AS (
        SELECT d.source_vocabulary_id,
               d.source_code,
               (count(*) FILTER (WHERE d.change_type = 'ADDED'))::int    AS n_added,
               (count(*) FILTER (WHERE d.change_type = 'REMOVED'))::int  AS n_removed,
               (count(*) FILTER (WHERE d.change_type = 'MODIFIED'))::int AS n_modified,
               max(coalesce(d.new_row ->> 'source_code_description',
                            d.old_row ->> 'source_code_description'))    AS source_code_description
          FROM mapping.compare_cache_lines d
         WHERE d.cache_key = :cache_key
         GROUP BY d.source_vocabulary_id, d.source_code
    ),
    f AS (
        SELECT d.source_vocabulary_id,
               d.source_code,
               array_agg(DISTINCT k ORDER BY k) AS fields
          FROM mapping.compare_cache_lines d
         CROSS JOIN LATERAL unnest(d.changed_fields) AS k
         WHERE d.cache_key = :cache_key
         GROUP BY d.source_vocabulary_id, d.source_code
    )
    SELECT :cache_key,
           CASE
               WHEN NOT EXISTS (
                    SELECT 1 FROM mapping.source_to_concept_map o
                     WHERE o.release_id = ids.id_from
                       AND o.source_vocabulary_id = c.source_vocabulary_id
                       AND o.source_code = c.source_code)          THEN 'NEW_CODE'
               WHEN NOT EXISTS (
                    SELECT 1 FROM mapping.source_to_concept_map n
                     WHERE n.release_id = ids.id_to
                       AND n.source_vocabulary_id = c.source_vocabulary_id
                       AND n.source_code = c.source_code)          THEN 'REMOVED_CODE'
               WHEN c.n_added > 0 OR c.n_removed > 0                THEN 'TARGET_CHANGED'
               ELSE 'MODIFIED'
           END                                      AS change_kind,
           c.source_vocabulary_id,
           c.source_code,
           c.source_code_description,
           c.n_added,
           c.n_removed,
           c.n_modified,
           coalesce(f.fields, ARRAY[]::text[])      AS changed_fields
      FROM c
     CROSS JOIN ids
      LEFT JOIN f
             ON f.source_vocabulary_id = c.source_vocabulary_id
            AND f.source_code          = c.source_code
    """
)

# Cibles d'un code dans une release (comme old_targets / new_targets de mapping.diff_codes)
TARGETS_SUBQUERY = """
    (SELECT jsonb_agg(
                jsonb_build_object(
                    'target_concept_id', s.target_concept_id,
                    'relationship_id',   s.relationship_id,
                    'mapping_status',    s.mapping_status,
                    'concept_name',      co.concept_name
                )
                ORDER BY s.relationship_id, s.target_concept_id
            )
       FROM mapping.source_to_concept_map s
       LEFT JOIN vocab.concept co ON co.concept_id = s.target_concept_id
      WHERE s.release_id = (SELECT release_id FROM mapping.release WHERE label = {label})
        AND s.source_vocabulary_id = p.source_vocabulary_id
        AND s.source_code = p.source_code)
"""
WITH_TARGETS = (
    "p.*, "
    + TARGETS_SUBQUERY.replace("{label}", ":from_label")
    + " AS old_targets, "
    + TARGETS_SUBQUERY.replace("{label}", ":to_label")
    + " AS new_targets"
)

# Filtres statiques : listes vides = pas de filtre ; chaque facette peut être ignorée (:skip_*)
FILTER_CLAUSE = """
     WHERE c.cache_key = :cache_key
       AND (:query = '' OR c.source_code ILIKE :pattern ESCAPE '\\'
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
      FROM mapping.compare_cache_codes c
    """
    + FILTER_CLAUSE.replace(":skip_kind", "true")
    + """
     GROUP BY c.change_kind
    UNION ALL
    SELECT 'source_vocabulary', c.source_vocabulary_id, count(*)
      FROM mapping.compare_cache_codes c
    """
    + FILTER_CLAUSE.replace(":skip_vocab", "true")
    + """
     GROUP BY c.source_vocabulary_id
    UNION ALL
    SELECT 'field', f, count(*)
      FROM mapping.compare_cache_codes c
     CROSS JOIN LATERAL unnest(c.changed_fields) AS f
    """
    + FILTER_CLAUSE.replace(":skip_field", "true")
    + """
     GROUP BY f
    ORDER BY facet, n DESC, value
    """
)

ROWS_SQL = text(
    "SELECT "
    + WITH_TARGETS
    + """
      FROM (SELECT c.*,
                   count(*) OVER () AS total
              FROM mapping.compare_cache_codes c
    """
    + FILTER_CLAUSE
    + """
             ORDER BY c.source_vocabulary_id, c.source_code
             LIMIT :limit OFFSET :offset) p
     ORDER BY p.source_vocabulary_id, p.source_code
    """
)

# Export : cibles des codes exportés chargées en une seule requête groupée (pas une sous-requête par code)
EXPORT_SQL = text(
    """
    WITH p AS (
        SELECT c.*
          FROM mapping.compare_cache_codes c
    """
    + FILTER_CLAUSE
    + """
    ),
    ids AS (
        SELECT (SELECT release_id FROM mapping.release WHERE label = :from_label) AS id_from,
               (SELECT release_id FROM mapping.release WHERE label = :to_label)   AS id_to
    ),
    tg AS (
        SELECT s.release_id,
               s.source_vocabulary_id,
               s.source_code,
               jsonb_agg(
                   jsonb_build_object(
                       'target_concept_id', s.target_concept_id,
                       'relationship_id',   s.relationship_id,
                       'mapping_status',    s.mapping_status,
                       'concept_name',      co.concept_name
                   )
                   ORDER BY s.relationship_id, s.target_concept_id
               ) AS targets
          FROM mapping.source_to_concept_map s
          JOIN p
            ON p.source_vocabulary_id = s.source_vocabulary_id
           AND p.source_code          = s.source_code
          LEFT JOIN vocab.concept co ON co.concept_id = s.target_concept_id
         WHERE s.release_id IN (SELECT id_from FROM ids UNION ALL SELECT id_to FROM ids)
         GROUP BY s.release_id, s.source_vocabulary_id, s.source_code
    )
    SELECT p.*,
           o.targets AS old_targets,
           n.targets AS new_targets
      FROM p
     CROSS JOIN ids
      LEFT JOIN tg o
             ON o.release_id           = ids.id_from
            AND o.source_vocabulary_id = p.source_vocabulary_id
            AND o.source_code          = p.source_code
      LEFT JOIN tg n
             ON n.release_id           = ids.id_to
            AND n.source_vocabulary_id = p.source_vocabulary_id
            AND n.source_code          = p.source_code
     ORDER BY p.source_vocabulary_id, p.source_code
    """
).execution_options(yield_per=5000)

# Plans par hachage pour les gros exports (estimations imprécises sur le cache)
EXPORT_SETTINGS = (text("SET LOCAL enable_nestloop = off"), text("SET LOCAL work_mem = '64MB'"))


MATRIX_SQL = text(
    """
    SELECT c.source_vocabulary_id,
           c.change_kind,
           count(*) AS n
      FROM mapping.compare_cache_codes c
     WHERE c.cache_key = :cache_key
     GROUP BY c.source_vocabulary_id, c.change_kind
     ORDER BY c.source_vocabulary_id
    """
)

STATUS_TRANSITIONS_SQL = text(
    """
    SELECT d.old_row ->> 'mapping_status' AS old_status,
           d.new_row ->> 'mapping_status' AS new_status,
           count(*)                       AS n
      FROM mapping.compare_cache_lines d
     WHERE d.cache_key = :cache_key
       AND d.change_type = 'MODIFIED'
       AND 'mapping_status' = ANY(d.changed_fields)
     GROUP BY 1, 2
     ORDER BY n DESC
    """
)

RELEASE_SIZES_SQL = text(
    """
    WITH codes AS (
        SELECT s.release_id,
               count(*) AS n_mappings
          FROM mapping.source_to_concept_map s
         WHERE s.release_id IN (
                SELECT release_id FROM mapping.release WHERE label IN (:from_label, :to_label))
         GROUP BY s.release_id, s.source_vocabulary_id, s.source_code
    )
    SELECT r.label,
           coalesce(sum(c.n_mappings), 0)::bigint AS n_mappings,
           count(c.release_id)            AS n_codes
      FROM mapping.release r
      LEFT JOIN codes c ON c.release_id = r.release_id
     WHERE r.label IN (:from_label, :to_label)
     GROUP BY r.label
    """
)


def _params(flt: CompareFilter, cache_key: str) -> dict[str, Any]:
    escaped = flt.query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return {
        "cache_key": cache_key,
        "from_label": flt.from_label,
        "to_label": flt.to_label,
        "query": flt.query,
        "pattern": f"%{escaped}%",
        "change_kind": list(flt.change_kind),
        "source_vocabulary": list(flt.source_vocabulary),
        "field": list(flt.field),
        "skip_kind": False,
        "skip_vocab": False,
        "skip_field": False,
    }


def ensure_cached(session: Session, cache_key: str, from_label: str, to_label: str) -> bool:
    """Calcule et met en cache la comparaison si elle ne l'est pas déjà ; True si calculée maintenant."""
    params = {"cache_key": cache_key, "from_label": from_label, "to_label": to_label}
    if session.execute(REGISTER_SQL, params).first() is None:
        return False
    session.execute(MATERIALIZE_LINES_SQL, params)
    session.execute(MATERIALIZE_SQL, params)
    session.execute(EVICT_SQL, {"keep": CACHE_SIZE})
    return True


def facets(session: Session, flt: CompareFilter, cache_key: str) -> Sequence[RowMapping]:
    return session.execute(FACETS_SQL, _params(flt, cache_key)).mappings().all()


def rows(
    session: Session, flt: CompareFilter, cache_key: str, limit: int, offset: int
) -> Sequence[RowMapping]:
    params = {**_params(flt, cache_key), "limit": limit, "offset": offset}
    return session.execute(ROWS_SQL, params).mappings().all()


def iter_rows(session: Session, flt: CompareFilter, cache_key: str) -> Iterator[RowMapping]:
    for statement in EXPORT_SETTINGS:
        session.execute(statement)
    yield from session.execute(EXPORT_SQL, _params(flt, cache_key)).mappings()


def matrix(session: Session, cache_key: str) -> Sequence[RowMapping]:
    return session.execute(MATRIX_SQL, {"cache_key": cache_key}).mappings().all()


def status_transitions(session: Session, cache_key: str) -> Sequence[RowMapping]:
    return session.execute(STATUS_TRANSITIONS_SQL, {"cache_key": cache_key}).mappings().all()


def release_sizes(session: Session, from_label: str, to_label: str) -> dict[str, RowMapping]:
    result = session.execute(RELEASE_SIZES_SQL, {"from_label": from_label, "to_label": to_label})
    return {row["label"]: row for row in result.mappings()}


# ---------------------------------------------------------------------------
# Métriques ligne à ligne (mapping.diff_releases), lues dans le cache mapping.compare_cache_lines
# ---------------------------------------------------------------------------

MATERIALIZE_LINES_SQL = text(
    """
    INSERT INTO mapping.compare_cache_lines (
        cache_key, change_type, source_vocabulary_id, source_code, target_concept_id, relationship_id,
        changed_fields, old_row, new_row
    )
    SELECT :cache_key, d.*
      FROM mapping.diff_releases(:from_label, :to_label) d
    """
)

REGISTER_SQL = text(
    """
    INSERT INTO mapping.compare_cache (cache_key, from_label, to_label)
    VALUES (:cache_key, :from_label, :to_label)
    ON CONFLICT (cache_key) DO NOTHING
    RETURNING cache_key
    """
)

# Cache borné : seules les comparaisons les plus récentes sont conservées
EVICT_SQL = text(
    """
    DELETE FROM mapping.compare_cache
     WHERE cache_key NOT IN (
            SELECT cache_key
              FROM mapping.compare_cache
             ORDER BY created_at DESC
             LIMIT :keep)
    """
)

CACHE_SIZE = 20

LINE_STATS_SQL = text(
    """
    SELECT 'total' AS metric, d.change_type AS key, d.change_type AS change_type, count(*) AS n
      FROM mapping.compare_cache_lines d
     WHERE d.cache_key = :cache_key
     GROUP BY d.change_type
    UNION ALL
    SELECT 'vocabulary', d.source_vocabulary_id, d.change_type, count(*)
      FROM mapping.compare_cache_lines d
     WHERE d.cache_key = :cache_key
     GROUP BY d.source_vocabulary_id, d.change_type
    UNION ALL
    SELECT 'domain',
           coalesce(d.new_row ->> 'domain_id', d.old_row ->> 'domain_id', '(vide)'),
           d.change_type,
           count(*)
      FROM mapping.compare_cache_lines d
     WHERE d.cache_key = :cache_key
     GROUP BY 2, d.change_type
    UNION ALL
    SELECT 'field', f, 'MODIFIED', count(*)
      FROM mapping.compare_cache_lines d
     CROSS JOIN LATERAL unnest(d.changed_fields) AS f
     WHERE d.cache_key = :cache_key
     GROUP BY f
    UNION ALL
    SELECT 'review', d.new_row ->> 'mapping_status', d.change_type, count(*)
      FROM mapping.compare_cache_lines d
     WHERE d.cache_key = :cache_key
       AND d.change_type IN ('ADDED', 'MODIFIED')
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
          FROM mapping.compare_cache_lines d
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
         WHERE d.cache_key = :cache_key
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


def line_stats(session: Session, cache_key: str) -> Sequence[RowMapping]:
    return session.execute(LINE_STATS_SQL, {"cache_key": cache_key}).mappings().all()


def line_origins(session: Session, cache_key: str, from_label: str, to_label: str) -> Sequence[RowMapping]:
    params = {"cache_key": cache_key, "from_label": from_label, "to_label": to_label}
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
