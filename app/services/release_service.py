"""Cycle de vie des releases : orchestration des fonctions PostgreSQL de versionnement."""

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TypeVar

from sqlalchemy import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models import Release
from app.repositories import audit_repo, release_repo, vocab_repo
from app.services.errors import BusinessError, database_message

logger = logging.getLogger(__name__)

T = TypeVar("T")

LABEL_PATTERN = re.compile(r"^v(\d+)\.(\d+)$")
DIFF_PAGE_SIZE = 50


@dataclass
class DiffOut:
    from_label: str
    to_label: str
    change_types: list[str]
    source_vocabularies: list[str]
    facets: dict[str, list[tuple[str, int]]]
    rows: Sequence[RowMapping]
    total: int
    page: int
    page_size: int = DIFF_PAGE_SIZE
    pages: int = 1
    errors: list[str] = field(default_factory=list)


def list_releases(session: Session) -> Sequence[Release]:
    with session.begin():
        return release_repo.list_releases(session)


def lineage(session: Session) -> Sequence[RowMapping]:
    with session.begin():
        return release_repo.lineage(session)


def resolve_release(session: Session, label: str | None) -> Release | None:
    """Release demandée, sinon la dernière publiée, sinon la plus récente."""
    with session.begin():
        if label:
            return release_repo.get_by_label(session, label)
        published = release_repo.latest_published(session)
        if published is not None:
            return published
        releases = release_repo.list_releases(session)
        return releases[0] if releases else None


def overview(session: Session) -> dict[str, object]:
    """Données de la page /releases : lignée, staging ouverte, libellés suggérés."""
    with session.begin():
        latest = release_repo.latest_published(session)
        staging = release_repo.open_staging(session)
        lineage = release_repo.lineage(session)
        open_major = [r for r in release_repo.open_releases(session) if r.kind == "major"]
    return {
        "lineage": lineage,
        "latest_published": latest,
        "staging": staging,
        "open_majors": open_major,
        "suggested_staging_label": _next_minor(latest.label) if latest else None,
        "suggested_major_label": _next_major(staging.label) if staging else None,
        "has_releases": bool(lineage),
    }


def _next_minor(label: str) -> str | None:
    match = LABEL_PATTERN.match(label)
    return f"v{match.group(1)}.{int(match.group(2)) + 1}" if match else None


def _next_major(label: str) -> str | None:
    match = LABEL_PATTERN.match(label)
    return f"v{int(match.group(1)) + 1}.0" if match else None


def _check_label(session: Session, label: str) -> str:
    label = label.strip()
    if not label:
        raise BusinessError("Le libellé de la release est obligatoire.")
    if release_repo.get_by_label(session, label) is not None:
        raise BusinessError(f"La release {label} existe déjà.")
    return label


def create_initial(session: Session, label: str, description: str | None, user: str) -> None:
    """Première release majeure (vide), lorsque le référentiel ne contient encore aucune release."""
    with session.begin():
        if release_repo.count_releases(session) > 0:
            raise BusinessError("Une release existe déjà : créer la staging depuis la dernière publiée.")
        label = _check_label(session, label)
        audit_repo.set_current_user(session, user)
        release_id = _call(lambda: release_repo.create_release(session, label, "major", None, description))
        release_repo.set_vocabulary_version(session, release_id, vocab_repo.vocabulary_version(session))
    logger.info("Release initiale %s créée par %s", label, user)


def create_staging(session: Session, label: str, description: str | None, user: str) -> None:
    with session.begin():
        parent = release_repo.latest_published(session)
        if parent is None:
            raise BusinessError("Aucune release publiée : impossible de créer une staging.")
        staging = release_repo.open_staging(session)
        if staging is not None:
            raise BusinessError(f"La staging {staging.label} est déjà ouverte (une seule staging à la fois).")
        label = _check_label(session, label)
        audit_repo.set_current_user(session, user)
        _call(lambda: release_repo.create_release(session, label, "staging", parent.label, description))
    logger.info("Staging %s créée depuis %s par %s", label, parent.label, user)


def promote(session: Session, staging_label: str, new_label: str, user: str) -> None:
    with session.begin():
        staging = release_repo.get_by_label(session, staging_label)
        if staging is None or staging.kind != "staging" or staging.status != "open":
            raise BusinessError(f"La staging ouverte {staging_label} est introuvable.")
        new_label = _check_label(session, new_label)
        audit_repo.set_current_user(session, user)
        _call(lambda: release_repo.promote_staging(session, staging_label, new_label))
    logger.info("Staging %s promue en %s par %s", staging_label, new_label, user)


def publish(session: Session, label: str, cdm_build_ref: str | None, user: str) -> None:
    with session.begin():
        release = release_repo.get_by_label(session, label)
        if release is None:
            raise BusinessError(f"Release {label} introuvable.")
        if release.kind != "major":
            raise BusinessError(
                "Seule une release majeure est publiée ; une staging doit d'abord être promue."
            )
        if release.status not in ("open", "frozen"):
            raise BusinessError(f"La release {label} est déjà {release.status}.")
        audit_repo.set_current_user(session, user)
        if release.vocabulary_version is None:
            release_repo.set_vocabulary_version(
                session, release.release_id, vocab_repo.vocabulary_version(session)
            )
        _call(lambda: release_repo.publish_release(session, label, (cdm_build_ref or "").strip() or None))
    logger.info("Release %s publiée par %s (build %s)", label, user, cdm_build_ref)


def mapping_count(session: Session, release: Release) -> int:
    with session.begin():
        return release_repo.count_mappings(session, release.release_id)


def diff(
    session: Session,
    from_label: str,
    to_label: str,
    change_types: list[str],
    source_vocabularies: list[str],
    page: int,
) -> DiffOut:
    change_types = [c for c in change_types if c in release_repo.DIFF_CHANGE_TYPES]
    with session.begin():
        for label in (from_label, to_label):
            if release_repo.get_by_label(session, label) is None:
                raise BusinessError(f"Release {label} introuvable.")
        facet_rows = release_repo.diff_facets(
            session, from_label, to_label, change_types, source_vocabularies
        )
        offset = (max(page, 1) - 1) * DIFF_PAGE_SIZE
        rows = release_repo.diff_rows(
            session, from_label, to_label, change_types, source_vocabularies, DIFF_PAGE_SIZE, offset
        )
    facets: dict[str, list[tuple[str, int]]] = {"change_type": [], "source_vocabulary": []}
    for row in facet_rows:
        facets[row["facet"]].append((row["value"], int(row["n"])))
    total = int(rows[0]["total"]) if rows else 0
    return DiffOut(
        from_label=from_label,
        to_label=to_label,
        change_types=change_types,
        source_vocabularies=source_vocabularies,
        facets=facets,
        rows=rows,
        total=total,
        page=max(page, 1),
        pages=max(1, -(-total // DIFF_PAGE_SIZE)),
    )


def _call(action: Callable[[], T]) -> T:
    """Exécute un appel de fonction PostgreSQL en traduisant l'erreur en message métier."""
    try:
        return action()
    except DBAPIError as exc:
        raise BusinessError(database_message(exc)) from exc
