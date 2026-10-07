"""Charge CONCEPT.csv et VOCABULARY.csv (export Athena) dans le schéma vocab.

Supprime les index de 02_vocab_indexes.sql, vide les tables, charge les fichiers
par COPY puis rejoue 02_vocab_indexes.sql.

Usage : python scripts/load_vocab.py --dir /chemin/athena [--database-url URL]
"""

import argparse
import logging
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BASE_DIR, get_settings  # noqa: E402
from app.db import libpq_dsn  # noqa: E402

logger = logging.getLogger("load_vocab")

INDEX_SCRIPT = BASE_DIR / "db" / "ddl" / "02_vocab_indexes.sql"
DROP_INDEXES = """
DROP INDEX IF EXISTS vocab.idx_concept_vocab_code;
DROP INDEX IF EXISTS vocab.idx_concept_name_trgm;
DROP INDEX IF EXISTS vocab.idx_concept_code_trgm;
DROP INDEX IF EXISTS vocab.idx_concept_std_domain;
"""
COPY_CONCEPT = "COPY vocab.concept FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', HEADER true, QUOTE E'\\b')"
COPY_VOCABULARY = (
    "COPY vocab.vocabulary FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', HEADER true, QUOTE E'\\b')"
)
BLOCK_SIZE = 1 << 20


def copy_file(cur: psycopg.Cursor, statement: str, path: Path) -> None:
    logger.info("Chargement de %s", path)
    with cur.copy(statement) as copy, path.open("rb") as handle:
        while block := handle.read(BLOCK_SIZE):
            copy.write(block)


def load_vocab(database_url: str, athena_dir: Path) -> None:
    concept_file = athena_dir / "CONCEPT.csv"
    vocabulary_file = athena_dir / "VOCABULARY.csv"
    for path in (concept_file, vocabulary_file):
        if not path.is_file():
            raise FileNotFoundError(f"Fichier introuvable : {path}")

    with psycopg.connect(libpq_dsn(database_url)) as conn:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(DROP_INDEXES)
            cur.execute("TRUNCATE vocab.concept, vocab.vocabulary")
            copy_file(cur, COPY_CONCEPT, concept_file)
            copy_file(cur, COPY_VOCABULARY, vocabulary_file)
        logger.info("Reconstruction des index (02_vocab_indexes.sql)")
        conn.execute(INDEX_SCRIPT.read_text(encoding="utf-8"))
        conn.commit()
        count = conn.execute("SELECT count(*) FROM vocab.concept").fetchone()
        version = conn.execute(
            "SELECT vocabulary_version FROM vocab.vocabulary WHERE vocabulary_id = 'None'"
        ).fetchone()
    logger.info("%s concepts chargés, version %s", count[0] if count else 0, version[0] if version else "?")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dir", required=True, type=Path, help="Répertoire de l'export Athena")
    parser.add_argument("--database-url", default=get_settings().database_url)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        load_vocab(args.database_url, args.dir)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
