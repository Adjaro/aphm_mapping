"""Correction manuelle d'un mapping dans la release ouverte."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models import CustomColumn, Release, SourceToConceptMap
from app.repositories import audit_repo, custom_column_repo, release_repo, stcm_repo, vocab_repo
from app.schemas.mapping import MappingEditIn
from app.services.custom_column_service import convert_value
from app.services.errors import BusinessError, database_message

logger = logging.getLogger(__name__)


@dataclass
class EditContextOut:
    mapping: SourceToConceptMap
    release: Release
    concept: RowMapping | None
    custom_columns: Sequence[CustomColumn]
    domains: list[str]
    errors: dict[str, str] = field(default_factory=dict)


def edit_context(session: Session, stcm_id: int) -> EditContextOut:
    with session.begin():
        mapping = stcm_repo.get_mapping(session, stcm_id)
        if mapping is None:
            raise BusinessError(f"Mapping {stcm_id} introuvable.")
        release = release_repo.get_by_id(session, mapping.release_id)
        assert release is not None
        concept = vocab_repo.get_concept(session, mapping.target_concept_id)
        custom_columns = custom_column_repo.list_columns(session)
        domains = vocab_repo.standard_domains(session)
    return EditContextOut(mapping, release, concept, custom_columns, domains)


def lookup_concepts(
    session: Session, query: str, domain: str | None, standard_only: bool
) -> Sequence[RowMapping]:
    query = query.strip()
    if len(query) < 2 and not query.isdigit():
        return []
    with session.begin():
        return vocab_repo.lookup(session, query, domain, standard_only)


def update_mapping(
    session: Session, stcm_id: int, data: MappingEditIn, user: str
) -> tuple[SourceToConceptMap, str]:
    """Applique la correction ; renvoie le mapping et le libellé de sa release."""
    errors = data.field_errors()
    if errors:
        raise BusinessError(" ".join(errors.values()))
    with session.begin():
        mapping = stcm_repo.get_mapping(session, stcm_id)
        if mapping is None:
            raise BusinessError(f"Mapping {stcm_id} introuvable.")
        release = release_repo.get_by_id(session, mapping.release_id)
        if release is None or release.status != "open":
            label = release.label if release else "?"
            raise BusinessError(f"La release {label} n'est pas ouverte : modification impossible.")
        target_vocabulary_id = _target_vocabulary(session, data.target_concept_id)
        if stcm_repo.key_exists(
            session,
            mapping.release_id,
            mapping.source_vocabulary_id,
            mapping.source_code,
            data.target_concept_id,
            data.relationship_id,
            mapping.stcm_id,
        ):
            raise BusinessError("Ce code source a déjà un mapping vers ce concept avec cette relation.")
        extra = _merge_extra(mapping.extra, custom_column_repo.list_columns(session), data.custom_values)
        audit_repo.set_current_user(session, user)
        _apply(mapping, data, target_vocabulary_id, extra, user)
        try:
            session.flush()
        except DBAPIError as exc:
            raise BusinessError(database_message(exc)) from exc
    logger.info("Mapping %s modifié par %s", stcm_id, user)
    return mapping, release.label


def _target_vocabulary(session: Session, target_concept_id: int) -> str:
    if target_concept_id == 0:
        return "None"
    concept = vocab_repo.get_concept(session, target_concept_id)
    if concept is None:
        raise BusinessError(f"Le concept {target_concept_id} est absent du vocabulaire chargé.")
    return str(concept["vocabulary_id"])


def _merge_extra(
    current: dict[str, Any], columns: Sequence[CustomColumn], values: dict[str, str]
) -> dict[str, Any]:
    extra = dict(current or {})
    for column in columns:
        converted = convert_value(column, values.get(column.column_name))
        if converted is None:
            extra.pop(column.column_name, None)
        else:
            extra[column.column_name] = converted
    return extra


def _apply(
    mapping: SourceToConceptMap,
    data: MappingEditIn,
    target_vocabulary_id: str,
    extra: dict[str, Any],
    user: str,
) -> None:
    status_changed = mapping.mapping_status != data.mapping_status
    mapping.target_concept_id = data.target_concept_id
    mapping.target_vocabulary_id = target_vocabulary_id
    mapping.relationship_id = data.relationship_id
    mapping.mapping_status = data.mapping_status
    mapping.equivalence = data.equivalence or None
    mapping.mapping_comment = (data.mapping_comment or "").strip() or None
    mapping.extra = extra
    if status_changed:
        mapping.reviewed_by = user
        mapping.reviewed_at = datetime.now(UTC)
