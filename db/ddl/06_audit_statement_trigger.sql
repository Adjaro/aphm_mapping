-- =====================================================================
-- 06 — Audit ensembliste : triggers FOR EACH STATEMENT avec tables de transition
-- Prérequis : 03_mapping.sql
-- Remplace le trigger ligne à ligne stcm_audit (03) par deux triggers d'instruction.
-- Le contenu de mapping.audit_log est identique (une ligne par mapping modifié ou
-- supprimé, old_row / new_row complets, changed_by issu de app.user), mais les
-- mises à jour massives (import upsert, replace_vocabulary) sont nettement plus rapides.
-- =====================================================================

DROP TRIGGER IF EXISTS stcm_audit ON mapping.source_to_concept_map;
DROP FUNCTION IF EXISTS mapping.trg_stcm_audit();

CREATE OR REPLACE FUNCTION mapping.trg_stcm_audit_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO mapping.audit_log (release_id, stcm_id, operation, old_row, new_row)
    SELECT o.release_id,
           o.stcm_id,
           'UPDATE',
           to_jsonb(o),
           to_jsonb(n)
      FROM old_rows o
      JOIN new_rows n ON n.stcm_id = o.stcm_id
     ORDER BY o.stcm_id;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION mapping.trg_stcm_audit_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO mapping.audit_log (release_id, stcm_id, operation, old_row, new_row)
    SELECT o.release_id,
           o.stcm_id,
           'DELETE',
           to_jsonb(o),
           NULL
      FROM old_rows o
     ORDER BY o.stcm_id;
    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS stcm_audit_update ON mapping.source_to_concept_map;
CREATE TRIGGER stcm_audit_update
    AFTER UPDATE ON mapping.source_to_concept_map
    REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
    FOR EACH STATEMENT EXECUTE FUNCTION mapping.trg_stcm_audit_update();

DROP TRIGGER IF EXISTS stcm_audit_delete ON mapping.source_to_concept_map;
CREATE TRIGGER stcm_audit_delete
    AFTER DELETE ON mapping.source_to_concept_map
    REFERENCING OLD TABLE AS old_rows
    FOR EACH STATEMENT EXECUTE FUNCTION mapping.trg_stcm_audit_delete();
