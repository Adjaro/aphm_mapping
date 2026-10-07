"""Charge un répertoire d'exports Usagi (ex. data/) dans le référentiel.

Étapes :
  1. si vocab.concept est vide (vocabulaire Athena non chargé), alimente vocab.concept et
     vocab.vocabulary avec les concepts cibles décrits dans les fichiers (mode démonstration) ;
  2. crée la release initiale si aucune release n'existe ;
  3. importe chaque fichier par le circuit d'import standard (contrôles SQL, import_batch, rejets) ;
  4. optionnellement publie la release et ouvre la staging suivante.

Usage : python scripts/load_usagi_dir.py --dir data [--release v1.0] [--publish BUILD] [--staging v1.1]
"""

import argparse
import csv
import logging
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, libpq_dsn  # noqa: E402
from app.services import import_service, release_service  # noqa: E402
from app.services.errors import BusinessError  # noqa: E402

logger = logging.getLogger("load_usagi_dir")

USER = "chargement-initial"
VOCABULARY_VERSION = "Extrait des fichiers de mapping (démonstration)"

# Colonne Usagi -> colonne de source_to_concept_map (la première présente l'emporte)
USAGI_COLUMNS: dict[str, tuple[str, ...]] = {
    "source_code": ("sourceCode",),
    "source_code_description": ("sourceName",),
    "target_concept_id": ("conceptId",),
    "target_vocabulary_id": ("vocabulary_id",),
    "domain_id": ("domain_id",),
    "mapping_status": ("mappingStatus", "mapping_status"),
    "equivalence": ("equivalence",),
    "relationship_id": ("mappingType",),
    "mapping_comment": ("comment",),
    "mapped_by": ("createdBy", "created_by"),
    "reviewed_by": ("statusSetBy",),
    "source_frequency": ("sourceFrequency", "nbr"),
    "source_vocabulary_id": ("sourceVocabulary",),
}

# Identifiants source trop longs pour varchar(20) : identifiant court retenu
SOURCE_VOCABULARY_OVERRIDES = {
    "aphm_biologie_interpretation": "aphm_bio_interp",
    "aphm_voies_administration": "aphm_voies_admin",
}

CONCEPT_FIELDS = (
    "conceptId",
    "concept_name",
    "domain_id",
    "vocabulary_id",
    "concept_class_id",
    "standard_concept",
    "concept_code",
    "valid_start_date",
    "valid_end_date",
    "invalid_reason",
)

CREATE_TMP_CONCEPT = """
CREATE TEMP TABLE tmp_concept (LIKE vocab.concept INCLUDING DEFAULTS) ON COMMIT DROP
"""
COPY_TMP_CONCEPT = """
COPY tmp_concept (concept_id, concept_name, domain_id, vocabulary_id, concept_class_id, standard_concept,
                  concept_code, valid_start_date, valid_end_date, invalid_reason) FROM STDIN
"""
INSERT_CONCEPTS = """
INSERT INTO vocab.concept
SELECT DISTINCT ON (concept_id) *
  FROM tmp_concept
 ORDER BY concept_id
ON CONFLICT (concept_id) DO NOTHING
"""
INSERT_NO_MATCH = """
INSERT INTO vocab.concept (concept_id, concept_name, domain_id, vocabulary_id, concept_class_id,
                           standard_concept, concept_code, valid_start_date, valid_end_date)
VALUES (0, 'No matching concept', 'Metadata', 'None', 'Undefined', NULL, 'No matching concept',
        DATE '1970-01-01', DATE '2099-12-31')
ON CONFLICT (concept_id) DO NOTHING
"""
INSERT_VOCABULARIES = """
INSERT INTO vocab.vocabulary (vocabulary_id, vocabulary_name, vocabulary_version, vocabulary_concept_id)
SELECT DISTINCT c.vocabulary_id,
       c.vocabulary_id,
       CASE WHEN c.vocabulary_id = 'None' THEN %(version)s END,
       0
  FROM vocab.concept c
ON CONFLICT (vocabulary_id) DO NOTHING
"""


def usagi_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.csv") if p.is_file())


