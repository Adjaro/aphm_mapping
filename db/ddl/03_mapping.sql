-- =====================================================================
-- 03 — Référentiel de mappings versionné (schéma mapping)
-- Prérequis : 01_vocab_tables.sql
-- Cycle de vie : v1.0 (published) -> v1.1 (staging, open) -> v2.0 (published) ...
-- Modèle "snapshot" : chaque release possède sa copie complète des lignes.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE SCHEMA IF NOT EXISTS mapping;

-- ---------------------------------------------------------------------
-- 2. Releases
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mapping.release (
    release_id          serial PRIMARY KEY,
    label               text NOT NULL UNIQUE,                 -- 'v1.0', 'v1.1', 'v2.0'
    kind                text NOT NULL CHECK (kind IN ('major', 'staging')),
    status              text NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'frozen', 'published', 'archived')),
    parent_release_id   int REFERENCES mapping.release (release_id),
    vocabulary_version  text,                                 -- vocab.vocabulary 'None'.vocabulary_version
    cdm_build_ref       text,                                 -- identifiant du CDM généré avec cette release
    description         text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    frozen_at           timestamptz,
    published_at        timestamptz
);

-- Une seule staging ouverte à la fois
CREATE UNIQUE INDEX IF NOT EXISTS uq_release_one_open_staging
    ON mapping.release (kind) WHERE kind = 'staging' AND status = 'open';

-- ---------------------------------------------------------------------
-- 3. Colonnes personnalisées (stockées dans source_to_concept_map.extra)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mapping.custom_column (
    column_name     text PRIMARY KEY CHECK (column_name ~ '^[a-z][a-z0-9_]*$'),
    label           text NOT NULL,
    data_type       text NOT NULL DEFAULT 'text'
                    CHECK (data_type IN ('text', 'integer', 'numeric', 'date', 'boolean')),
    allowed_values  text[],                                   -- liste fermée optionnelle
    is_required     boolean NOT NULL DEFAULT false,
    description     text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 4. Imports CSV / Excel
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mapping.import_batch (
    import_batch_id       serial PRIMARY KEY,
    release_id            int NOT NULL REFERENCES mapping.release (release_id),
    file_name             text NOT NULL,
    file_sha256           text,
    sheet_name            text,
    domain_id             varchar(20),
    source_vocabulary_id  varchar(20),
    column_mapping        jsonb NOT NULL DEFAULT '{}',  -- {"source_code": "CODE_LOCAL", ...}
    default_values        jsonb NOT NULL DEFAULT '{}',  -- {"source_vocabulary_id": "APHM_LABO", ...}
    load_mode             text NOT NULL DEFAULT 'upsert'
                          CHECK (load_mode IN ('insert', 'upsert', 'replace_vocabulary')),
    rows_read             int NOT NULL DEFAULT 0,
    rows_loaded           int NOT NULL DEFAULT 0,
    rows_rejected         int NOT NULL DEFAULT 0,
    status                text NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'loaded', 'partial', 'failed', 'rolled_back')),
    created_by            text,
    created_at            timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mapping.import_error (
    import_error_id  bigserial PRIMARY KEY,
    import_batch_id  int NOT NULL REFERENCES mapping.import_batch (import_batch_id) ON DELETE CASCADE,
    row_number       int,
    raw_row          jsonb,
    error_message    text NOT NULL
);

