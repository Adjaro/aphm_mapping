-- =====================================================================
-- 04 — Fonctions de conversion tolérantes (contrôles d'import) et vue CDM par release
-- Prérequis : 03_mapping.sql
--   * mapping.try_cast_date(text)  : date ISO, YYYYMMDD ou JJ/MM/AAAA, NULL si invalide
--   * mapping.try_cast_int(text)   : entier 32 bits, NULL si invalide
--   * mapping.try_cast_numeric(text) : numérique (virgule décimale acceptée), NULL si invalide
--   * mapping.try_cast_boolean(text) : vrai/faux, oui/non, true/false, 1/0, NULL si invalide
--   * mapping.v_stcm_cdm : format CDM v5.4 strict pour n'importe quelle release (export pipeline)
-- =====================================================================

CREATE OR REPLACE FUNCTION mapping.try_cast_date(p_value text)
RETURNS date LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    v_value text := btrim(p_value);
BEGIN
    IF v_value IS NULL OR v_value = '' THEN
        RETURN NULL;
    END IF;
    IF v_value ~ '^\d{4}-\d{2}-\d{2}([ T].*)?$' THEN
        RETURN make_date(substr(v_value, 1, 4)::int, substr(v_value, 6, 2)::int, substr(v_value, 9, 2)::int);
    ELSIF v_value ~ '^\d{8}$' THEN
        RETURN make_date(substr(v_value, 1, 4)::int, substr(v_value, 5, 2)::int, substr(v_value, 7, 2)::int);
    ELSIF v_value ~ '^\d{2}/\d{2}/\d{4}$' THEN
        RETURN make_date(substr(v_value, 7, 4)::int, substr(v_value, 4, 2)::int, substr(v_value, 1, 2)::int);
    END IF;
    RETURN NULL;
EXCEPTION
    WHEN others THEN
        RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION mapping.try_cast_int(p_value text)
RETURNS int LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
               WHEN btrim(p_value) ~ '^-?\d{1,9}$' THEN btrim(p_value)::int
           END;
$$;

CREATE OR REPLACE FUNCTION mapping.try_cast_numeric(p_value text)
RETURNS numeric LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
               WHEN replace(btrim(p_value), ',', '.') ~ '^-?\d+(\.\d+)?$'
               THEN replace(btrim(p_value), ',', '.')::numeric
           END;
$$;

CREATE OR REPLACE FUNCTION mapping.try_cast_boolean(p_value text)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
               WHEN lower(btrim(p_value)) IN ('true', 't', 'vrai', 'oui', 'o', 'yes', 'y', '1') THEN true
               WHEN lower(btrim(p_value)) IN ('false', 'f', 'faux', 'non', 'n', 'no', '0')    THEN false
           END;
$$;

-- Format CDM v5.4 strict, toutes releases (le pipeline filtre sur release_label)
CREATE OR REPLACE VIEW mapping.v_stcm_cdm AS
SELECT r.label                   AS release_label,
       s.source_code,
       s.source_concept_id,
       s.source_vocabulary_id,
       s.source_code_description,
       s.target_concept_id,
       s.target_vocabulary_id,
       s.valid_start_date,
       s.valid_end_date,
       s.invalid_reason
  FROM mapping.source_to_concept_map s
  JOIN mapping.release r ON r.release_id = s.release_id
 WHERE s.mapping_status <> 'IGNORED';
