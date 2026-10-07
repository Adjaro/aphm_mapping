-- =====================================================================
-- 08 — Comparaison de deux releases au niveau du code source
-- Prérequis : 05_diff_releases_perf.sql
-- mapping.diff_codes(p_from, p_to) regroupe le résultat de diff_releases par
-- (source_vocabulary_id, source_code) et qualifie le changement :
--   NEW_CODE       : code absent de p_from, présent dans p_to
--   REMOVED_CODE   : code présent dans p_from, absent de p_to
--   TARGET_CHANGED : cibles (target_concept_id, relationship_id) ajoutées ou retirées
--   MODIFIED       : mêmes cibles, autres colonnes modifiées (statut, commentaire…)
-- old_targets / new_targets : cibles du code dans chaque release (jsonb).
-- =====================================================================

CREATE OR REPLACE FUNCTION mapping.diff_codes(p_from text, p_to text)
RETURNS TABLE (
    change_kind              text,
    source_vocabulary_id     varchar,
    source_code              varchar,
    source_code_description  varchar,
    n_added                  int,
    n_removed                int,
    n_modified               int,
    changed_fields           text[],
    old_targets              jsonb,
    new_targets              jsonb
) LANGUAGE sql STABLE AS $$
    WITH d AS (
        SELECT *
          FROM mapping.diff_releases(p_from, p_to)
    ),
    c AS (
        SELECT d.source_vocabulary_id,
               d.source_code,
               (count(*) FILTER (WHERE d.change_type = 'ADDED'))::int    AS n_added,
               (count(*) FILTER (WHERE d.change_type = 'REMOVED'))::int  AS n_removed,
               (count(*) FILTER (WHERE d.change_type = 'MODIFIED'))::int AS n_modified
          FROM d
         GROUP BY d.source_vocabulary_id, d.source_code
    ),
    f AS (
        SELECT d.source_vocabulary_id,
               d.source_code,
               array_agg(DISTINCT k ORDER BY k) AS fields
          FROM d
         CROSS JOIN LATERAL unnest(d.changed_fields) AS k
         GROUP BY d.source_vocabulary_id, d.source_code
    ),
    r AS (
        SELECT (SELECT release_id FROM mapping.release WHERE label = p_from) AS id_from,
               (SELECT release_id FROM mapping.release WHERE label = p_to)   AS id_to
    ),
    t AS (
        SELECT s.release_id,
               s.source_vocabulary_id,
               s.source_code,
               max(s.source_code_description) AS description,
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
          JOIN c
            ON c.source_vocabulary_id = s.source_vocabulary_id
           AND c.source_code          = s.source_code
          LEFT JOIN vocab.concept co ON co.concept_id = s.target_concept_id
         WHERE s.release_id IN (SELECT id_from FROM r UNION ALL SELECT id_to FROM r)
         GROUP BY s.release_id, s.source_vocabulary_id, s.source_code
    )
    SELECT CASE
               WHEN o.targets IS NULL               THEN 'NEW_CODE'
               WHEN n.targets IS NULL               THEN 'REMOVED_CODE'
               WHEN c.n_added > 0 OR c.n_removed > 0 THEN 'TARGET_CHANGED'
               ELSE 'MODIFIED'
           END,
           c.source_vocabulary_id,
           c.source_code,
           coalesce(n.description, o.description),
           c.n_added,
           c.n_removed,
           c.n_modified,
           coalesce(f.fields, ARRAY[]::text[]),
           o.targets,
           n.targets
      FROM c
     CROSS JOIN r
      LEFT JOIN f
             ON f.source_vocabulary_id = c.source_vocabulary_id
            AND f.source_code          = c.source_code
      LEFT JOIN t o
             ON o.release_id           = r.id_from
            AND o.source_vocabulary_id = c.source_vocabulary_id
            AND o.source_code          = c.source_code
      LEFT JOIN t n
             ON n.release_id           = r.id_to
            AND n.source_vocabulary_id = c.source_vocabulary_id
            AND n.source_code          = c.source_code;
$$;
