"""Fonctions de comparaison : diff_codes (deux releases) et compare_athena (« Maps to » natifs)."""

from sqlalchemy.orm import Session

from tests.conftest import add_mapping, sql


def _two_releases(session: Session) -> None:
    sql(session, "SELECT mapping.create_release('v1.0', 'major')")
    add_mapping(session, "v1.0", "GLU", 1001)
    add_mapping(session, "v1.0", "HB", 1002)
    add_mapping(session, "v1.0", "OLD", 1001)
    add_mapping(session, "v1.0", "DT2", 2002, target_vocabulary_id="SNOMED", domain_id="Condition")
    sql(session, "SELECT mapping.publish_release('v1.0', 'b1')")
    sql(session, "SELECT mapping.create_release('v1.1', 'staging', 'v1.0')")


def test_diff_codes_classifies_changes_per_code(session: Session) -> None:
    _two_releases(session)
    sql(
        session,
        """
        UPDATE mapping.source_to_concept_map s SET mapping_status = 'FLAGGED'
          FROM mapping.release r
         WHERE r.release_id = s.release_id AND r.label = 'v1.1' AND s.source_code = 'GLU'
        """,
    )
    sql(
        session,
        """
        UPDATE mapping.source_to_concept_map s SET target_concept_id = 2001
          FROM mapping.release r
         WHERE r.release_id = s.release_id AND r.label = 'v1.1' AND s.source_code = 'DT2'
        """,
    )
    sql(
        session,
        """
        DELETE FROM mapping.source_to_concept_map s USING mapping.release r
         WHERE r.release_id = s.release_id AND r.label = 'v1.1' AND s.source_code = 'OLD'
        """,
    )
    add_mapping(session, "v1.1", "NEW", 3001, target_vocabulary_id="UCUM", domain_id="Unit")
    rows = sql(
        session,
        """
        SELECT source_code, change_kind, n_added, n_removed, n_modified, changed_fields,
               old_targets -> 0 ->> 'target_concept_id', new_targets -> 0 ->> 'target_concept_id'
          FROM mapping.diff_codes('v1.0', 'v1.1')
         ORDER BY source_code
        """,
    )
    assert rows == [
        ("DT2", "TARGET_CHANGED", 1, 1, 0, [], "2002", "2001"),
        ("GLU", "MODIFIED", 0, 0, 1, ["mapping_status"], "1001", "1001"),
        ("NEW", "NEW_CODE", 1, 0, 0, [], None, "3001"),
        ("OLD", "REMOVED_CODE", 0, 1, 0, [], "1001", None),
    ]


def _athena_snapshot(session: Session) -> None:
    sql(
        session,
        """
        INSERT INTO mapping.athena_vocabulary_map (
            source_vocabulary_id, athena_vocabulary_id, ignore_dots, ignore_case)
        VALUES ('TEST', 'CIM10', true, true)
        """,
    )
    sql(
        session,
        """
        INSERT INTO mapping.athena_maps_to (vocabulary_id, concept_code, concept_id, concept_name,
                                            relationship_id,
                                            target_concept_id, target_concept_name, target_vocabulary_id)
        VALUES ('CIM10', 'E11.9', 501, 'Diabète type 2', 'Maps to', 2001, 'Type 2 diabetes', 'SNOMED'),
               ('CIM10', 'R73.0', 502, 'Glycémie anormale', 'Maps to', 1001, 'Glucose', 'LOINC'),
               ('CIM10', 'Z99.9', 503, 'Sans mapping', NULL, NULL, NULL, NULL),
               ('CIM10', 'D64.9', 504, 'Anémie', 'Maps to', 1002, 'Hemoglobin', 'LOINC')
        """,
    )


def test_compare_athena_statuses(session: Session) -> None:
    sql(session, "SELECT mapping.create_release('v1.0', 'major')")
    add_mapping(session, "v1.0", "E119", 2001, target_vocabulary_id="SNOMED", domain_id="Condition")
    add_mapping(session, "v1.0", "r730", 1002)
    add_mapping(session, "v1.0", "Z999", 1001)
    add_mapping(session, "v1.0", "D649", 0, target_vocabulary_id="None")
    add_mapping(session, "v1.0", "XXX", 1001)
    add_mapping(session, "v1.0", "AUTRE", 1001, source_vocabulary_id="NON_PARAMETRE")
    _athena_snapshot(session)
    rows = sql(
        session,
        """
        SELECT source_code, comparison_status, athena_concept_code, athena_target_ids
          FROM mapping.compare_athena('v1.0')
         ORDER BY source_code
        """,
    )
    assert rows == [
        ("D649", "MISSING_LOCAL", "D64.9", [1002]),
        ("E119", "SAME", "E11.9", [2001]),
        ("XXX", "CODE_NOT_IN_ATHENA", None, None),
        ("Z999", "ATHENA_NO_MAPPING", "Z99.9", None),
        ("r730", "DIFFERENT", "R73.0", [1001]),
    ]


def test_strict_matching_without_normalization(session: Session) -> None:
    sql(session, "SELECT mapping.create_release('v1.0', 'major')")
    add_mapping(session, "v1.0", "E119", 2001, target_vocabulary_id="SNOMED", domain_id="Condition")
    _athena_snapshot(session)
    sql(session, "UPDATE mapping.athena_vocabulary_map SET ignore_dots = false")
    rows = sql(session, "SELECT comparison_status FROM mapping.compare_athena('v1.0')")
    assert rows == [("CODE_NOT_IN_ATHENA",)]
