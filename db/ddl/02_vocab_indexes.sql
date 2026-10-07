-- =====================================================================
-- 02 — Index du vocabulaire
-- À exécuter APRÈS le chargement des CSV Athena.
-- Rechargement complet : DROP de ces index, \copy, relance de ce script.
-- =====================================================================

-- Validation à l'import : retrouver un concept par (vocabulaire, code)
CREATE INDEX IF NOT EXISTS idx_concept_vocab_code ON vocab.concept (vocabulary_id, concept_code);

-- Sélecteur de concept cible (correction manuelle) : recherche par libellé ou code,
-- filtrée sur les concepts standards valides
CREATE INDEX IF NOT EXISTS idx_concept_name_trgm  ON vocab.concept USING gin (concept_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_concept_code_trgm  ON vocab.concept USING gin (concept_code gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_concept_std_domain ON vocab.concept (standard_concept, domain_id);

ANALYZE vocab.concept;
ANALYZE vocab.vocabulary;