-- ---------------------------------------------------------------------
-- 5. SOURCE_TO_CONCEPT_MAP versionnée et étendue
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mapping.source_to_concept_map (
    stcm_id                  bigserial PRIMARY KEY,
    release_id               int NOT NULL REFERENCES mapping.release (release_id),
    -- colonnes OMOP CDM v5.4
    source_code              varchar(50)  NOT NULL,
    source_concept_id        int          NOT NULL DEFAULT 0,
    source_vocabulary_id     varchar(20)  NOT NULL,
    source_code_description  varchar(255),
    target_concept_id        int          NOT NULL,
    target_vocabulary_id     varchar(20)  NOT NULL,
    valid_start_date         date         NOT NULL DEFAULT DATE '1970-01-01',
    valid_end_date           date         NOT NULL DEFAULT DATE '2099-12-31',
    invalid_reason           varchar(1),
    -- extensions AP-HM
    domain_id                varchar(20),
    relationship_id          varchar(20)  NOT NULL DEFAULT 'Maps to',   -- 'Maps to' / 'Maps to value'
    source_frequency         int,
    mapping_status           text NOT NULL DEFAULT 'UNCHECKED'
                             CHECK (mapping_status IN ('APPROVED', 'UNCHECKED', 'FLAGGED', 'IGNORED')),
    equivalence              text
                             CHECK (equivalence IN ('EQUAL', 'EQUIVALENT', 'WIDER', 'NARROWER', 'INEXACT', 'UNMATCHED')),
    mapping_comment          text,
    mapped_by                text,
    reviewed_by              text,
    reviewed_at              timestamptz,
    import_batch_id          int REFERENCES mapping.import_batch (import_batch_id),
    extra                    jsonb NOT NULL DEFAULT '{}',               -- colonnes personnalisées
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_stcm_release_key
        UNIQUE (release_id, source_vocabulary_id, source_code, target_concept_id, relationship_id)
);

