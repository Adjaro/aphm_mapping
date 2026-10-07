"""Recherche à facettes, chiffres clés, fiche d'un code source et synthèse qualité."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import RowMapping
from sqlalchemy.orm import Session

from app.models import CustomColumn, Release
from app.repositories import audit_repo, custom_column_repo, stcm_repo, vocab_repo
from app.schemas.search import (
    FACET_LABELS,
    FACET_VALUE_HELP,
    NULL_VALUE,
    FacetOut,
    FacetValueOut,
    SearchFilter,
    facet_value_label,
)

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
AUDIT_IGNORED_FIELDS = {"updated_at"}


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


def search(session: Session, release: Release, flt: SearchFilter) -> SearchResultOut:
    flt = flt.normalized()
    with session.begin():
        total = stcm_repo.count_results(session, release.release_id, flt)
        pages = max(1, -(-total // flt.page_size))
        if flt.page > pages:
            flt.page = pages
        rows = stcm_repo.search(session, release.release_id, flt)
        counts = stcm_repo.facet_counts(session, release.release_id, flt)
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


def _build_facets(flt: SearchFilter, counts: dict[str, list[tuple[str | None, int]]]) -> list[FacetOut]:
    facets: list[FacetOut] = []
    for name, label in FACET_LABELS.items():
        selected = set(flt.facet_values(name))
        facet = FacetOut(name=name, label=label)
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


def key_figures(session: Session, release: Release) -> RowMapping:
    with session.begin():
        return stcm_repo.key_figures(session, release.release_id)


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
