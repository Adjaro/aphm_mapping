"""Filtres de la recherche à facettes (état reflété dans l'URL)."""

from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlencode

from pydantic import BaseModel, Field

PAGE_SIZES = (15, 30, 50, 100)
DEFAULT_PAGE_SIZE = 30
NULL_VALUE = "_none"

# Paramètre d'URL -> libellé affiché ; l'ordre est celui du panneau de facettes
FACET_LABELS: dict[str, str] = {
    "source_vocabulary": "Vocabulaire source",
    "domain": "Domaine",
    "target_vocabulary": "Vocabulaire cible",
    "mapping_status": "Statut",
    "equivalence": "Équivalence",
    "relationship": "Relation",
    "quality": "Qualité",
    "validity": "Validité",
}

SORT_LABELS: dict[str, str] = {
    "source_code": "Code source",
    "source_code_description": "Description",
    "source_vocabulary_id": "Vocab. source",
    "domain_id": "Domaine",
    "target_concept_id": "ID cible",
    "concept_name": "Libellé cible",
    "target_vocabulary_id": "Vocab. cible",
    "mapping_status": "Statut",
    "quality_flag": "Qualité",
}


class SearchFilter(BaseModel):
    release: str | None = None
    query: str = ""
    source_vocabulary: list[str] = Field(default_factory=list)
    domain: list[str] = Field(default_factory=list)
    target_vocabulary: list[str] = Field(default_factory=list)
    mapping_status: list[str] = Field(default_factory=list)
    equivalence: list[str] = Field(default_factory=list)
    relationship: list[str] = Field(default_factory=list)
    quality: list[str] = Field(default_factory=list)
    validity: list[str] = Field(default_factory=list)
    import_batch: int | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = DEFAULT_PAGE_SIZE
    sort: str = "source_code"
    order: Literal["asc", "desc"] = "asc"

    def normalized(self) -> "SearchFilter":
        """Corrige les valeurs hors liste (taille de page, tri) sans lever d'erreur."""
        data = self.model_copy()
        if data.page_size not in PAGE_SIZES:
            data.page_size = DEFAULT_PAGE_SIZE
        if data.sort not in SORT_LABELS:
            data.sort = "source_code"
        data.query = data.query.strip()
        return data

    def facet_values(self, facet: str) -> list[str]:
        values: list[str] = getattr(self, facet)
        return values

    def query_params(self, **overrides: object) -> list[tuple[str, str]]:
        """Paramètres d'URL (facettes multi-valeurs répétées), avec surcharges éventuelles."""
        data = self.model_dump()
        data.update(overrides)
        params: list[tuple[str, str]] = []
        for key in ("release", "query", *FACET_LABELS, "import_batch", "page", "page_size", "sort", "order"):
            value = data.get(key)
            if value is None or value == "" or value == []:
                continue
            if isinstance(value, list):
                params.extend((key, str(v)) for v in value)
            else:
                params.append((key, str(value)))
        return params

    def url(self, path: str = "/search-terms/terms", /, **overrides: object) -> str:
        return f"{path}?{urlencode(self.query_params(**overrides))}"

    def without(self, facet: str, value: str | None = None) -> str:
        """URL de la recherche courante sans un filtre (pastille « Effacer »)."""
        if facet in FACET_LABELS:
            remaining = [v for v in self.facet_values(facet) if value is not None and v != value]
            return self.url(**{facet: remaining, "page": 1})
        return self.url(**{facet: None, "page": 1})

    def active_filters(self) -> list[tuple[str, str, str]]:
        """(paramètre, valeur, libellé) de chaque filtre actif."""
        active: list[tuple[str, str, str]] = []
        if self.query:
            active.append(("query", self.query, f"Texte : {self.query}"))
        for facet, label in FACET_LABELS.items():
            for value in self.facet_values(facet):
                shown = "(vide)" if value == NULL_VALUE else value
                active.append((facet, value, f"{label} : {shown}"))
        if self.import_batch is not None:
            active.append(("import_batch", str(self.import_batch), f"Import n° {self.import_batch}"))
        return active


@dataclass
class FacetValueOut:
    value: str
    label: str
    count: int
    selected: bool


@dataclass
class FacetOut:
    name: str
    label: str
    values: list[FacetValueOut] = field(default_factory=list)
