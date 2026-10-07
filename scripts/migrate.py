"""Applique dans l'ordre les fichiers db/ddl/*.sql non encore appliqués.

Chaque fichier est appliqué dans sa propre transaction et enregistré dans
public.schema_migration avec son checksum. Le script refuse de continuer si le
checksum d'un fichier déjà appliqué a changé.

Usage : python scripts/migrate.py [--database-url URL]
"""

import argparse
import hashlib
import logging
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BASE_DIR, get_settings  # noqa: E402
from app.db import libpq_dsn  # noqa: E402

logger = logging.getLogger("migrate")

DDL_DIR = BASE_DIR / "db" / "ddl"

CREATE_MIGRATION_TABLE = """
CREATE TABLE IF NOT EXISTS public.schema_migration (
    version     text PRIMARY KEY,
    checksum    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    """Checksum modifié ou échec d'application d'un fichier."""


def file_checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migrate(database_url: str, ddl_dir: Path = DDL_DIR) -> list[str]:
    """Applique les migrations manquantes ; renvoie la liste des versions appliquées."""
    applied_now: list[str] = []
    with psycopg.connect(libpq_dsn(database_url), autocommit=True) as conn:
        conn.execute(CREATE_MIGRATION_TABLE)
        rows = conn.execute("SELECT version, checksum FROM public.schema_migration").fetchall()
        applied = {version: checksum for version, checksum in rows}

        for path in sorted(ddl_dir.glob("*.sql")):
            checksum = file_checksum(path)
            if path.name in applied:
                if applied[path.name] != checksum:
                    raise MigrationError(
                        f"Le fichier déjà appliqué {path.name} a été modifié (checksum différent). "
                        "Créer une nouvelle migration numérotée au lieu de modifier celle-ci."
                    )
                continue
            logger.info("Application de %s", path.name)
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute(
                    "INSERT INTO public.schema_migration (version, checksum) VALUES (%s, %s)",
                    (path.name, checksum),
                )
            applied_now.append(path.name)
    return applied_now


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--database-url", default=get_settings().database_url)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        applied = migrate(args.database_url)
    except MigrationError as exc:
        logger.error("%s", exc)
        return 1
    logger.info("%d migration(s) appliquée(s)%s", len(applied), f" : {', '.join(applied)}" if applied else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
