"""Recherche à facettes, chiffres clés, fiche d'un code source et synthèse qualité."""

from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from sqlalchemy import RowMapping
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CustomColumn, Release
from app.repositories import audit_repo, custom_column_repo, stcm_repo, vocab_repo
from app.schemas.compare import CompareFilter
from app.schemas.search import (
    COLLAPSED_FACETS,
    FACET_LABELS,
    FACET_VALUE_HELP,
    NULL_VALUE,
    FacetOut,
    FacetValueOut,
    SearchFilter,
    facet_value_label,
)
from app.services import compare_service, release_service

# Champs comparés d'une release à l'autre dans l'onglet Historique
HISTORY_FIELDS: tuple[tuple[str, str], ...] = (
    ("targets", "Cibles"),
    ("source_code_description", "Description"),
    ("domain_id", "Domaine"),
    ("mapping_status", "Statut"),
    ("equivalence", "Équivalence"),
    ("mapping_comment", "Commentaire"),
    ("invalid_reason", "Invalidité"),
)

# Champs ignorés dans la comparaison avant / après de l'audit
AUDIT_IGNORED_FIELDS = {"updated_at", "search_text"}


@dataclass
class SearchResultOut:
    filter: SearchFilter
    rows: Sequence[RowMapping]
    total: int
    facets: list[FacetOut]
    pages: int
    target: dict[str, Any] | None = None


@dataclass
class HistoryColumnOut:
    label: str
    status: str
    present: bool
    values: dict[str, str]
    changed: set[str] = field(default_factory=set)


@dataclass
class AuditEntryOut:
    audit_id: int
    operation: str
    changed_by: str
    changed_at: Any
    release_label: str | None
    target_concept_id: Any
    changes: list[tuple[str, Any, Any]]


@dataclass
class MappingDetailOut:
    release: Release
    source_vocabulary_id: str
    source_code: str
    rows: Sequence[RowMapping]
    custom_columns: Sequence[CustomColumn]
    history: list[HistoryColumnOut]
    history_fields: tuple[tuple[str, str], ...]
    audit: list[AuditEntryOut]


# Cache des recherches : une release publiée ou archivée ne change plus (trigger stcm_guard) ;
# pour une release ouverte, l'empreinte des données invalide le cache après chaque modification.
SEARCH_CACHE_SIZE = 256
_search_cache: OrderedDict[tuple[int, str, str], "SearchResultOut"] = OrderedDict()
_cache_lock = Lock()


def _cache_key(session: Session, release: Release, flt: SearchFilter) -> tuple[int, str, str]:
    if release.status in ("published", "archived"):
        version = "figée"
    else:
        with session.begin():
            version = stcm_repo.data_version(session, release.release_id)
    return release.release_id, version, flt.model_dump_json()


def search(session: Session, release: Release, flt: SearchFilter) -> SearchResultOut:
    flt = flt.normalized()
    key = _cache_key(session, release, flt)
    with _cache_lock:
        cached = _search_cache.get(key)
        if cached is not None:
            _search_cache.move_to_end(key)
            return cached
    result = _search(session, release, flt)
    with _cache_lock:
        _search_cache[key] = result
        while len(_search_cache) > SEARCH_CACHE_SIZE:
            _search_cache.popitem(last=False)
    return result


def clear_search_cache() -> None:
    with _cache_lock:
        _search_cache.clear()
        _figures_cache.clear()


