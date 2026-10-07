"""Formulaire de déclaration d'une colonne personnalisée."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

DataType = Literal["text", "integer", "numeric", "date", "boolean"]

DATA_TYPE_LABELS: dict[str, str] = {
    "text": "Texte",
    "integer": "Entier",
    "numeric": "Numérique",
    "date": "Date",
    "boolean": "Booléen",
}


class CustomColumnIn(BaseModel):
    column_name: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=63)
    label: str = Field(min_length=1, max_length=200)
    data_type: DataType = "text"
    allowed_values: list[str] = Field(default_factory=list)
    is_required: bool = False
    description: str | None = None

    @field_validator("allowed_values", mode="before")
    @classmethod
    def split_values(cls, value: object) -> object:
        """Accepte une saisie « valeur1, valeur2 » ou une liste."""
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return value
