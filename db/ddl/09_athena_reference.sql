-- =====================================================================
-- 09 — Comparaison avec les mappings natifs d'Athena (relations « Maps to »)
-- Prérequis : 03_mapping.sql
--   * mapping.athena_connection     : bases Athena enregistrées (une seule active)
--   * mapping.athena_vocabulary_map : source_vocabulary_id local -> vocabulary_id Athena
--                                     (+ normalisation des codes : points, casse)
--   * mapping.athena_maps_to        : copie locale, à la demande, des concepts des
--                                     vocabulaires paramétrés et de leurs relations
--                                     « Maps to » / « Maps to value » valides
--   * mapping.normalize_code()      : clé de rapprochement d'un code
--   * mapping.compare_athena()      : statut de chaque mapping d'une release face à Athena
-- Le mot de passe est stocké en clair : utiliser un compte Athena en lecture seule.
-- =====================================================================

CREATE TABLE IF NOT EXISTS mapping.athena_connection (
    athena_connection_id  serial PRIMARY KEY,
    label                 text NOT NULL UNIQUE,
    host                  text NOT NULL,
    port                  int  NOT NULL DEFAULT 5432 CHECK (port BETWEEN 1 AND 65535),
    database_name         text NOT NULL,
    username              text NOT NULL,
    password              text,
    schema_name           text NOT NULL DEFAULT 'public' CHECK (schema_name ~ '^[A-Za-z_][A-Za-z0-9_]*$'),
    is_active             boolean NOT NULL DEFAULT false,
    athena_vocabulary_version text,
    last_sync_at          timestamptz,
    last_sync_status      text CHECK (last_sync_status IN ('ok', 'failed')),
    last_sync_message     text,
    last_sync_rows        int,
    created_at            timestamptz NOT NULL DEFAULT now()
);

-- Une seule connexion active à la fois
CREATE UNIQUE INDEX IF NOT EXISTS uq_athena_connection_one_active
    ON mapping.athena_connection (is_active) WHERE is_active;

CREATE TABLE IF NOT EXISTS mapping.athena_vocabulary_map (
    source_vocabulary_id  varchar(20) PRIMARY KEY,
    athena_vocabulary_id  varchar(20) NOT NULL,
    ignore_dots           boolean NOT NULL DEFAULT true,
    ignore_case           boolean NOT NULL DEFAULT true,
    created_at            timestamptz NOT NULL DEFAULT now()
);

-- Une ligne par (concept source Athena, cible) ; concept sans « Maps to » : cible NULL
CREATE TABLE IF NOT EXISTS mapping.athena_maps_to (
    vocabulary_id          varchar(20)  NOT NULL,
    concept_code           varchar(50)  NOT NULL,
    concept_id             int          NOT NULL,
    concept_name           varchar(255),
    invalid_reason         varchar(1),
    relationship_id        varchar(20),
    target_concept_id      int,
    target_concept_name    varchar(255),
    target_vocabulary_id   varchar(20),
    target_domain_id       varchar(20),
    target_standard_concept varchar(1)
);

CREATE INDEX IF NOT EXISTS idx_athena_maps_to_vocab_code ON mapping.athena_maps_to (vocabulary_id, concept_code);
CREATE INDEX IF NOT EXISTS idx_athena_maps_to_concept    ON mapping.athena_maps_to (concept_id);

CREATE OR REPLACE FUNCTION mapping.normalize_code(p_code text, p_ignore_dots boolean, p_ignore_case boolean)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN p_ignore_case THEN upper(v.code) ELSE v.code END
      FROM (SELECT CASE WHEN p_ignore_dots THEN replace(btrim(p_code), '.', '') ELSE btrim(p_code) END AS code) v;
$$;

