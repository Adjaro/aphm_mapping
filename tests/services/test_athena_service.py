"""Base Athena : connexion enregistrée, test, synchronisation depuis une base distante, comparaison.

La « base Athena distante » est simulée par le schéma athena_src de la base de test.
"""

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.schemas.athena import AthenaConnectionIn, AthenaFilter, AthenaVocabularyMapIn
from app.services import athena_service, release_service
from app.services.errors import BusinessError
from tests.conftest import _TEST_URL, add_mapping, sql

REMOTE_SCHEMA = """
CREATE SCHEMA athena_src;
CREATE TABLE athena_src.concept (LIKE vocab.concept);
CREATE TABLE athena_src.vocabulary (LIKE vocab.vocabulary);
CREATE TABLE athena_src.concept_relationship (
    concept_id_1 int, concept_id_2 int, relationship_id varchar(20),
    valid_start_date date, valid_end_date date, invalid_reason varchar(1)
);
INSERT INTO athena_src.vocabulary VALUES ('None', 'OMOP', NULL, 'v5.0 31-AUG-26', 0);
INSERT INTO athena_src.concept VALUES
    (501, 'Diabète type 2', 'Condition', 'CIM10', 'ICD10 code', NULL, 'E11.9',
     '1970-01-01', '2099-12-31', NULL),
    (502, 'Glycémie anormale', 'Condition', 'CIM10', 'ICD10 code', NULL, 'R73.0',
     '1970-01-01', '2099-12-31', NULL),
    (2001, 'Type 2 diabetes mellitus', 'Condition', 'SNOMED', 'Disorder', 'S', '44054006',
     '1970-01-01', '2099-12-31', NULL),
    (1001, 'Glucose', 'Measurement', 'LOINC', 'Lab Test', 'S', '2345-7', '1970-01-01', '2099-12-31', NULL),
    (900, 'Autre vocabulaire', 'Drug', 'ATC', 'ATC 5th', 'C', 'A01', '1970-01-01', '2099-12-31', NULL);
INSERT INTO athena_src.concept_relationship VALUES
    (501, 2001, 'Maps to', '1970-01-01', '2099-12-31', NULL),
    (502, 1001, 'Maps to', '1970-01-01', '2099-12-31', NULL),
    (502, 2001, 'Maps to', '1970-01-01', '2020-01-01', 'D'),
    (501, 2001, 'Is a', '1970-01-01', '2099-12-31', NULL);
"""


def _connection_in(**overrides: object) -> AthenaConnectionIn:
    url = make_url(_TEST_URL)
    data = {
        "label": "Athena test",
        "host": url.host or "localhost",
        "port": url.port or 5432,
        "database_name": url.database or "",
        "username": url.username or "",
        "password": url.password,
        "schema_name": "athena_src",
    }
    data.update(overrides)
    return AthenaConnectionIn.model_validate(data)


@pytest.fixture
def remote(session: Session) -> None:
    sql(session, "DROP SCHEMA IF EXISTS athena_src CASCADE")
    for statement in REMOTE_SCHEMA.split(";"):
        if statement.strip():
            sql(session, statement)


def _connection_id(session: Session) -> int:
    return int(sql(session, "SELECT athena_connection_id FROM mapping.athena_connection")[0][0])  # type: ignore[call-overload]


def test_connection_test_and_sync(session: Session, remote: None) -> None:
    athena_service.create_connection(session, _connection_in())
    connection_id = _connection_id(session)
    message = athena_service.test_connection(session, connection_id)
    assert "réussie" in message and "v5.0 31-AUG-26" in message

    with pytest.raises(BusinessError, match="Aucune correspondance"):
        athena_service.synchronize(session)
    athena_service.save_vocabulary_map(
        session, AthenaVocabularyMapIn(source_vocabulary_id="CIM", athena_vocabulary_id="CIM10")
    )
    result = athena_service.synchronize(session)
    assert "2 lignes" in result  # 2 concepts CIM10 ; relations « Is a » et invalides exclues
    rows = sql(
        session,
        "SELECT concept_code, relationship_id, target_concept_id FROM mapping.athena_maps_to ORDER BY 1",
    )
    assert rows == [("E11.9", "Maps to", 2001), ("R73.0", "Maps to", 1001)]
    status = sql(session, "SELECT last_sync_status, athena_vocabulary_version FROM mapping.athena_connection")
    assert status == [("ok", "v5.0 31-AUG-26")]

    release_service.create_initial(session, "v1.0", None, "t")
    add_mapping(session, "v1.0", "E119", 2001, source_vocabulary_id="CIM", target_vocabulary_id="SNOMED")
    add_mapping(session, "v1.0", "R730", 2001, source_vocabulary_id="CIM", target_vocabulary_id="SNOMED")
    add_mapping(session, "v1.0", "X", 1001, source_vocabulary_id="LABO")
    release = release_service.resolve_release(session, "v1.0")
    assert release is not None
    comparison = athena_service.compare(session, release, AthenaFilter())
    assert comparison.status_totals["SAME"] == 1
    assert comparison.status_totals["DIFFERENT"] == 1
    assert [row["source_vocabulary_id"] for row in comparison.unconfigured] == ["LABO"]
    filtered = athena_service.compare(session, release, AthenaFilter(status=["DIFFERENT"]))
    assert [row["source_code"] for row in filtered.rows] == ["R730"]
    targets = athena_service.code_targets(session, "CIM", "E119")
    assert [t["target_concept_id"] for t in targets] == [2001]


def test_sync_failure_is_recorded(session: Session, remote: None) -> None:
    athena_service.create_connection(session, _connection_in(schema_name="schema_absent"))
    athena_service.save_vocabulary_map(
        session, AthenaVocabularyMapIn(source_vocabulary_id="CIM", athena_vocabulary_id="CIM10")
    )
    with pytest.raises(BusinessError, match="Synchronisation impossible"):
        athena_service.synchronize(session)
    assert sql(session, "SELECT last_sync_status FROM mapping.athena_connection") == [("failed",)]
    with pytest.raises(BusinessError, match="impossible"):
        athena_service.test_connection(session, _connection_id(session))


def test_bad_credentials_give_clear_message(session: Session) -> None:
    athena_service.create_connection(
        session, _connection_in(database_name="base_absente", label="Mauvaise base")
    )
    with pytest.raises(BusinessError, match="Connexion Athena « Mauvaise base » impossible"):
        athena_service.test_connection(session, _connection_id(session))


def test_only_one_active_connection(session: Session) -> None:
    athena_service.create_connection(session, _connection_in(label="A"))
    athena_service.create_connection(session, _connection_in(label="B"))
    active = sql(session, "SELECT label FROM mapping.athena_connection WHERE is_active")
    assert active == [("A",)]
    b_id = sql(session, "SELECT athena_connection_id FROM mapping.athena_connection WHERE label = 'B'")[0][0]
    athena_service.activate_connection(session, int(b_id))  # type: ignore[call-overload]
    assert sql(session, "SELECT label FROM mapping.athena_connection WHERE is_active") == [("B",)]
    with pytest.raises(BusinessError, match="existe déjà"):
        athena_service.create_connection(session, _connection_in(label="A"))