def _search(session: Session, release: Release, flt: SearchFilter) -> SearchResultOut:
    with session.begin():
        stcm_repo.prepare_search(session, release.release_id, flt)
        total = stcm_repo.count_results(session, flt)
        pages = max(1, -(-total // flt.page_size))
        if flt.page > pages:
            flt.page = pages
        rows = stcm_repo.search(session, flt)
        counts = stcm_repo.facet_counts(session, flt)
        target = _target(session, release, flt.target_concept) if flt.target_concept is not None else None
    facets = _build_facets(flt, counts)
    return SearchResultOut(filter=flt, rows=rows, total=total, facets=facets, pages=pages, target=target)


def _target(session: Session, release: Release, concept_id: int) -> dict[str, Any]:
    """Résumé du concept cible filtré : libellé (vocabulaire chargé) et codes source qui y pointent."""
    concept = vocab_repo.get_concept(session, concept_id)
    summary = stcm_repo.target_summary(session, release.release_id, concept_id)
    return {
        "concept_id": concept_id,
        "concept": dict(concept) if concept else None,
        "n_source_codes": int(summary["n_source_codes"]),
        "source_vocabularies": summary["source_vocabularies"] or [],
    }


@dataclass
class SuggestionsOut:
    sources: Sequence[RowMapping]
    targets: Sequence[RowMapping]


SUGGESTION_LIMIT = 6


def suggestions(session: Session, release: Release, query: str, scope: str) -> SuggestionsOut:
    """Suggestions instantanées : codes source et concepts cibles correspondant à la saisie."""
    query = query.strip()
    if len(query) < 2 and not query.isdigit():
        return SuggestionsOut([], [])
    with session.begin():
        stcm_repo.apply_search_settings(session)
        sources = (
            stcm_repo.suggest_source_codes(session, release.release_id, query, SUGGESTION_LIMIT)
            if scope in ("all", "source")
            else []
        )
        targets = (
            stcm_repo.suggest_targets(session, release.release_id, query, SUGGESTION_LIMIT)
            if scope in ("all", "target")
            else []
        )
    return SuggestionsOut(sources, targets)


def warm_up(session: Session) -> None:
    """Précalcule la page de recherche par défaut (dernière release publiée) au démarrage."""
    release = release_service.resolve_release(session, None)
    if release is not None:
        search(session, release, SearchFilter(release=release.label))
        key_figures(session, release)
    from_label, to_label = compare_service.default_labels(session)
    if from_label and to_label:
        compare_service.compare(session, CompareFilter(from_label=from_label, to_label=to_label))


def _build_facets(flt: SearchFilter, counts: dict[str, list[tuple[str | None, int]]]) -> list[FacetOut]:
    facets: list[FacetOut] = []
    for name, label in FACET_LABELS.items():
        if name == "quality" and not get_settings().show_quality:
            continue
        selected = set(flt.facet_values(name))
        facet = FacetOut(name=name, label=label, collapsed=name in COLLAPSED_FACETS and not selected)
        seen: set[str] = set()
        for value, count in counts.get(name, []):
            key = NULL_VALUE if value is None else value
            seen.add(key)
            facet.values.append(
                FacetValueOut(
                    value=key,
                    label=facet_value_label(name, key),
                    count=count,
                    selected=key in selected,
                    help=FACET_VALUE_HELP.get(name, {}).get(key),
                )
            )
        # Une valeur cochée sans résultat reste visible (compteur 0) pour pouvoir la décocher
        for key in sorted(selected - seen):
            facet.values.append(
                FacetValueOut(value=key, label=facet_value_label(name, key), count=0, selected=True)
            )
        facets.append(facet)
    return facets


_figures_cache: dict[tuple[int, str], RowMapping] = {}


def key_figures(session: Session, release: Release) -> RowMapping:
    """Chiffres clés de l'accueil, réutilisés tant que les données de la release ne changent pas."""
    key = _cache_key(session, release, SearchFilter())[:2]
    with _cache_lock:
        cached = _figures_cache.get(key)
    if cached is not None:
        return cached
    with session.begin():
        figures = stcm_repo.key_figures(session, release.release_id)
    with _cache_lock:
        if len(_figures_cache) > 64:
            _figures_cache.clear()
        _figures_cache[key] = figures
    return figures


def quality_summary(session: Session, release: Release) -> tuple[list[str], list[dict[str, Any]]]:
    """Tableau croisé vocabulaire source × drapeau qualité."""
    with session.begin():
        rows = stcm_repo.quality_summary(session, release.release_id)
    flags = sorted({row["quality_flag"] for row in rows}, key=lambda f: (f != "OK", f))
    table: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = table.setdefault(
            row["source_vocabulary_id"], {"source_vocabulary_id": row["source_vocabulary_id"]}
        )
        entry[row["quality_flag"]] = int(row["n"])
    lines = sorted(table.values(), key=lambda e: e["source_vocabulary_id"])
    for entry in lines:
        entry["total"] = sum(entry.get(flag, 0) for flag in flags)
    return flags, lines


def mapping_detail(
    session: Session, release: Release, source_vocabulary_id: str, source_code: str
) -> MappingDetailOut | None:
    with session.begin():
        rows = stcm_repo.detail_rows(session, release.release_id, source_vocabulary_id, source_code)
        history_rows = stcm_repo.history_rows(session, source_vocabulary_id, source_code)
        audit_rows = audit_repo.code_audit(session, source_vocabulary_id, source_code)
        custom_columns = custom_column_repo.list_columns(session)
    if not rows and not any(row["target_concept_id"] is not None for row in history_rows):
        return None
    return MappingDetailOut(
        release=release,
        source_vocabulary_id=source_vocabulary_id,
        source_code=source_code,
        rows=rows,
        custom_columns=custom_columns,
        history=_build_history(history_rows),
        history_fields=HISTORY_FIELDS,
        audit=[_audit_entry(row) for row in audit_rows],
    )


def _build_history(rows: Sequence[RowMapping]) -> list[HistoryColumnOut]:
    """Une colonne par release ; les valeurs différentes de la release précédente sont marquées."""
    columns: dict[str, HistoryColumnOut] = {}
    grouped: dict[str, list[RowMapping]] = {}
    for row in rows:
        if row["label"] not in columns:
            columns[row["label"]] = HistoryColumnOut(
                label=row["label"], status=row["status"], present=False, values={}
            )
            grouped[row["label"]] = []
        if row["target_concept_id"] is not None:
            columns[row["label"]].present = True
            grouped[row["label"]].append(row)
    previous: HistoryColumnOut | None = None
    for label, column in columns.items():
        column.values = _history_values(grouped[label])
        if previous is not None and (previous.present or column.present):
            column.changed = {
                name for name, _ in HISTORY_FIELDS if column.values[name] != previous.values[name]
            }
        previous = column
    return list(columns.values())


def _history_values(rows: list[RowMapping]) -> dict[str, str]:
    def join(name: str) -> str:
        values = [str(row[name]) for row in rows if row[name] not in (None, "")]
        return " | ".join(dict.fromkeys(values))

    values = {name: join(name) for name, _ in HISTORY_FIELDS if name != "targets"}
    values["targets"] = " | ".join(
        f"{row['relationship_id']} → {row['target_concept_id']} "
        f"{row['concept_name'] or '(concept introuvable)'}"
        for row in rows
    )
    return values


def _audit_entry(row: RowMapping) -> AuditEntryOut:
    old = row["old_row"] or {}
    new = row["new_row"] or {}
    if row["operation"] == "UPDATE":
        keys = sorted(
            k for k in set(old) | set(new) if k not in AUDIT_IGNORED_FIELDS and old.get(k) != new.get(k)
        )
        changes = [(k, old.get(k), new.get(k)) for k in keys]
    else:
        changes = [
            (k, old.get(k), None)
            for k in (
                "target_concept_id",
                "relationship_id",
                "mapping_status",
                "equivalence",
                "mapping_comment",
            )
        ]
    return AuditEntryOut(
        audit_id=row["audit_id"],
        operation=row["operation"],
        changed_by=row["changed_by"],
        changed_at=row["changed_at"],
        release_label=row["release_label"],
        target_concept_id=old.get("target_concept_id"),
        changes=changes,
    )