-- Statuts :
--   SAME               : notre cible fait partie des cibles Athena (même relation)
--   DIFFERENT          : Athena propose d'autres cibles pour cette relation
--   MISSING_LOCAL      : non mappé chez nous (cible 0) alors qu'Athena propose une cible
--   ATHENA_NO_MAPPING  : code trouvé dans Athena mais sans « Maps to » pour cette relation
--   CODE_NOT_IN_ATHENA : code introuvable dans le vocabulaire Athena paramétré
-- Seuls les vocabulaires présents dans athena_vocabulary_map sont comparés.
CREATE OR REPLACE FUNCTION mapping.compare_athena(p_release_label text)
RETURNS TABLE (
    stcm_id                 bigint,
    source_vocabulary_id    varchar,
    source_code             varchar,
    source_code_description varchar,
    target_concept_id       int,
    target_concept_name     varchar,
    relationship_id         varchar,
    mapping_status          text,
    athena_vocabulary_id    varchar,
    athena_concept_id       int,
    athena_concept_code     varchar,
    athena_concept_name     varchar,
    athena_target_ids       int[],
    athena_targets          jsonb,
    comparison_status       text
) LANGUAGE sql STABLE AS $$
    WITH s AS (
        SELECT s.stcm_id,
               s.source_vocabulary_id,
               s.source_code,
               s.source_code_description,
               s.target_concept_id,
               s.relationship_id,
               s.mapping_status,
               m.athena_vocabulary_id,
               mapping.normalize_code(s.source_code, m.ignore_dots, m.ignore_case) AS code_key
          FROM mapping.source_to_concept_map s
          JOIN mapping.release r
            ON r.release_id = s.release_id
           AND r.label      = p_release_label
          JOIN mapping.athena_vocabulary_map m
            ON m.source_vocabulary_id = s.source_vocabulary_id
    ),
    a AS (
        SELECT m.source_vocabulary_id,
               mapping.normalize_code(a.concept_code, m.ignore_dots, m.ignore_case) AS code_key,
               a.*
          FROM mapping.athena_maps_to a
          JOIN mapping.athena_vocabulary_map m
            ON m.athena_vocabulary_id = a.vocabulary_id
    ),
    j AS (
        SELECT s.stcm_id,
               min(a.concept_id)                                                AS athena_concept_id,
               min(a.concept_code)                                              AS athena_concept_code,
               min(a.concept_name)                                              AS athena_concept_name,
               count(a.concept_id)                                              AS n_found,
               array_agg(DISTINCT a.target_concept_id ORDER BY a.target_concept_id)
                   FILTER (WHERE a.relationship_id = s.relationship_id)         AS target_ids,
               jsonb_agg(
                   DISTINCT jsonb_build_object(
                       'relationship_id',   a.relationship_id,
                       'target_concept_id', a.target_concept_id,
                       'concept_name',      a.target_concept_name,
                       'vocabulary_id',     a.target_vocabulary_id,
                       'domain_id',         a.target_domain_id
                   )
               ) FILTER (WHERE a.target_concept_id IS NOT NULL)                AS targets
          FROM s
          LEFT JOIN a
                 ON a.source_vocabulary_id = s.source_vocabulary_id
                AND a.code_key             = s.code_key
         GROUP BY s.stcm_id
    )
    SELECT s.stcm_id,
           s.source_vocabulary_id,
           s.source_code,
           s.source_code_description,
           s.target_concept_id,
           c.concept_name,
           s.relationship_id,
           s.mapping_status,
           s.athena_vocabulary_id,
           j.athena_concept_id,
           j.athena_concept_code,
           j.athena_concept_name,
           j.target_ids,
           j.targets,
           CASE
               WHEN j.n_found = 0                          THEN 'CODE_NOT_IN_ATHENA'
               WHEN j.target_ids IS NULL                   THEN 'ATHENA_NO_MAPPING'
               WHEN s.target_concept_id = ANY(j.target_ids) THEN 'SAME'
               WHEN s.target_concept_id = 0                THEN 'MISSING_LOCAL'
               ELSE 'DIFFERENT'
           END
      FROM s
      JOIN j ON j.stcm_id = s.stcm_id
      LEFT JOIN vocab.concept c ON c.concept_id = s.target_concept_id;
$$;
