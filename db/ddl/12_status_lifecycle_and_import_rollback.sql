-- =====================================================================
-- 12 — Statuts liés au cycle de release et annulation d'un import
-- Prérequis : 03, 06, 07, 11
--   * publish_release : à la publication, les mappings UNCHECKED deviennent APPROVED
--     (revus par l'utilisateur qui publie) ; IGNORED est conservé ; la publication est
--     refusée s'il reste des mappings FLAGGED.
--   * releases déjà publiées : leurs mappings UNCHECKED passent APPROVED (une seule fois,
--     verrou levé le temps de la mise à jour, modifications tracées dans audit_log).
--   * import_batch.loaded_at : horodatage de la transaction de chargement (= audit_log.changed_at
--     des lignes modifiées / supprimées par l'import).
--   * rollback_import(batch) : annule un import d'une release ouverte (supprime les lignes
--     ajoutées, restaure les lignes modifiées ou supprimées) ; le lot passe « rolled_back ».
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Publication : validation automatique des mappings non encore revus
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION mapping.publish_release(p_label text, p_cdm_build_ref text DEFAULT NULL)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    v_release_id int;
    v_status     text;
    v_flagged    int;
BEGIN
    SELECT release_id, status INTO v_release_id, v_status
      FROM mapping.release
     WHERE label = p_label;
    IF v_release_id IS NULL OR v_status NOT IN ('open', 'frozen') THEN
        RAISE EXCEPTION 'Release % introuvable ou déjà publiée/archivée', p_label;
    END IF;

    SELECT count(*) INTO v_flagged
      FROM mapping.source_to_concept_map
     WHERE release_id = v_release_id
       AND mapping_status = 'FLAGGED';
    IF v_flagged > 0 THEN
        RAISE EXCEPTION 'Release % : % mapping(s) FLAGGED à traiter avant publication', p_label, v_flagged;
    END IF;

    IF v_status = 'open' THEN
        UPDATE mapping.source_to_concept_map
           SET mapping_status = 'APPROVED',
               reviewed_at    = coalesce(reviewed_at, now()),
               reviewed_by    = coalesce(reviewed_by, nullif(current_setting('app.user', true), ''))
         WHERE release_id = v_release_id
           AND mapping_status = 'UNCHECKED';
    ELSIF EXISTS (
        SELECT 1 FROM mapping.source_to_concept_map
         WHERE release_id = v_release_id AND mapping_status = 'UNCHECKED'
    ) THEN
        RAISE EXCEPTION 'Release % figée avec des mappings UNCHECKED : la rouvrir pour les valider', p_label;
    END IF;

    UPDATE mapping.release
       SET status        = 'published',
           frozen_at     = coalesce(frozen_at, now()),
           published_at  = now(),
           cdm_build_ref = coalesce(p_cdm_build_ref, cdm_build_ref)
     WHERE release_id = v_release_id;
END $$;

-- ---------------------------------------------------------------------
-- 2. Releases déjà publiées : UNCHECKED -> APPROVED (rattrapage unique)
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM mapping.source_to_concept_map s
          JOIN mapping.release r ON r.release_id = s.release_id
         WHERE r.status = 'published'
           AND s.mapping_status = 'UNCHECKED'
    ) THEN
        PERFORM set_config('app.user', 'migration 12 (publication = validation)', true);
        ALTER TABLE mapping.source_to_concept_map DISABLE TRIGGER stcm_guard;
        UPDATE mapping.source_to_concept_map s
           SET mapping_status = 'APPROVED',
               reviewed_at    = coalesce(s.reviewed_at, r.published_at),
               updated_at     = now()
          FROM mapping.release r
         WHERE r.release_id = s.release_id
           AND r.status = 'published'
           AND s.mapping_status = 'UNCHECKED';
        ALTER TABLE mapping.source_to_concept_map ENABLE TRIGGER stcm_guard;
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- 3. Annulation d'un import
-- ---------------------------------------------------------------------
ALTER TABLE mapping.import_batch ADD COLUMN IF NOT EXISTS loaded_at timestamptz;

-- Lignes concernées par un import : ajoutées (INSERT), modifiées (UPDATE) ou supprimées (DELETE)
CREATE OR REPLACE FUNCTION mapping.import_effects(p_batch_id int)
RETURNS TABLE (stcm_id bigint, effect text, old_row jsonb)
LANGUAGE sql STABLE AS $$
    WITH b AS (
        SELECT release_id, loaded_at FROM mapping.import_batch WHERE import_batch_id = p_batch_id
    )
    SELECT s.stcm_id, 'INSERT', NULL::jsonb
      FROM mapping.source_to_concept_map s, b
     WHERE s.release_id = b.release_id
       AND s.import_batch_id = p_batch_id
       AND s.created_at = b.loaded_at
    UNION ALL
    SELECT a.stcm_id, a.operation, a.old_row
      FROM mapping.audit_log a, b
     WHERE a.release_id = b.release_id
       AND a.changed_at = b.loaded_at
       AND a.operation IN ('UPDATE', 'DELETE');
