"""Formulaire de correction manuelle d'un mapping."""

from pydantic import BaseModel, Field

from app.schemas.imports import EQUIVALENCES, MAPPING_STATUSES, RELATIONSHIPS


class MappingEditIn(BaseModel):
    target_concept_id: int = Field(ge=0)
    relationship_id: str = "Maps to"
    mapping_status: str = "UNCHECKED"
    equivalence: str | None = None
    mapping_comment: str | None = Field(default=None, max_length=4000)
    custom_values: dict[str, str] = Field(default_factory=dict)

    def field_errors(self) -> dict[str, str]:
        """Contrôles de listes fermées (messages affichés sous les champs)."""
        errors: dict[str, str] = {}
        if self.relationship_id not in RELATIONSHIPS:
            errors["relationship_id"] = "Relation non autorisée."
        if self.mapping_status not in MAPPING_STATUSES:
            errors["mapping_status"] = "Statut non autorisé."
        if self.equivalence and self.equivalence not in EQUIVALENCES:
            errors["equivalence"] = "Équivalence non autorisée."
        return errors
