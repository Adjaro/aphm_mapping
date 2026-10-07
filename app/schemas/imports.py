"""Schémas de l'assistant d'import et description des colonnes importables."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

LoadMode = Literal["insert", "upsert", "replace_vocabulary"]

LOAD_MODE_LABELS: dict[str, str] = {
    "insert": "Insertion : rejette les lignes dont la clé existe déjà",
    "upsert": "Mise à jour : met à jour les lignes existantes, insère les nouvelles",
    "replace_vocabulary": "Remplacement : supprime puis recharge tout le vocabulaire source dans la release",
}

MAPPING_STATUSES = ("APPROVED", "UNCHECKED", "FLAGGED", "IGNORED")
EQUIVALENCES = ("EQUAL", "EQUIVALENT", "WIDER", "NARROWER", "INEXACT", "UNMATCHED")
RELATIONSHIPS = ("Maps to", "Maps to value", "Maps to unit")


@dataclass(frozen=True)
class ImportColumn:
    """Colonne de source_to_concept_map alimentable par un import."""

    name: str
    label: str
    required: bool = False
    aliases: tuple[str, ...] = ()


# Liste blanche des colonnes CDM / extensions importables (l'ordre est celui du formulaire).
# Les alias couvrent notamment les exports Usagi.
IMPORT_COLUMNS: tuple[ImportColumn, ...] = (
    ImportColumn("source_code", "Code source", True, ("sourcecode", "code", "code_local")),
    ImportColumn(
        "source_vocabulary_id", "Vocabulaire source", True, ("sourcevocabulary", "sourcevocabularyid")
    ),
    ImportColumn(
        "source_code_description", "Description", False, ("sourcename", "description", "libelle", "label")
    ),
    ImportColumn("source_concept_id", "ID concept source"),
    ImportColumn("target_concept_id", "ID concept cible", True, ("conceptid", "concept_id")),
    ImportColumn("target_vocabulary_id", "Vocabulaire cible", False, ("vocabulary_id", "vocabularyid")),
    ImportColumn("relationship_id", "Relation", False, ("mappingtype",)),
    ImportColumn("domain_id", "Domaine", False, ("domainid", "domain")),
    ImportColumn("valid_start_date", "Début de validité"),
    ImportColumn("valid_end_date", "Fin de validité"),
    ImportColumn("invalid_reason", "Motif d'invalidité"),
    ImportColumn("source_frequency", "Fréquence source", False, ("sourcefrequency", "frequency", "nbr")),
    ImportColumn("mapping_status", "Statut", False, ("mappingstatus", "status")),
    ImportColumn("equivalence", "Équivalence"),
    ImportColumn("mapping_comment", "Commentaire", False, ("comment", "commentaire")),
    ImportColumn("mapped_by", "Mappé par", False, ("createdby", "created_by", "auteur")),
    ImportColumn("reviewed_by", "Revu par", False, ("statussetby", "reviewer")),
)

IMPORT_COLUMN_NAMES = tuple(column.name for column in IMPORT_COLUMNS)


class ImportUploadIn(BaseModel):
    source_vocabulary_id: str | None = Field(default=None, max_length=20)


class ColumnChoiceIn(BaseModel):
    """Choix pour une colonne cible : colonne du fichier OU valeur par défaut OU rien."""

    file_column: str | None = None
    default_value: str | None = None


class ColumnMappingIn(BaseModel):
    sheet_name: str | None = None
    load_mode: LoadMode = "upsert"
    choices: dict[str, ColumnChoiceIn] = Field(default_factory=dict)


@dataclass
class FilePreviewOut:
    columns: list[str]
    rows: list[list[str]]
    encoding: str | None = None
    separator: str | None = None
    sheets: list[str] = field(default_factory=list)
    sheet_name: str | None = None


@dataclass
class LoadResultOut:
    rows_read: int
    rows_inserted: int
    rows_updated: int
    rows_unchanged: int
    rows_deleted: int
    rows_rejected: int
    status: str
