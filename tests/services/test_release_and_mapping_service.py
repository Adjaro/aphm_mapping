"""Services de release (messages métier) et de correction manuelle."""

import pytest
from sqlalchemy.orm import Session

from app.schemas.custom_column import CustomColumnIn
from app.schemas.mapping import MappingEditIn
from app.services import custom_column_service, mapping_service, release_service, search_service
from app.services.errors import BusinessError
from tests.conftest import add_mapping, sql


def _cycle(session: Session) -> None:
    release_service.create_initial(session, "v1.0", None, "test")
    add_mapping(session, "v1.0", "GLU", 1001)
    release_service.publish(session, "v1.0", "build-1", "test")
    release_service.create_staging(session, "v1.1", None, "test")


def _stcm_id(session: Session, label: str, code: str) -> int:
    rows = sql(
        session,
        """
        SELECT s.stcm_id FROM mapping.source_to_concept_map s JOIN mapping.release r USING (release_id)
         WHERE r.label = :label AND s.source_code = :code
        """,
        label=label,
        code=code,
    )
    return int(rows[0][0])  # type: ignore[call-overload]


def test_release_rules_give_clear_messages(session: Session) -> None:
    with pytest.raises(BusinessError, match="Aucune release publiée"):
        release_service.create_staging(session, "v1.1", None, "x")
    _cycle(session)
    with pytest.raises(BusinessError, match="déjà ouverte"):
        release_service.create_staging(session, "v1.2", None, "x")
    with pytest.raises(BusinessError, match="Seule une release majeure"):
        release_service.publish(session, "v1.1", "b", "x")
    with pytest.raises(BusinessError, match="existe déjà"):
        release_service.promote(session, "v1.1", "v1.0", "x")
    release_service.promote(session, "v1.1", "v2.0", "x")
    overview = release_service.overview(session)
    assert overview["suggested_staging_label"] == "v1.1"
    assert [r.label for r in overview["open_majors"]] == ["v2.0"]  # type: ignore[attr-defined]


def test_update_mapping_changes_target_and_audits(session: Session) -> None:
    _cycle(session)
    stcm_id = _stcm_id(session, "v1.1", "GLU")
    data = MappingEditIn(
        target_concept_id=2001, mapping_status="APPROVED", equivalence="WIDER", mapping_comment=" ok "
    )
    mapping, label = mapping_service.update_mapping(session, stcm_id, data, "bob")
    assert label == "v1.1"
    assert (mapping.target_vocabulary_id, mapping.reviewed_by, mapping.mapping_comment) == (
        "SNOMED",
        "bob",
        "ok",
    )
    audit = sql(session, "SELECT changed_by, new_row ->> 'target_concept_id' FROM mapping.audit_log")
    assert audit == [("bob", "2001")]


def test_update_mapping_refused_on_published_release(session: Session) -> None:
    _cycle(session)
    with pytest.raises(BusinessError, match="n'est pas ouverte"):
        mapping_service.update_mapping(
            session, _stcm_id(session, "v1.0", "GLU"), MappingEditIn(target_concept_id=1002), "x"
        )


def test_update_mapping_validates_target_and_custom_columns(session: Session) -> None:
    _cycle(session)
    stcm_id = _stcm_id(session, "v1.1", "GLU")
    with pytest.raises(BusinessError, match="absent du vocabulaire"):
        mapping_service.update_mapping(session, stcm_id, MappingEditIn(target_concept_id=424242), "x")
    custom_column_service.create_column(
        session, CustomColumnIn(column_name="lot", label="Lot", data_type="integer")
    )
    with pytest.raises(BusinessError, match="entier attendu"):
        mapping_service.update_mapping(
            session, stcm_id, MappingEditIn(target_concept_id=1001, custom_values={"lot": "abc"}), "x"
        )
    mapping, _ = mapping_service.update_mapping(
        session, stcm_id, MappingEditIn(target_concept_id=1001, custom_values={"lot": "7"}), "x"
    )
    assert mapping.extra == {"lot": 7}


def test_custom_column_name_cannot_shadow_cdm_column(session: Session) -> None:
    with pytest.raises(BusinessError, match="déjà une colonne"):
        custom_column_service.create_column(session, CustomColumnIn(column_name="domain_id", label="x"))


def test_detail_history_marks_changes(session: Session) -> None:
    _cycle(session)
    stcm_id = _stcm_id(session, "v1.1", "GLU")
    mapping_service.update_mapping(
        session, stcm_id, MappingEditIn(target_concept_id=1001, mapping_status="FLAGGED"), "x"
    )
    release = release_service.resolve_release(session, "v1.1")
    assert release is not None
    detail = search_service.mapping_detail(session, release, "TEST", "GLU")
    assert detail is not None
    assert [c.label for c in detail.history] == ["v1.0", "v1.1"]
    assert detail.history[1].changed == {"mapping_status"}
    assert [field for field, _, _ in detail.audit[0].changes] == [
        "mapping_status",
        "reviewed_at",
        "reviewed_by",
    ]
    assert detail.audit[0].changes[0] == ("mapping_status", "UNCHECKED", "FLAGGED")
