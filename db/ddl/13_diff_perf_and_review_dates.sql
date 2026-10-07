-- =====================================================================
-- 13 — Rapidité de diff_releases et dates de relecture automatiques
-- Prérequis : 11_search_text_column.sql, 12_status_lifecycle_and_import_rollback.sql
--   * diff_releases (même signature, même résultat) : changed_fields calculé par comparaison
--     des colonnes (au lieu des clés jsonb, réévaluées pour chaque clé) et lignes jsonb
--     calculées une seule fois (CTE MATERIALIZED). 30 s -> < 1 s pour 55 000 différences.
--     Toute nouvelle colonne de source_to_concept_map doit être ajoutée ici (comme en 05).
--   * Le rattrapage de 12 avait daté chaque validation automatique avec la date de publication
--     de SA release : des lignes identiques différaient alors d'une release à l'autre par
--     reviewed_at. Une validation automatique (sans relecteur) n'a pas de date de relecture :
--     reviewed_at est remis à NULL (lignes sans reviewed_by dont reviewed_at est une date de
--     publication), dans toutes les releases, verrou levé le temps de la correction.
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
        SELECT ARRAY['stcm_id', 'release_id', 'import_batch_id', 'created_at', 'updated_at', 'search_text'] AS cols
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
    d AS MATERIALIZED (
        SELECT CASE WHEN a.stcm_id IS NULL THEN NULL ELSE to_jsonb(a) - (SELECT cols FROM excl) END AS aj,
               CASE WHEN b.stcm_id IS NULL THEN NULL ELSE to_jsonb(b) - (SELECT cols FROM excl) END AS bj,
               coalesce(b.source_vocabulary_id, a.source_vocabulary_id) AS source_vocabulary_id,
               coalesce(b.source_code,          a.source_code)          AS source_code,
               coalesce(b.target_concept_id,    a.target_concept_id)    AS target_concept_id,
               coalesce(b.relationship_id,      a.relationship_id)      AS relationship_id,
               -- champs modifiés, dans l'ordre alphabétique (clés hors clé métier, identiques par jointure)
               CASE WHEN a.stcm_id IS NOT NULL AND b.stcm_id IS NOT NULL THEN
                   array_remove(ARRAY[
                       CASE WHEN a.domain_id               IS DISTINCT FROM b.domain_id               THEN 'domain_id' END,
                       CASE WHEN a.equivalence             IS DISTINCT FROM b.equivalence             THEN 'equivalence' END,
                       CASE WHEN a.extra                   IS DISTINCT FROM b.extra                   THEN 'extra' END,
                       CASE WHEN a.invalid_reason          IS DISTINCT FROM b.invalid_reason          THEN 'invalid_reason' END,
                       CASE WHEN a.mapped_by               IS DISTINCT FROM b.mapped_by               THEN 'mapped_by' END,
                       CASE WHEN a.mapping_comment         IS DISTINCT FROM b.mapping_comment         THEN 'mapping_comment' END,
                       CASE WHEN a.mapping_status          IS DISTINCT FROM b.mapping_status          THEN 'mapping_status' END,
                       CASE WHEN a.reviewed_at             IS DISTINCT FROM b.reviewed_at             THEN 'reviewed_at' END,
                       CASE WHEN a.reviewed_by             IS DISTINCT FROM b.reviewed_by             THEN 'reviewed_by' END,
                       CASE WHEN a.source_code_description IS DISTINCT FROM b.source_code_description THEN 'source_code_description' END,
                       CASE WHEN a.source_concept_id       IS DISTINCT FROM b.source_concept_id       THEN 'source_concept_id' END,
                       CASE WHEN a.source_frequency        IS DISTINCT FROM b.source_frequency        THEN 'source_frequency' END,
                       CASE WHEN a.target_vocabulary_id    IS DISTINCT FROM b.target_vocabulary_id    THEN 'target_vocabulary_id' END,
                       CASE WHEN a.valid_end_date          IS DISTINCT FROM b.valid_end_date          THEN 'valid_end_date' END,
                       CASE WHEN a.valid_start_date        IS DISTINCT FROM b.valid_start_date        THEN 'valid_start_date' END
                   ]::text[], NULL)
               END AS changed_fields
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
        d.changed_fields,
        d.aj,
        d.bj
    FROM d;
$$;

-- Validations automatiques : pas de date de relecture propre à chaque release
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM mapping.source_to_concept_map s
         WHERE s.reviewed_by IS NULL
           AND s.reviewed_at IN (SELECT published_at FROM mapping.release WHERE published_at IS NOT NULL)
    ) THEN
        PERFORM set_config('app.user', 'migration 13 (validation automatique sans date de relecture)', true);
        ALTER TABLE mapping.source_to_concept_map DISABLE TRIGGER stcm_guard;
        UPDATE mapping.source_to_concept_map s
           SET reviewed_at = NULL
         WHERE s.reviewed_by IS NULL
           AND s.reviewed_at IN (SELECT published_at FROM mapping.release WHERE published_at IS NOT NULL);
        ALTER TABLE mapping.source_to_concept_map ENABLE TRIGGER stcm_guard;
    END IF;
END $$;
