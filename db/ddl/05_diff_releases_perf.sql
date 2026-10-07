-- =====================================================================
-- 05 — Optimisation de mapping.diff_releases (même signature, même résultat)
-- Prérequis : 03_mapping.sql
-- La version de 03 sérialise toutes les lignes des deux releases en jsonb avant de
-- les comparer. Celle-ci compare d'abord les colonnes, puis ne construit old_row /
-- new_row / changed_fields que pour les lignes ADDED / REMOVED / MODIFIED.
-- IMPORTANT : toute nouvelle colonne de source_to_concept_map doit être ajoutée à la
-- comparaison ci-dessous (nouvelle migration), sauf si elle est exclue du diff
-- (stcm_id, release_id, import_batch_id, created_at, updated_at).
-- =====================================================================

CREATE OR REPLACE FUNCTION mapping.diff_releases(p_from text, p_to text)
RETURNS TABLE (
    change_type          text,
    source_vocabulary_id varchar,
    source_code          varchar,
    target_concept_id    int,
    relationship_id      varchar,
    changed_fields       text[],
    old_row              jsonb,
    new_row              jsonb
) LANGUAGE sql STABLE AS $$
    WITH excl AS (
        SELECT ARRAY['stcm_id', 'release_id', 'import_batch_id', 'created_at', 'updated_at'] AS cols
    ),
    a AS (
        SELECT s.*
          FROM mapping.source_to_concept_map s
         WHERE s.release_id = (SELECT r.release_id FROM mapping.release r WHERE r.label = p_from)
    ),
    b AS (
        SELECT s.*
          FROM mapping.source_to_concept_map s
         WHERE s.release_id = (SELECT r.release_id FROM mapping.release r WHERE r.label = p_to)
    ),
    d AS (
        SELECT CASE WHEN a.stcm_id IS NULL THEN NULL ELSE to_jsonb(a) - (SELECT cols FROM excl) END AS aj,
               CASE WHEN b.stcm_id IS NULL THEN NULL ELSE to_jsonb(b) - (SELECT cols FROM excl) END AS bj,
               coalesce(b.source_vocabulary_id, a.source_vocabulary_id) AS source_vocabulary_id,
               coalesce(b.source_code,          a.source_code)          AS source_code,
               coalesce(b.target_concept_id,    a.target_concept_id)    AS target_concept_id,
               coalesce(b.relationship_id,      a.relationship_id)      AS relationship_id
          FROM a
          FULL JOIN b
            ON  a.source_vocabulary_id = b.source_vocabulary_id
            AND a.source_code          = b.source_code
            AND a.target_concept_id    = b.target_concept_id
            AND a.relationship_id      = b.relationship_id
         WHERE a.stcm_id IS NULL
            OR b.stcm_id IS NULL
            OR (a.source_concept_id, a.source_code_description, a.target_vocabulary_id, a.valid_start_date,
                a.valid_end_date, a.invalid_reason, a.domain_id, a.source_frequency, a.mapping_status,
                a.equivalence, a.mapping_comment, a.mapped_by, a.reviewed_by, a.reviewed_at, a.extra)
               IS DISTINCT FROM
               (b.source_concept_id, b.source_code_description, b.target_vocabulary_id, b.valid_start_date,
                b.valid_end_date, b.invalid_reason, b.domain_id, b.source_frequency, b.mapping_status,
                b.equivalence, b.mapping_comment, b.mapped_by, b.reviewed_by, b.reviewed_at, b.extra)
    )
    SELECT
        CASE WHEN d.aj IS NULL THEN 'ADDED'
             WHEN d.bj IS NULL THEN 'REMOVED'
             ELSE 'MODIFIED' END,
        d.source_vocabulary_id,
        d.source_code,
        d.target_concept_id,
        d.relationship_id,
        CASE WHEN d.aj IS NOT NULL AND d.bj IS NOT NULL THEN
            ARRAY(SELECT k FROM jsonb_object_keys(d.bj) k
                   WHERE (d.aj -> k) IS DISTINCT FROM (d.bj -> k)
                   ORDER BY k)
        END,
        d.aj,
        d.bj
    FROM d;
$$;
