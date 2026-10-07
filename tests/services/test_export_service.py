"""Exports d'une release : properties (contenu, tri, filtres, écriture dossier), CSV CDM et complet."""

import io
import zipfile
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Release
from app.schemas.custom_column import CustomColumnIn
from app.schemas.export import ExportFilter
from app.services import custom_column_service, export_service, release_service
from tests.conftest import add_mapping, sql


def _release(session: Session) -> Release:
    release_service.create_initial(session, "v1.0", None, "t")
    add_mapping(session, "v1.0", "b", 1001)
    add_mapping(session, "v1.0", "a", 1002)
    add_mapping(session, "v1.0", "a", 1001)  # deux cibles : triées numériquement
    add_mapping(session, "v1.0", "a", 1001, source_vocabulary_id="AUTRE")  # même code, autre vocabulaire
    add_mapping(session, "v1.0", "B", 1002, source_vocabulary_id="AUTRE")
    add_mapping(session, "v1.0", "Z", 0, target_vocabulary_id="None")
    add_mapping(session, "v1.0", "IGN", 1001, mapping_status="IGNORED")
    add_mapping(session, "v1.0", "E11", 2001, target_vocabulary_id="SNOMED", domain_id="Meas Value")
    add_mapping(session, "v1.0", "NODOM", 3001, target_vocabulary_id="UCUM", domain_id=None)
    release = release_service.resolve_release(session, "v1.0")
    assert release is not None
    return release


@pytest.fixture
def export_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(get_settings(), "export_dir", tmp_path)
    return tmp_path / get_settings().properties_subdir


def test_properties_content_and_order(session: Session) -> None:
    release = _release(session)
    files = export_service.properties_files(release.release_id, ExportFilter())
    # domaine du mapping, sinon celui du concept cible ; espaces -> _
    assert sorted(files) == ["Meas_Value.properties", "Measurement.properties", "Unit.properties"]
    # tri binaire (majuscules avant minuscules), cibles fusionnées entre vocabulaires, IGNORED et 0 exclus
    assert files["Measurement.properties"] == "B=1002\na=1001,1002\nb=1001\n"
    assert files["Meas_Value.properties"] == "E11=2001\n"
    assert files["Unit.properties"] == "NODOM=3001\n"


def test_properties_filters(session: Session) -> None:
    release = _release(session)
    flt = ExportFilter(
        source_vocabulary=["AUTRE"], mapping_status=["UNCHECKED", "IGNORED"], include_unmapped=True
    )
    assert export_service.properties_files(release.release_id, flt) == {
        "Measurement.properties": "B=1002\na=1001\n"
    }
    flt = ExportFilter(source_vocabulary=["TEST"], include_unmapped=True)
    content = export_service.properties_files(release.release_id, flt)["Measurement.properties"]
    assert "Z=0" in content and "IGN" not in content


def test_properties_zip(session: Session) -> None:
    release = _release(session)
    archive = zipfile.ZipFile(io.BytesIO(export_service.properties_zip(release.release_id, ExportFilter())))
    assert sorted(archive.namelist()) == [
        "Meas_Value.properties",
        "Measurement.properties",
        "Unit.properties",
    ]


def test_write_properties_replaces_folder_content(session: Session, export_dir: Path) -> None:
    release = _release(session)
    export_dir.mkdir(parents=True)
    (export_dir / "Ancien.properties").write_text("x=1\n", encoding="utf-8")
    (export_dir / "notes.txt").write_text("conservé", encoding="utf-8")
    result = export_service.write_properties(release.release_id, ExportFilter())
    assert result.removed == ["Ancien.properties"]
    assert result.files == {"Meas_Value.properties": 1, "Measurement.properties": 3, "Unit.properties": 1}
    assert sorted(p.name for p in export_dir.iterdir()) == [
        "Meas_Value.properties",
        "Measurement.properties",
        "Unit.properties",
        "notes.txt",
    ]
    assert (export_dir / "Measurement.properties").read_bytes() == b"B=1002\na=1001,1002\nb=1001\n"


def test_csv_exports(session: Session) -> None:
    release = _release(session)
    custom_column_service.create_column(session, CustomColumnIn(column_name="lot", label="Lot"))
    sql(
        session,
        "UPDATE mapping.source_to_concept_map SET extra = '{\"lot\": \"L1\"}' WHERE source_code = 'b'",
    )
    cdm = "".join(
        export_service.release_cdm_csv(release.release_id, ExportFilter(source_vocabulary=["AUTRE"]))
    )
    lines = cdm.strip().splitlines()
    assert lines[0].startswith("source_code,source_concept_id") and len(lines) == 3
    full = "".join(export_service.release_full_csv(release.release_id, ExportFilter()))
    header, *rows = full.lstrip("﻿").strip().splitlines()
    assert header.endswith("quality_flag;import_batch_id;lot")
    assert any(row.startswith("b;") and row.endswith(";L1") for row in rows)


def test_relative_export_dir_is_resolved_from_project_root() -> None:
    from app.config import BASE_DIR, Settings

    settings = Settings(export_dir=Path("data"), upload_dir=Path("var/uploads"))
    assert settings.export_dir == BASE_DIR / "data"
    assert settings.upload_dir == BASE_DIR / "var" / "uploads"
