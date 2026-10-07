-- =====================================================================
-- 01 — Tables de vocabulaire (schéma vocab) — lecture seule pour l'appli
-- Base : omop_referentiel
-- Le vocabulaire n'est PAS navigable dans l'application : il sert uniquement à
--   * afficher libellé / domaine / statut standard des concepts cibles,
--   * valider les cibles à l'import et alimenter le contrôle qualité,
--   * proposer des concepts cibles lors d'une correction manuelle.
-- Seules CONCEPT et VOCABULARY (format OMOP CDM v5.4) sont nécessaires.
-- Chargement : CSV Athena (tabulés, dates YYYYMMDD) par \copy, puis 02_vocab_indexes.sql.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE SCHEMA IF NOT EXISTS vocab;

CREATE TABLE IF NOT EXISTS vocab.concept (
    concept_id        integer      NOT NULL,
    concept_name      varchar(255) NOT NULL,
    domain_id         varchar(20)  NOT NULL,
    vocabulary_id     varchar(20)  NOT NULL,
    concept_class_id  varchar(20)  NOT NULL,
    standard_concept  varchar(1),
    concept_code      varchar(50)  NOT NULL,
    valid_start_date  date         NOT NULL,
    valid_end_date    date         NOT NULL,
    invalid_reason    varchar(1),
    CONSTRAINT pk_concept PRIMARY KEY (concept_id)
);

CREATE TABLE IF NOT EXISTS vocab.vocabulary (
    vocabulary_id          varchar(20)  NOT NULL,
    vocabulary_name        varchar(255) NOT NULL,
    vocabulary_reference   varchar(255),
    vocabulary_version     varchar(255),
    vocabulary_concept_id  integer      NOT NULL,
    CONSTRAINT pk_vocabulary PRIMARY KEY (vocabulary_id)
);

-- Chargement (psql) :
-- \copy vocab.concept    FROM 'CONCEPT.csv'    WITH (FORMAT csv, DELIMITER E'\t', HEADER true, QUOTE E'\b')
-- \copy vocab.vocabulary FROM 'VOCABULARY.csv' WITH (FORMAT csv, DELIMITER E'\t', HEADER true, QUOTE E'\b')
-- Version chargée : SELECT vocabulary_version FROM vocab.vocabulary WHERE vocabulary_id = 'None';
