-- =====================================================================
-- 14 — Cache des comparaisons de releases (onglet « Comparer »)
-- Prérequis : 13_diff_perf_and_review_dates.sql
-- Le diff de deux releases est calculé une fois (mapping.diff_releases), stocké ici sous une clé
-- (releases + empreinte de leurs données), puis relu pour chaque page, filtre ou export.
-- Tables UNLOGGED : pur cache, non journalisé, vidé sans conséquence (recalculé à la demande).
-- L'application conserve les dernières comparaisons et supprime les plus anciennes.
-- =====================================================================

CREATE UNLOGGED TABLE IF NOT EXISTS mapping.compare_cache (
    cache_key   text PRIMARY KEY,
    from_label  text NOT NULL,
    to_label    text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE UNLOGGED TABLE IF NOT EXISTS mapping.compare_cache_lines (
    cache_key             text NOT NULL REFERENCES mapping.compare_cache (cache_key) ON DELETE CASCADE,
    change_type           text NOT NULL,
    source_vocabulary_id  varchar NOT NULL,
    source_code           varchar NOT NULL,
    target_concept_id     int NOT NULL,
    relationship_id       varchar NOT NULL,
    changed_fields        text[],
    old_row               jsonb,
    new_row               jsonb
);
CREATE INDEX IF NOT EXISTS idx_compare_cache_lines_key ON mapping.compare_cache_lines (cache_key);

CREATE UNLOGGED TABLE IF NOT EXISTS mapping.compare_cache_codes (
    cache_key                text NOT NULL REFERENCES mapping.compare_cache (cache_key) ON DELETE CASCADE,
    change_kind              text NOT NULL,
    source_vocabulary_id     varchar NOT NULL,
    source_code              varchar NOT NULL,
    source_code_description  varchar,
    n_added                  int NOT NULL,
    n_removed                int NOT NULL,
    n_modified               int NOT NULL,
    changed_fields           text[] NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_compare_cache_codes_key
    ON mapping.compare_cache_codes (cache_key, source_vocabulary_id, source_code);
