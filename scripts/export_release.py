"""Exporte une release (properties, CSV CDM ou CSV complet), pour le pipeline ou une tâche planifiée.

Exemples :
  python scripts/export_release.py --release v2.0 --format properties
      -> écrit <EXPORT_DIR>/<PROPERTIES_SUBDIR>/<Domaine>.properties (data/30_properties par défaut)
  python scripts/export_release.py --release v2.0 --format csv_cdm --out export/stcm_v2.0.csv
  python scripts/export_release.py --format properties --vocabulary ICD10 --vocabulary UCD --zip out.zip

Sans --release : dernière release publiée.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.schemas.export import DEFAULT_STATUSES, FORMAT_LABELS, ExportFilter  # noqa: E402
from app.schemas.imports import MAPPING_STATUSES  # noqa: E402
from app.services import export_service, release_service  # noqa: E402

logger = logging.getLogger("export_release")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--release", help="Libellé de la release (défaut : dernière publiée)")
    parser.add_argument("--format", choices=sorted(FORMAT_LABELS), default="properties")
    parser.add_argument("--vocabulary", action="append", default=[], help="Vocabulaire source (répétable)")
    parser.add_argument(
        "--status", action="append", choices=MAPPING_STATUSES, help="Statut inclus (répétable)"
    )
    parser.add_argument("--include-unmapped", action="store_true", help="Inclure les cibles 0")
    parser.add_argument("--out", type=Path, help="Fichier CSV de sortie (formats csv_*)")
    parser.add_argument("--zip", type=Path, help="Properties : écrire un ZIP au lieu du dossier configuré")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    session = SessionLocal()
    try:
        release = release_service.resolve_release(session, args.release)
    finally:
        session.close()
    if release is None:
        logger.error("Release %s introuvable", args.release or "(dernière publiée)")
        return 1
    flt = ExportFilter(
        release=release.label,
        format=args.format,
        source_vocabulary=args.vocabulary,
        mapping_status=args.status or list(DEFAULT_STATUSES),
        include_unmapped=args.include_unmapped,
    )
    if flt.format == "properties":
        if args.zip:
            args.zip.write_bytes(export_service.properties_zip(release.release_id, flt))
            logger.info("Release %s : ZIP écrit dans %s", release.label, args.zip)
        else:
            result = export_service.write_properties(release.release_id, flt)
            logger.info(
                "Release %s : %s fichiers écrits dans %s", release.label, len(result.files), result.directory
            )
            if result.removed:
                logger.info("Anciens fichiers supprimés : %s", ", ".join(result.removed))
        return 0
    out = args.out or Path(f"source_to_concept_map_{release.label}_{flt.format}.csv")
    stream = (
        export_service.release_cdm_csv(release.release_id, flt)
        if flt.format == "csv_cdm"
        else export_service.release_full_csv(release.release_id, flt)
    )
    with out.open("w", encoding="utf-8", newline="") as handle:
        for chunk in stream:
            handle.write(chunk)
    logger.info("Release %s : %s écrit", release.label, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
