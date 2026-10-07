"""Onglet « Comparer » : tableau de bord et liste par code entre deux releases."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import RowMapping
from sqlalchemy.orm import Session

from app.repositories import compare_repo, release_repo
from app.schemas.compare import CHANGE_KIND_LABELS, COMPARE_FACETS, PAGE_SIZE, CompareFilter
from app.schemas.search import FacetOut, FacetValueOut
from app.services.errors import BusinessError


@dataclass
class CompareOut:
    filter: CompareFilter
    sizes: dict[str, RowMapping]
    kind_totals: dict[str, int]
    matrix: list[dict[str, Any]]
    field_totals: list[tuple[str, int]]
    transitions: Sequence[RowMapping]
    facets: list[FacetOut]
    rows: Sequence[RowMapping]
    total: int
    pages: int
    kinds: dict[str, str] = field(default_factory=lambda: dict(CHANGE_KIND_LABELS))


def default_labels(session: Session) -> tuple[str, str]:
    """Par défaut : la release la plus récente comparée à sa parente (sinon à la précédente).

    Une majeure issue d'une promotion est une copie de sa staging archivée : on la compare
    alors à la parente de cette staging (la majeure précédente).
    """
    with session.begin():
        releases = release_repo.list_releases(session)
        if len(releases) < 2:
            return "", ""
        latest = releases[0]
        parent = (
            release_repo.get_by_id(session, latest.parent_release_id) if latest.parent_release_id else None
        )
        while parent is not None and parent.kind == "staging" and parent.status == "archived":
            grandparent = (
                release_repo.get_by_id(session, parent.parent_release_id)
                if parent.parent_release_id
                else None
            )
            if grandparent is None:
                break
            parent = grandparent
        return (parent.label if parent else releases[1].label), latest.label


def compare(session: Session, flt: CompareFilter) -> CompareOut:
    if flt.from_label == flt.to_label:
        raise BusinessError("Choisir deux releases différentes.")
    with session.begin():
        for label in (flt.from_label, flt.to_label):
            if release_repo.get_by_label(session, label) is None:
                raise BusinessError(f"Release {label} introuvable.")
        compare_repo.materialize(session, flt.from_label, flt.to_label)
        sizes = compare_repo.release_sizes(session, flt.from_label, flt.to_label)
        matrix_rows = compare_repo.matrix(session)
        transitions = compare_repo.status_transitions(session)
        facet_rows = compare_repo.facets(session, flt)
        rows = compare_repo.rows(session, flt, PAGE_SIZE, (flt.page - 1) * PAGE_SIZE)
    total = int(rows[0]["total"]) if rows else 0
    kind_totals, matrix = _matrix(matrix_rows)
    facets = _facets(flt, facet_rows)
    field_facet = next(f for f in facets if f.name == "field")
    return CompareOut(
        filter=flt,
        sizes=sizes,
        kind_totals=kind_totals,
        matrix=matrix,
        field_totals=[(v.value, v.count) for v in field_facet.values] if not flt.field else [],
        transitions=transitions,
        facets=facets,
        rows=rows,
        total=total,
        pages=max(1, -(-total // PAGE_SIZE)),
    )


def _matrix(rows: Sequence[RowMapping]) -> tuple[dict[str, int], list[dict[str, Any]]]:
    totals = dict.fromkeys(CHANGE_KIND_LABELS, 0)
    lines: dict[str, dict[str, Any]] = {}
    for row in rows:
        line = lines.setdefault(
            row["source_vocabulary_id"], {"source_vocabulary_id": row["source_vocabulary_id"]}
        )
        line[row["change_kind"]] = int(row["n"])
        totals[row["change_kind"]] += int(row["n"])
    for line in lines.values():
        line["total"] = sum(line.get(kind, 0) for kind in CHANGE_KIND_LABELS)
    return totals, sorted(lines.values(), key=lambda line: -int(line["total"]))


def _facets(flt: CompareFilter, rows: Sequence[RowMapping]) -> list[FacetOut]:
    facets = {name: FacetOut(name=name, label=label) for name, label in COMPARE_FACETS.items()}
    for row in rows:
        selected = row["value"] in getattr(flt, row["facet"])
        label = (
            CHANGE_KIND_LABELS.get(row["value"], row["value"])
            if row["facet"] == "change_kind"
            else row["value"]
        )
        facets[row["facet"]].values.append(FacetValueOut(row["value"], label, int(row["n"]), selected))
    return list(facets.values())
