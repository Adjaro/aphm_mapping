"""Fixtures pytest : base de test recréée depuis db/ddl/ et vocabulaire minimal.

La base TEST_DATABASE_URL est entièrement réinitialisée (schémas vocab et mapping).
"""

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from app.config import Settings, get_settings

_TEST_URL: str = os.environ.get("TEST_DATABASE_URL") or Settings().test_database_url or ""
if not _TEST_URL:
    raise RuntimeError("TEST_DATABASE_URL doit être défini (fichier .env ou variable d'environnement).")
# L'application doit utiliser la base de test : à faire AVANT l'import de app.db
os.environ["DATABASE_URL"] = _TEST_URL
os.environ["WARM_UP_SEARCH"] = "false"  # pas de préchauffage en arrière-plan pendant les tests
get_settings.cache_clear()

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import CursorResult, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import SessionLocal, libpq_dsn  # noqa: E402
from app.main import app  # noqa: E402
from app.services import compare_service, search_service  # noqa: E402
from scripts.migrate import migrate  # noqa: E402

RESET_SQL = """
DROP SCHEMA IF EXISTS mapping CASCADE;
DROP SCHEMA IF EXISTS vocab CASCADE;
DROP SCHEMA IF EXISTS athena_src CASCADE;
DROP TABLE IF EXISTS public.schema_migration;
"""

VOCAB_ROWS = [
    (0, "No matching concept", "Metadata", "None", "Undefined", None, "No matching concept", None),
    (1001, "Glucose [Mass/volume] in Serum", "Measurement", "LOINC", "Lab Test", "S", "2345-7", None),
    (1002, "Hemoglobin [Mass/volume] in Blood", "Measurement", "LOINC", "Lab Test", "S", "718-7", None),
    (2001, "Type 2 diabetes mellitus", "Condition", "SNOMED", "Disorder", "S", "44054006", None),
    (2002, "Obsolete disorder", "Condition", "SNOMED", "Disorder", None, "999999", "D"),
    (3001, "milligram per deciliter", "Unit", "UCUM", "Unit", "S", "mg/dL", None),
]

TRUNCATE_SQL = """
TRUNCATE mapping.audit_log, mapping.import_error, mapping.source_to_concept_map, mapping.import_batch,
         mapping.release, mapping.custom_column, mapping.athena_connection, mapping.athena_vocabulary_map,
         mapping.athena_maps_to RESTART IDENTITY CASCADE
"""


@pytest.fixture(scope="session", autouse=True)
def test_database() -> Iterator[str]:
    with psycopg.connect(libpq_dsn(_TEST_URL), autocommit=True) as conn:
        conn.execute(RESET_SQL)
    migrate(_TEST_URL)
    with psycopg.connect(libpq_dsn(_TEST_URL)) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO vocab.concept (concept_id, concept_name, domain_id, vocabulary_id,
                                           concept_class_id,
                                           standard_concept, concept_code, valid_start_date, valid_end_date,
                                           invalid_reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s, DATE '1970-01-01', DATE '2099-12-31', %s)
                """,
                VOCAB_ROWS,
            )
            cur.execute(
                """
                INSERT INTO vocab.vocabulary (vocabulary_id, vocabulary_name, vocabulary_version,
                                              vocabulary_concept_id)
                VALUES ('None', 'OMOP Standardized Vocabularies', 'v5.0 TEST', 0)
                """
            )
        conn.commit()
    yield _TEST_URL


@pytest.fixture(autouse=True)
def clean_data(test_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    with psycopg.connect(libpq_dsn(test_database), autocommit=True) as conn:
        conn.execute(TRUNCATE_SQL)
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path / "uploads")
    search_service.clear_search_cache()  # les identifiants de release sont réutilisés d'un test à l'autre
    compare_service.clear_compare_cache()
    yield


@pytest.fixture
def session() -> Iterator[Session]:
    db_session = SessionLocal()
    try:
        yield db_session
    finally:
        db_session.close()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def sql(session: Session, statement: str, **params: object) -> list[tuple[object, ...]]:
    """Exécute une requête de test dans sa propre transaction et renvoie les lignes éventuelles."""
    with session.begin():
        result = session.execute(text(statement), params)
        if isinstance(result, CursorResult) and result.returns_rows:
            return [tuple(row) for row in result]
        return []


def add_mapping(
    session: Session, release_label: str, source_code: str, target_concept_id: int, **values: object
) -> None:
    """Insère directement un mapping de test (vocabulaire source TEST par défaut)."""
    params: dict[str, object] = {
        "label": release_label,
        "source_code": source_code,
        "target_concept_id": target_concept_id,
        "source_vocabulary_id": values.get("source_vocabulary_id", "TEST"),
        "target_vocabulary_id": values.get("target_vocabulary_id", "LOINC"),
        "description": values.get("description", f"Libellé {source_code}"),
        "domain_id": values.get("domain_id", "Measurement"),
        "mapping_status": values.get("mapping_status", "UNCHECKED"),
        "relationship_id": values.get("relationship_id", "Maps to"),
    }
    sql(
        session,
        """
        INSERT INTO mapping.source_to_concept_map (
            release_id, source_code, source_vocabulary_id, source_code_description, target_concept_id,
            target_vocabulary_id, domain_id, mapping_status, relationship_id)
        SELECT r.release_id, :source_code, :source_vocabulary_id, :description, :target_concept_id,
               :target_vocabulary_id, :domain_id, :mapping_status, :relationship_id
          FROM mapping.release r
         WHERE r.label = :label
        """,
        **params,
    )