$$;

CREATE OR REPLACE FUNCTION mapping.rollback_import(p_batch_id int)
RETURNS TABLE (n_deleted int, n_restored int, n_reinserted int)
LANGUAGE plpgsql AS $$
DECLARE
    v_batch    mapping.import_batch%ROWTYPE;
    v_status   text;
    v_conflict int;
    v_deleted  int;
    v_restored int;
    v_reinserted int;
BEGIN
    SELECT * INTO v_batch FROM mapping.import_batch WHERE import_batch_id = p_batch_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Import % introuvable', p_batch_id;
    END IF;
    IF v_batch.status NOT IN ('loaded', 'partial') OR v_batch.loaded_at IS NULL THEN
        RAISE EXCEPTION 'Import % non annulable (statut %)', p_batch_id, v_batch.status;
    END IF;
    SELECT status INTO v_status FROM mapping.release WHERE release_id = v_batch.release_id;
    IF v_status <> 'open' THEN
        RAISE EXCEPTION 'Import % : la release n''est plus ouverte (statut %), import archivé', p_batch_id, v_status;
    END IF;

    CREATE TEMP TABLE tmp_effects ON COMMIT DROP AS
    SELECT * FROM mapping.import_effects(p_batch_id);

    -- Refus si une ligne concernée a été modifiée depuis (correction manuelle, autre import)
    SELECT count(*) INTO v_conflict
      FROM mapping.audit_log a
     WHERE a.release_id = v_batch.release_id
       AND a.changed_at > v_batch.loaded_at
       AND a.stcm_id IN (SELECT e.stcm_id FROM tmp_effects e);
    IF v_conflict > 0 THEN
        RAISE EXCEPTION 'Import % : % ligne(s) modifiée(s) depuis le chargement, annulation impossible',
            p_batch_id, v_conflict;
    END IF;

    -- 1. Lignes ajoutées par l'import
    DELETE FROM mapping.source_to_concept_map s
     USING tmp_effects e
     WHERE e.effect = 'INSERT'
       AND s.stcm_id = e.stcm_id;
    GET DIAGNOSTICS v_deleted = ROW_COUNT;

    -- 2. Lignes supprimées par l'import (mode replace_vocabulary) : réinsérées à l'identique
    INSERT INTO mapping.source_to_concept_map (
        stcm_id, release_id, source_code, source_concept_id, source_vocabulary_id, source_code_description,
        target_concept_id, target_vocabulary_id, valid_start_date, valid_end_date, invalid_reason,
        domain_id, relationship_id, source_frequency, mapping_status, equivalence, mapping_comment,
        mapped_by, reviewed_by, reviewed_at, import_batch_id, extra, created_at, updated_at
    )
    SELECT r.stcm_id, r.release_id, r.source_code, r.source_concept_id, r.source_vocabulary_id,
           r.source_code_description, r.target_concept_id, r.target_vocabulary_id, r.valid_start_date,
           r.valid_end_date, r.invalid_reason, r.domain_id, r.relationship_id, r.source_frequency,
           r.mapping_status, r.equivalence, r.mapping_comment, r.mapped_by, r.reviewed_by, r.reviewed_at,
           r.import_batch_id, r.extra, r.created_at, r.updated_at
      FROM tmp_effects e
     CROSS JOIN LATERAL jsonb_populate_record(NULL::mapping.source_to_concept_map, e.old_row) r
     WHERE e.effect = 'DELETE';
    GET DIAGNOSTICS v_reinserted = ROW_COUNT;

    -- 3. Lignes modifiées par l'import : valeurs d'avant le chargement
    UPDATE mapping.source_to_concept_map s
       SET source_concept_id       = r.source_concept_id,
           source_code_description = r.source_code_description,
           target_vocabulary_id    = r.target_vocabulary_id,
           valid_start_date        = r.valid_start_date,
           valid_end_date          = r.valid_end_date,
           invalid_reason          = r.invalid_reason,
           domain_id               = r.domain_id,
           source_frequency        = r.source_frequency,
           mapping_status          = r.mapping_status,
           equivalence             = r.equivalence,
           mapping_comment         = r.mapping_comment,
           mapped_by               = r.mapped_by,
           reviewed_by             = r.reviewed_by,
           reviewed_at             = r.reviewed_at,
           import_batch_id         = r.import_batch_id,
           extra                   = r.extra
      FROM tmp_effects e
     CROSS JOIN LATERAL jsonb_populate_record(NULL::mapping.source_to_concept_map, e.old_row) r
     WHERE e.effect = 'UPDATE'
       AND s.stcm_id = e.stcm_id;
    GET DIAGNOSTICS v_restored = ROW_COUNT;

    UPDATE mapping.import_batch SET status = 'rolled_back' WHERE import_batch_id = p_batch_id;
    DROP TABLE tmp_effects;

    RETURN QUERY SELECT v_deleted, v_restored, v_reinserted;
END $$;
