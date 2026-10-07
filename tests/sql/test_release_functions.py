"""Fonctions et triggers PostgreSQL : cycle de release, verrouillage, audit, diff."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from tests.conftest import add_mapping, sql


def _published_v10(session: Session) -> None:
    sql(session, "SELECT mapping.create_release('v1.0', 'major')")
    add_mapping(session, "v1.0", "GLU", 1001)
    add_mapping(session, "v1.0", "HB", 1002)
    sql(session, "SELECT mapping.publish_release('v1.0', 'build-1')")


def _count(session: Session, label: str) -> int:
    rows = sql(
        session,
        """
        SELECT count(*) FROM mapping.source_to_concept_map s
          JOIN mapping.release r USING (release_id) WHERE r.label = :label
        """,
        label=label,
    )
    return int(rows[0][0])  # type: ignore[call-overload]


def test_create_release_copies_parent_rows(session: Session) -> None:
    _published_v10(session)
    sql(session, "SELECT mapping.create_release('v1.1', 'staging', 'v1.0')")
    assert _count(session, "v1.1") == 2
    status = sql(session, "SELECT status, kind FROM mapping.release WHERE label = 'v1.1'")
    assert status == [("open", "staging")]


def test_only_one_open_staging(session: Session) -> None:
    _published_v10(session)
    sql(session, "SELECT mapping.create_release('v1.1', 'staging', 'v1.0')")
    with pytest.raises(IntegrityError):
        sql(session, "SELECT mapping.create_release('v1.2', 'staging', 'v1.0')")


def test_published_release_is_locked(session: Session) -> None:
    _published_v10(session)
    with pytest.raises(DBAPIError, match="non modifiable"):
        sql(session, "UPDATE mapping.source_to_concept_map SET mapping_status = 'APPROVED'")
    with pytest.raises(DBAPIError, match="non modifiable"):
        add_mapping(session, "v1.0", "NEW", 1001)


def test_audit_records_update_with_app_user(session: Session) -> None:
    _published_v10(session)
    sql(session, "SELECT mapping.create_release('v1.1', 'staging', 'v1.0')")
    with session.begin():
        session.execute(text("SELECT set_config('app.user', 'alice', true)"))
        session.execute(
            text(
                """
                UPDATE mapping.source_to_concept_map s SET mapping_status = 'APPROVED'
                  FROM mapping.release r
                 WHERE r.release_id = s.release_id AND r.label = 'v1.1' AND s.source_code = 'GLU'
                """
            )
        )
    rows = sql(
        session,
        "SELECT operation, changed_by, old_row ->> 'mapping_status', new_row ->> 'mapping_status' "
        "FROM mapping.audit_log",
    )
    assert rows == [("UPDATE", "alice", "UNCHECKED", "APPROVED")]


def test_promote_staging_archives_and_creates_major(session: Session) -> None:
    _published_v10(session)
    sql(session, "SELECT mapping.create_release('v1.1', 'staging', 'v1.0')")
    add_mapping(session, "v1.1", "DT2", 2001, target_vocabulary_id="SNOMED", domain_id="Condition")
    sql(session, "SELECT mapping.promote_staging('v1.1', 'v2.0')")
    releases = {str(k): str(v) for k, v in sql(session, "SELECT label, status FROM mapping.release")}
    assert releases == {"v1.0": "published", "v1.1": "archived", "v2.0": "open"}
    assert _count(session, "v2.0") == 3
    sql(session, "SELECT mapping.publish_release('v2.0', 'build-2')")
    published = sql(session, "SELECT DISTINCT source_code FROM mapping.v_stcm_published ORDER BY 1")
    assert [r[0] for r in published] == ["DT2", "GLU", "HB"]


def test_diff_releases_reports_changes(session: Session) -> None:
    _published_v10(session)
    sql(session, "SELECT mapping.create_release('v1.1', 'staging', 'v1.0')")
    sql(
        session,
        """
        UPDATE mapping.source_to_concept_map s SET mapping_status = 'FLAGGED', mapping_comment = 'à revoir'
          FROM mapping.release r
         WHERE r.release_id = s.release_id AND r.label = 'v1.1' AND s.source_code = 'GLU'
        """,
    )
    sql(
        session,
        """
        DELETE FROM mapping.source_to_concept_map s USING mapping.release r
         WHERE r.release_id = s.release_id AND r.label = 'v1.1' AND s.source_code = 'HB'
        """,
    )
    add_mapping(session, "v1.1", "UNIT1", 3001, target_vocabulary_id="UCUM", domain_id="Unit")
    rows = sql(
        session,
        "SELECT change_type, source_code, changed_fields"
        " FROM mapping.diff_releases('v1.0', 'v1.1') ORDER BY 2",
    )
    assert rows == [
        ("MODIFIED", "GLU", ["mapping_comment", "mapping_status"]),
        ("REMOVED", "HB", None),
        ("ADDED", "UNIT1", None),
    ]


def test_quality_view_flags(session: Session) -> None:
    sql(session, "SELECT mapping.create_release('v1.0', 'major')")
    add_mapping(session, "v1.0", "OK", 1001)
    add_mapping(session, "v1.0", "ZERO", 0, target_vocabulary_id="None")
    add_mapping(session, "v1.0", "MISSING", 9999)
    add_mapping(session, "v1.0", "INVALID", 2002, target_vocabulary_id="SNOMED", domain_id="Condition")
    add_mapping(session, "v1.0", "VOCAB", 1001, target_vocabulary_id="SNOMED")
    add_mapping(session, "v1.0", "DOMAIN", 2001, target_vocabulary_id="SNOMED", domain_id="Measurement")
    rows = {
        str(k): str(v)
        for k, v in sql(session, "SELECT source_code, quality_flag FROM mapping.v_mapping_quality")
    }
    assert rows == {
        "OK": "OK",
        "ZERO": "UNMAPPED",
        "MISSING": "TARGET_NOT_FOUND",
        "INVALID": "TARGET_INVALID",
        "VOCAB": "VOCABULARY_MISMATCH",
        "DOMAIN": "DOMAIN_MISMATCH",
    }


CAST_SQL = {
    "date": "SELECT mapping.try_cast_date(:value)::text",
    "int": "SELECT mapping.try_cast_int(:value)::text",
    "numeric": "SELECT mapping.try_cast_numeric(:value)::text",
    "boolean": "SELECT mapping.try_cast_boolean(:value)::text",
}


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        ("date", "2024-02-29", "2024-02-29"),
        ("date", "20240131", "2024-01-31"),
        ("date", "31/12/2023", "2023-12-31"),
        ("date", "2023-02-30", None),
        ("date", "n/a", None),
        ("int", " 42 ", "42"),
        ("int", "4.2", None),
        ("int", "99999999999", None),
        ("numeric", "3,14", "3.14"),
        ("boolean", "Oui", "true"),
        ("boolean", "peut-être", None),
    ],
)
def test_try_cast_functions(session: Session, kind: str, value: str, expected: str | None) -> None:
    assert sql(session, CAST_SQL[kind], value=value) == [(expected,)]
