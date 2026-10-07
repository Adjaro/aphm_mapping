"""Filtres de l'onglet « Comparer » (deux releases, vue par code source)."""

from urllib.parse import urlencode

from pydantic import BaseModel, Field

PAGE_SIZE = 50

CHANGE_KIND_LABELS: dict[str, str] = {
    "NEW_CODE": "Nouveau code",
    "REMOVED_CODE": "Code supprimé",
    "TARGET_CHANGED": "Cible changée",
    "MODIFIED": "Autre modification",
}

# Paramètre d'URL -> libellé de la facette
COMPARE_FACETS: dict[str, str] = {
    "change_kind": "Type de changement",
    "source_vocabulary": "Vocabulaire source",
    "field": "Champ modifié",
}


class CompareFilter(BaseModel):
    from_label: str = ""
    to_label: str = ""
    query: str = ""
    change_kind: list[str] = Field(default_factory=list)
    source_vocabulary: list[str] = Field(default_factory=list)
    field: list[str] = Field(default_factory=list)
    page: int = Field(default=1, ge=1)

    def query_params(self, **overrides: object) -> list[tuple[str, str]]:
        data = self.model_dump()
        data.update(overrides)
        params: list[tuple[str, str]] = [("from", str(data["from_label"])), ("to", str(data["to_label"]))]
        for key in ("query", *COMPARE_FACETS, "page"):
            value = data.get(key)
            if value in (None, "", []):
                continue
            if isinstance(value, list):
                params.extend((key, str(v)) for v in value)
            else:
                params.append((key, str(value)))
        return params

    def url(self, path: str = "/compare", /, **overrides: object) -> str:
        return f"{path}?{urlencode(self.query_params(**overrides))}"
