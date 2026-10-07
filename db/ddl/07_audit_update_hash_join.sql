-- =====================================================================
-- 07 — Audit des UPDATE : jointure par hachage des tables de transition
-- Prérequis : 06_audit_statement_trigger.sql
-- Les tables de transition old_rows / new_rows n'ont ni index ni statistiques :
-- sans consigne, le planificateur peut choisir une boucle imbriquée (coût quadratique
-- sur un import massif). La fonction désactive les boucles imbriquées pour sa seule
-- exécution, ce qui impose une jointure par hachage sur stcm_id.
-- =====================================================================

CREATE OR REPLACE FUNCTION mapping.trg_stcm_audit_update()
RETURNS trigger LANGUAGE plpgsql
SET enable_nestloop = off
AS $$
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
