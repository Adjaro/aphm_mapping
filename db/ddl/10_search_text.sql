-- =====================================================================
-- 10 — Recherche plein texte insensible à la casse et aux accents
-- Prérequis : 03_mapping.sql
--   * extension unaccent (extension « trusted » : le propriétaire de la base peut la créer)
--   * mapping.normalize_text(text) : minuscules sans accents, IMMUTABLE (utilisable dans un index)
--   * index trigram sur le code et la description source normalisés
-- La recherche découpe la saisie en mots ; chaque mot doit apparaître dans le code, la
-- description source ou le libellé du concept cible (dans n'importe quel ordre).
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE OR REPLACE FUNCTION mapping.normalize_text(p_value text)
RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT lower(public.unaccent('public.unaccent'::regdictionary, coalesce(p_value, '')));
$$;

CREATE INDEX IF NOT EXISTS idx_stcm_code_norm_trgm
    ON mapping.source_to_concept_map USING gin (mapping.normalize_text(source_code) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_stcm_desc_norm_trgm
    ON mapping.source_to_concept_map USING gin (mapping.normalize_text(source_code_description) gin_trgm_ops);
