"""Connexion Athena, correspondance des vocabulaires et filtres de la comparaison."""

from urllib.parse import urlencode

from pydantic import BaseModel, Field

PAGE_SIZE = 50
SCHEMA_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"

COMPARISON_STATUS_LABELS: dict[str, str] = {
    "SAME": "Identique à Athena",
    "DIFFERENT": "Différent d'Athena",
    "MISSING_LOCAL": "Non mappé chez nous, mappé dans Athena",
    "ATHENA_NO_MAPPING": "Code sans « Maps to » dans Athena",
    "CODE_NOT_IN_ATHENA": "Code absent d'Athena",
}

ATHENA_FACETS: dict[str, str] = {
    "status": "Comparaison",
    "source_vocabulary": "Vocabulaire source",
    "mapping_status": "Statut local",
}


class AthenaConnectionIn(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=5432, ge=1, le=65535)
    database_name: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=255)
    password: str | None = None
    schema_name: str = Field(default="public", pattern=SCHEMA_PATTERN, max_length=63)


class AthenaVocabularyMapIn(BaseModel):
    source_vocabulary_id: str = Field(min_length=1, max_length=20)
    athena_vocabulary_id: str = Field(min_length=1, max_length=20)
    ignore_dots: bool = True
    ignore_case: bool = True


class AthenaFilter(BaseModel):
    release: str | None = None
    query: str = ""
    status: list[str] = Field(default_factory=list)
    source_vocabulary: list[str] = Field(default_factory=list)
    mapping_status: list[str] = Field(default_factory=list)
    page: int = Field(default=1, ge=1)

    def query_params(self, **overrides: object) -> list[tuple[str, str]]:
        data = self.model_dump()
        data.update(overrides)
        params: list[tuple[str, str]] = []
        for key in ("release", "query", *ATHENA_FACETS, "page"):
            value = data.get(key)
            if value in (None, "", []):
                continue
            if isinstance(value, list):
                params.extend((key, str(v)) for v in value)
            else:
                params.append((key, str(value)))
        return params

    def url(self, path: str = "/athena", /, **overrides: object) -> str:
        return f"{path}?{urlencode(self.query_params(**overrides))}"