def file_header(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return next(csv.reader(handle))


def iter_concepts(paths: list[Path]) -> list[tuple[str | None, ...]]:
    rows: dict[str, tuple[str | None, ...]] = {}
    for path in paths:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for record in csv.DictReader(handle):
                concept_id = (record.get("conceptId") or "").strip()
                if concept_id and concept_id != "0" and concept_id not in rows:
                    rows[concept_id] = tuple((record.get(f) or "").strip() or None for f in CONCEPT_FIELDS)
    return list(rows.values())


def load_demo_vocabulary(database_url: str, paths: list[Path]) -> None:
    with psycopg.connect(libpq_dsn(database_url)) as conn:
        count = conn.execute("SELECT count(*) FROM vocab.concept").fetchone()
        if count and count[0] > 0:
            logger.info(
                "vocab.concept contient déjà %s concepts : pas de vocabulaire de démonstration", count[0]
            )
            return
        concepts = iter_concepts(paths)
        logger.info("Vocabulaire de démonstration : %s concepts extraits des fichiers", len(concepts))
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(CREATE_TMP_CONCEPT)
            with cur.copy(COPY_TMP_CONCEPT) as copy:
                for row in concepts:
                    copy.write_row(row)
            cur.execute(INSERT_CONCEPTS)
            cur.execute(INSERT_NO_MATCH)
            cur.execute(INSERT_VOCABULARIES, {"version": VOCABULARY_VERSION})
        conn.execute("ANALYZE vocab.concept")
        conn.commit()


def column_mapping_for(header: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for target, candidates in USAGI_COLUMNS.items():
        for candidate in candidates:
            if candidate in header:
                mapping[target] = candidate
                break
    return mapping


def source_vocabulary_for(path: Path) -> str | None:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        first = next(csv.DictReader(handle), None)
    value = (first or {}).get("sourceVocabulary") or None
    return SOURCE_VOCABULARY_OVERRIDES.get(value or "")


def import_files(paths: list[Path]) -> None:
    for path in paths:
        header = file_header(path)
        mapping = column_mapping_for(header)
        defaults: dict[str, str] = {}
        override = source_vocabulary_for(path)
        if override:
            mapping.pop("source_vocabulary_id", None)
            defaults["source_vocabulary_id"] = override
        session = SessionLocal()
        try:
            result = import_service.import_local_file(session, path, mapping, defaults, "upsert", USER)
        finally:
            session.close()
        logger.info(
            "%s : %s lues, %s chargées, %s rejetées (%s)",
            path.name,
            result.rows_read,
            result.rows_inserted + result.rows_updated + result.rows_unchanged,
            result.rows_rejected,
            result.status,
        )


def ensure_release(label: str) -> None:
    session = SessionLocal()
    try:
        if not release_service.list_releases(session):
            release_service.create_initial(session, label, "Chargement initial des fichiers Usagi", USER)
            logger.info("Release initiale %s créée", label)
    finally:
        session.close()


def finalize(release_label: str, build_ref: str | None, staging_label: str | None) -> None:
    session = SessionLocal()
    try:
        if build_ref:
            release_service.publish(session, release_label, build_ref, USER)
            logger.info("Release %s publiée (build %s)", release_label, build_ref)
        if staging_label:
            release_service.create_staging(session, staging_label, "Staging ouverte après chargement", USER)
            logger.info("Staging %s créée", staging_label)
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dir", required=True, type=Path)
    parser.add_argument("--release", default="v1.0", help="Release initiale créée si aucune n'existe")
    parser.add_argument(
        "--publish", metavar="BUILD_REF", help="Publie la release avec cette référence de build"
    )
    parser.add_argument("--staging", metavar="LABEL", help="Crée ensuite la staging (ex. v1.1)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    paths = usagi_files(args.dir)
    if not paths:
        logger.error("Aucun fichier .csv dans %s", args.dir)
        return 1
    try:
        load_demo_vocabulary(get_settings().database_url, paths)
        ensure_release(args.release)
        import_files(paths)
        finalize(args.release, args.publish, args.staging)
    except BusinessError as exc:
        logger.error("%s", exc.message)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