CREATE INDEX IF NOT EXISTS idx_stcm_release_src    ON mapping.source_to_concept_map (release_id, source_vocabulary_id, source_code);
CREATE INDEX IF NOT EXISTS idx_stcm_release_target ON mapping.source_to_concept_map (release_id, target_concept_id);
CREATE INDEX IF NOT EXISTS idx_stcm_release_domain ON mapping.source_to_concept_map (release_id, domain_id);
CREATE INDEX IF NOT EXISTS idx_stcm_target         ON mapping.source_to_concept_map (target_concept_id);
CREATE INDEX IF NOT EXISTS idx_stcm_code_trgm      ON mapping.source_to_concept_map USING gin (source_code gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_stcm_desc_trgm      ON mapping.source_to_concept_map USING gin (source_code_description gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_stcm_extra          ON mapping.source_to_concept_map USING gin (extra);

-- ---------------------------------------------------------------------
-- 6. Audit des modifications (utile surtout pour la staging)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mapping.audit_log (
    audit_id     bigserial PRIMARY KEY,
    release_id   int,
    stcm_id      bigint,
    operation    text NOT NULL,
    old_row      jsonb,
    new_row      jsonb,
    changed_by   text NOT NULL DEFAULT coalesce(current_setting('app.user', true), current_user),
    changed_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_release ON mapping.audit_log (release_id, changed_at);
CREATE INDEX IF NOT EXISTS idx_audit_stcm    ON mapping.audit_log (stcm_id);

-- ---------------------------------------------------------------------
-- 7. Triggers : verrouillage des releases figées + audit + updated_at
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION mapping.trg_stcm_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT status INTO v_status FROM mapping.release WHERE release_id = OLD.release_id;
        IF v_status <> 'open' THEN
            RAISE EXCEPTION 'Release % non modifiable (statut %)', OLD.release_id, v_status;
        END IF;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        SELECT status INTO v_status FROM mapping.release WHERE release_id = NEW.release_id;
        IF v_status <> 'open' THEN
            RAISE EXCEPTION 'Release % non modifiable (statut %)', NEW.release_id, v_status;
        END IF;
        IF TG_OP = 'UPDATE' THEN
            NEW.updated_at := now();
        END IF;
        RETURN NEW;
    END IF;
    RETURN OLD;
END $$;

DROP TRIGGER IF EXISTS stcm_guard ON mapping.source_to_concept_map;
CREATE TRIGGER stcm_guard
    BEFORE INSERT OR UPDATE OR DELETE ON mapping.source_to_concept_map
    FOR EACH ROW EXECUTE FUNCTION mapping.trg_stcm_guard();

-- Audit ligne à ligne sur UPDATE/DELETE uniquement
-- (les INSERT massifs sont tracés par import_batch, pas ligne à ligne)
CREATE OR REPLACE FUNCTION mapping.trg_stcm_audit()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO mapping.audit_log (release_id, stcm_id, operation, old_row, new_row)
    VALUES (
        OLD.release_id,
        OLD.stcm_id,
        TG_OP,
        to_jsonb(OLD),
        CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(NEW) END
    );
    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS stcm_audit ON mapping.source_to_concept_map;
CREATE TRIGGER stcm_audit
    AFTER UPDATE OR DELETE ON mapping.source_to_concept_map
    FOR EACH ROW EXECUTE FUNCTION mapping.trg_stcm_audit();

-- ---------------------------------------------------------------------
-- 8. Gestion des releases
-- ---------------------------------------------------------------------

-- Crée une release en copiant intégralement la release parente
CREATE OR REPLACE FUNCTION mapping.create_release(
    p_label        text,
    p_kind         text,
    p_parent_label text DEFAULT NULL,
    p_description  text DEFAULT NULL
) RETURNS int LANGUAGE plpgsql AS $$
DECLARE
    v_parent int;
    v_new    int;
BEGIN
    IF p_parent_label IS NOT NULL THEN
        SELECT release_id INTO v_parent FROM mapping.release WHERE label = p_parent_label;
        IF v_parent IS NULL THEN
            RAISE EXCEPTION 'Release parente % introuvable', p_parent_label;
        END IF;
    END IF;

    INSERT INTO mapping.release (label, kind, status, parent_release_id, description, vocabulary_version)
    VALUES (
        p_label, p_kind, 'open', v_parent, p_description,
        (SELECT vocabulary_version FROM mapping.release WHERE release_id = v_parent)
    )
    RETURNING release_id INTO v_new;

    IF v_parent IS NOT NULL THEN
        INSERT INTO mapping.source_to_concept_map (
            release_id, source_code, source_concept_id, source_vocabulary_id, source_code_description,
            target_concept_id, target_vocabulary_id, valid_start_date, valid_end_date, invalid_reason,
            domain_id, relationship_id, source_frequency, mapping_status, equivalence, mapping_comment,
            mapped_by, reviewed_by, reviewed_at, import_batch_id, extra, created_at
        )
        SELECT
            v_new, source_code, source_concept_id, source_vocabulary_id, source_code_description,
            target_concept_id, target_vocabulary_id, valid_start_date, valid_end_date, invalid_reason,
            domain_id, relationship_id, source_frequency, mapping_status, equivalence, mapping_comment,
            mapped_by, reviewed_by, reviewed_at, import_batch_id, extra, created_at
        FROM mapping.source_to_concept_map
        WHERE release_id = v_parent;
    END IF;

    RETURN v_new;
END $$;

-- Fige puis publie une release (rattachée à un build CDM)
CREATE OR REPLACE FUNCTION mapping.publish_release(p_label text, p_cdm_build_ref text DEFAULT NULL)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    UPDATE mapping.release
       SET status        = 'published',
           frozen_at     = coalesce(frozen_at, now()),
           published_at  = now(),
           cdm_build_ref = coalesce(p_cdm_build_ref, cdm_build_ref)
     WHERE label = p_label AND status IN ('open', 'frozen');
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Release % introuvable ou déjà publiée/archivée', p_label;
    END IF;
END $$;

-- Promotion : staging (v1.1) -> nouvelle majeure (v2.0) ; la staging est archivée
CREATE OR REPLACE FUNCTION mapping.promote_staging(p_staging_label text, p_new_label text)
RETURNS int LANGUAGE plpgsql AS $$
DECLARE
    v_new int;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM mapping.release
         WHERE label = p_staging_label AND kind = 'staging' AND status = 'open'
    ) THEN
        RAISE EXCEPTION 'Staging ouverte % introuvable', p_staging_label;
    END IF;

    v_new := mapping.create_release(p_new_label, 'major', p_staging_label,
                                    'Promotion de ' || p_staging_label);

    UPDATE mapping.release
       SET status = 'archived', frozen_at = now()
     WHERE label = p_staging_label;

    RETURN v_new;
END $$;

-- ---------------------------------------------------------------------
-- 9. Diff entre deux releases
--    Clé métier : (source_vocabulary_id, source_code, target_concept_id, relationship_id)
--    Un changement de cible apparaît comme REMOVED + ADDED.
-- ---------------------------------------------------------------------
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
        SELECT s.source_vocabulary_id, s.source_code, s.target_concept_id, s.relationship_id,
               to_jsonb(s) - (SELECT cols FROM excl) AS j
          FROM mapping.source_to_concept_map s
          JOIN mapping.release r USING (release_id)
         WHERE r.label = p_from
    ),
    b AS (
        SELECT s.source_vocabulary_id, s.source_code, s.target_concept_id, s.relationship_id,
               to_jsonb(s) - (SELECT cols FROM excl) AS j
          FROM mapping.source_to_concept_map s
          JOIN mapping.release r USING (release_id)
         WHERE r.label = p_to
    )
    SELECT
        CASE WHEN a.j IS NULL THEN 'ADDED'
             WHEN b.j IS NULL THEN 'REMOVED'
             ELSE 'MODIFIED' END,
        coalesce(b.source_vocabulary_id, a.source_vocabulary_id),
        coalesce(b.source_code,          a.source_code),
        coalesce(b.target_concept_id,    a.target_concept_id),
        coalesce(b.relationship_id,      a.relationship_id),
        CASE WHEN a.j IS NOT NULL AND b.j IS NOT NULL THEN
            ARRAY(SELECT k FROM jsonb_object_keys(b.j) k
                   WHERE (a.j -> k) IS DISTINCT FROM (b.j -> k)
                   ORDER BY k)
        END,
        a.j,
        b.j
    FROM a
    FULL JOIN b
      ON  a.source_vocabulary_id = b.source_vocabulary_id
      AND a.source_code          = b.source_code
      AND a.target_concept_id    = b.target_concept_id
      AND a.relationship_id      = b.relationship_id
    WHERE a.j IS NULL OR b.j IS NULL OR a.j IS DISTINCT FROM b.j;
$$;

-- Lignée des releases et volumétrie
CREATE OR REPLACE VIEW mapping.v_release_lineage AS
SELECT r.label, r.kind, r.status, p.label AS parent_label, r.vocabulary_version,
       r.cdm_build_ref, r.created_at, r.published_at,
       (SELECT count(*) FROM mapping.source_to_concept_map s WHERE s.release_id = r.release_id) AS n_mappings
  FROM mapping.release r
  LEFT JOIN mapping.release p ON p.release_id = r.parent_release_id;

-- ---------------------------------------------------------------------
-- 10. Contrôle qualité des cibles (contre le vocabulaire chargé)
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW mapping.v_mapping_quality AS
SELECT s.release_id, s.stcm_id, s.source_vocabulary_id, s.source_code, s.target_concept_id,
       CASE
           WHEN s.target_concept_id = 0           THEN 'UNMAPPED'
           WHEN c.concept_id IS NULL              THEN 'TARGET_NOT_FOUND'
           WHEN c.invalid_reason IS NOT NULL      THEN 'TARGET_INVALID'
           WHEN c.standard_concept IS DISTINCT FROM 'S' THEN 'TARGET_NOT_STANDARD'
           WHEN s.target_vocabulary_id <> c.vocabulary_id THEN 'VOCABULARY_MISMATCH'
           WHEN s.domain_id IS NOT NULL AND s.domain_id <> c.domain_id THEN 'DOMAIN_MISMATCH'
           ELSE 'OK'
       END AS quality_flag,
       c.concept_name, c.domain_id AS target_domain_id, c.standard_concept, c.invalid_reason
  FROM mapping.source_to_concept_map s
  LEFT JOIN vocab.concept c ON c.concept_id = s.target_concept_id;

-- ---------------------------------------------------------------------
-- 11. Vue consommée par le pipeline (format CDM v5.4 strict)
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW mapping.v_stcm_published AS
SELECT s.source_code, s.source_concept_id, s.source_vocabulary_id, s.source_code_description,
       s.target_concept_id, s.target_vocabulary_id, s.valid_start_date, s.valid_end_date,
       s.invalid_reason
  FROM mapping.source_to_concept_map s
 WHERE s.release_id = (
        SELECT release_id FROM mapping.release
         WHERE status = 'published'
         ORDER BY published_at DESC
         LIMIT 1)
   AND s.mapping_status <> 'IGNORED';
