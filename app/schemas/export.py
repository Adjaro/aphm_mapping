"""Formats et filtres d'export d'une release."""

from typing import Literal
from urllib.parse import urlencode

from pydantic import BaseModel, Field

from app.schemas.imports import MAPPING_STATUSES

ExportFormat = Literal["properties", "csv_cdm", "csv_full"]

FORMAT_LABELS: dict[str, tuple[str, str, str]] = {
    # format -> (libellé, description, icône Bootstrap Icons)
    "properties": (
        "Properties",
        "Un fichier <Domaine>.properties par domaine, une ligne code_source=id_cible[,id_cible…]. "
        "Téléchargé en ZIP ou écrit dans le dossier du serveur.",
        "bi-file-earmark-code",
    ),
    "csv_cdm": (
        "CSV CDM",
        "Table source_to_concept_map au format OMOP CDM v5.4 strict (9 colonnes, séparateur virgule).",
        "bi-filetype-csv",
    ),
    "csv_full": (
        "CSV complet",
        "Toutes les colonnes : CDM, extensions AP-HM, colonnes personnalisées, "
        "libellé et qualité de la cible "
        "(séparateur point-virgule, ouverture directe dans Excel).",
        "bi-file-earmark-spreadsheet",
    ),
}

DEFAULT_STATUSES = [s for s in MAPPING_STATUSES if s != "IGNORED"]


class ExportFilter(BaseModel):
    release: str | None = None
    format: ExportFormat = "properties"
    source_vocabulary: list[str] = Field(default_factory=list)
    mapping_status: list[str] = Field(default_factory=lambda: list(DEFAULT_STATUSES))
    include_unmapped: bool = False

    def query_string(self) -> str:
        params: list[tuple[str, str]] = [("format", self.format)]
        if self.release:
            params.append(("release", self.release))
        params.extend(("source_vocabulary", v) for v in self.source_vocabulary)
        params.extend(("mapping_status", v) for v in self.mapping_status)
        if self.include_unmapped:
            params.append(("include_unmapped", "1"))
        return urlencode(params)
