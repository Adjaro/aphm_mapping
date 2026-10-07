"""Import CSV / Excel : trois modes, valeurs par défaut, rejets, colonnes personnalisées, formats."""

import io
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy.orm import Session

from app.schemas.custom_column import CustomColumnIn
from app.schemas.imports import ColumnMappingIn
from app.services import custom_column_service, file_reader, import_service, release_service
from app.services.errors import BusinessError
from tests.conftest import add_mapping, sql

HEADER = "code;libelle;concept;statut\n"
MAPPING = {"source_code": "code", "source_code_description": "libelle", "target_concept_id": "concept"}


def _staging(session: Session) -> None:
    """v1.0 publiée avec GLU -> 1001, puis staging v1.1 ouverte."""
    release_service.create_initial(session, "v1.0", None, "test")
    add_mapping(session, "v1.0", "GLU", 1001, source_vocabulary_id="LABO", description="Glucose")
    release_service.publish(session, "v1.0", "build-1", "test")
    release_service.create_staging(session, "v1.1", None, "test")


def _import(
    session: Session,
    content: str | bytes,
    mode: str = "upsert",
    mapping: dict[str, str] | None = None,
    defaults: dict[str, str] | None = None,
    file_name: str = "fichier.csv",
) -> int:
    data = content.encode("utf-8") if isinstance(content, str) else content
    batch_id = import_service.create_batch(session, io.BytesIO(data), file_name, None, "testeur")
    choices: dict[str, dict[str, str]] = {k: {"file_column": v} for k, v in (mapping or MAPPING).items()}
    choices.update(
        {k: {"default_value": v} for k, v in (defaults or {"source_vocabulary_id": "LABO"}).items()}
    )
    import_service.save_mapping(
        session, batch_id, ColumnMappingIn.model_validate({"load_mode": mode, "choices": choices})
    )
    import_service.run_import(session, batch_id, "testeur")
    return batch_id


def _rows(session: Session) -> dict[str, tuple[object, ...]]:
    rows = sql(
        session,
        """
        SELECT s.source_code, s.target_concept_id, s.target_vocabulary_id, s.mapping_status,
               s.source_code_description, s.mapped_by
          FROM mapping.source_to_concept_map s
          JOIN mapping.release r USING (release_id)
         WHERE r.label = 'v1.1'
        """,
    )
    return {str(r[0]): r[1:] for r in rows}


def _errors(session: Session, batch_id: int) -> dict[int, str]:
    rows = sql(
        session,
        "SELECT row_number, error_message FROM mapping.import_error WHERE import_batch_id = :b",
        b=batch_id,
    )
    return {int(r[0]): str(r[1]) for r in rows}  # type: ignore[call-overload]


def _batch(session: Session, batch_id: int) -> tuple[object, ...]:
    return sql(
        session,
        "SELECT status, rows_read, rows_loaded, rows_rejected"
        " FROM mapping.import_batch WHERE import_batch_id = :b",
        b=batch_id,
    )[0]


def test_import_requires_open_release(session: Session) -> None:
    with pytest.raises(BusinessError, match="Aucune release ouverte"):
        import_service.create_batch(session, io.BytesIO(b"a;b\n1;2\n"), "f.csv", None, "x")


def test_insert_mode_rejects_existing_keys_and_applies_defaults(session: Session) -> None:
    _staging(session)
    content = HEADER + "GLU;Glucose;1001;\nHB;Hémoglobine;1002;\n"
    batch_id = _import(
        session, content, "insert", defaults={"source_vocabulary_id": "LABO", "mapped_by": "équipe"}
    )
    assert _batch(session, batch_id) == ("partial", 2, 1, 1)
    assert "clé déjà présente" in _errors(session, batch_id)[2]
    # target_vocabulary_id déduit du concept, statut par défaut du DDL, valeur par défaut appliquée
    assert _rows(session)["HB"] == (1002, "LOINC", "UNCHECKED", "Hémoglobine", "équipe")


def test_upsert_updates_only_provided_columns(session: Session) -> None:
    _staging(session)
    content = "code;concept;statut\nGLU;1001;APPROVED\nDT2;2001;approved\n"
    mapping = {"source_code": "code", "target_concept_id": "concept", "mapping_status": "statut"}
    batch_id = _import(session, content, "upsert", mapping=mapping)
    assert _batch(session, batch_id) == ("loaded", 2, 2, 0)
    rows = _rows(session)
    # GLU identique : non réécrit, garde le statut validé à la publication de v1.0 ; description conservée
    assert rows["GLU"] == (1001, "LOINC", "APPROVED", "Glucose", None)
    # DT2 ajouté par l'import : à relire, quel que soit le statut du fichier
    assert rows["DT2"] == (2001, "SNOMED", "UNCHECKED", None, None)


def test_replace_vocabulary_deletes_then_reloads(session: Session) -> None:
    _staging(session)
    add_mapping(session, "v1.1", "OTHER", 1002, source_vocabulary_id="AUTRE")
    batch_id = _import(session, HEADER + "HB;Hb;1002;\n", "replace_vocabulary")
    assert _batch(session, batch_id) == ("loaded", 1, 1, 0)
    rows = _rows(session)
    assert set(rows) == {"HB", "OTHER"}
    deleted = sql(session, "SELECT count(*) FROM mapping.audit_log WHERE operation = 'DELETE'")
    assert deleted == [(1,)]


def test_rejects_invalid_rows_with_messages(session: Session) -> None:
    _staging(session)
    long_code = "X" * 51
    content = (
        "code;libelle;concept;statut;debut\n"
        "OK1;ok;1001;APPROVED;2024-01-01\n"
        f"{long_code};trop long;1001;;\n"
        "NOINT;pas entier;abc;;\n"
        "NOCONCEPT;absent;9999;;\n"
        "BADSTATUS;statut;1001;VALIDE;\n"
        "BADDATE;date;1001;;2023-02-30\n"
        ";sans code;1001;;\n"
        "OK1;doublon;1001;;\n"
    )
    mapping = {**MAPPING, "mapping_status": "statut", "valid_start_date": "debut"}
    batch_id = _import(session, content, "upsert", mapping=mapping)
    errors = _errors(session, batch_id)
    assert _batch(session, batch_id) == ("partial", 8, 1, 7)
    assert "dépasse 50 caractères" in errors[3]
    assert "n'est pas un entier" in errors[4]
    assert "absent de vocab.concept" in errors[5]
    assert "mapping_status non autorisé" in errors[6]
    assert "date valide" in errors[7]
    assert "source_code obligatoire" in errors[8]
    assert "double" in errors[9]
    assert _rows(session)["OK1"][2] == "UNCHECKED"  # statut du fichier ignoré : tout import est à relire


def test_vocabulary_mismatch_is_rejected(session: Session) -> None:
    _staging(session)
    content = "code;concept;vocab\nA;1001;SNOMED\nB;1001;LOINC\n"
    mapping = {"source_code": "code", "target_concept_id": "concept", "target_vocabulary_id": "vocab"}
    batch_id = _import(session, content, mapping=mapping)
    assert "incohérent" in _errors(session, batch_id)[2]
    assert set(_rows(session)) == {"GLU", "B"}


def test_required_column_without_source_is_refused(session: Session) -> None:
    _staging(session)
    batch_id = import_service.create_batch(session, io.BytesIO(HEADER.encode()), "f.csv", None, "x")
    data = ColumnMappingIn.model_validate({"choices": {"source_code": {"file_column": "code"}}})
    with pytest.raises(BusinessError, match="target_concept_id est obligatoire"):
        import_service.save_mapping(session, batch_id, data)


def test_custom_columns_are_validated_and_typed(session: Session) -> None:
    _staging(session)
    custom_column_service.create_column(
        session, CustomColumnIn(column_name="priorite", label="Priorité", data_type="integer")
    )
    custom_column_service.create_column(
        session, CustomColumnIn(column_name="source", label="Source", allowed_values=["LABO", "DIM"])
    )
    content = "code;concept;prio;src\nA;1001;3;LABO\nB;1001;haute;DIM\nC;1001;1;AUTRE\n"
    mapping = {"source_code": "code", "target_concept_id": "concept", "priorite": "prio", "source": "src"}
    batch_id = _import(session, content, mapping=mapping)
    errors = _errors(session, batch_id)
    assert "type integer attendu" in errors[3]
    assert "valeur non autorisée" in errors[4]
    extra = sql(session, "SELECT extra FROM mapping.source_to_concept_map WHERE source_code = 'A'")
    assert extra == [({"priorite": 3, "source": "LABO"},)]


def test_excel_import(session: Session, tmp_path: Path) -> None:
    _staging(session)
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Mappings"
    sheet.append(["code", "libelle", "concept"])
    sheet.append(["XL1", "Depuis Excel", 1002])
    sheet.append([12345, "Code numérique", 2001.0])
    path = tmp_path / "mappings.xlsx"
    workbook.save(path)
    batch_id = _import(session, path.read_bytes(), file_name="mappings.xlsx")
    assert _batch(session, batch_id) == ("loaded", 2, 2, 0)
    assert _rows(session)["12345"][0] == 2001


def test_detects_cp1252_and_comma_separator(tmp_path: Path) -> None:
    path = tmp_path / "f.csv"
    path.write_bytes("code,libellé\nA,Hémoglobine\n".encode("cp1252"))
    fmt = file_reader.detect_csv_format(path)
    assert (fmt.encoding, fmt.separator) == ("cp1252", ",")
    columns, rows = file_reader.preview(path, None)
    assert columns == ["code", "libellé"]
    assert rows == [["A", "Hémoglobine"]]


def test_suggest_choices_by_normalized_name() -> None:
    choices = import_service.suggest_choices(
        ["Source_Code", "Libellé", "conceptId"],
        [
            ("source_code", ()),
            ("source_code_description", ("libelle",)),
            ("target_concept_id", ("conceptid",)),
        ],
        None,
    )
    assert choices["source_code"]["file_column"] == "Source_Code"
    assert choices["source_code_description"]["file_column"] == "Libellé"
    assert choices["target_concept_id"]["file_column"] == "conceptId"


def test_batch_already_loaded_cannot_be_rerun(session: Session) -> None:
    _staging(session)
    batch_id = _import(session, HEADER + "HB;Hb;1002;\n")
    with pytest.raises(BusinessError, match="déjà traité"):
        import_service.run_import(session, batch_id, "x")


def test_upsert_skips_unchanged_rows(session: Session) -> None:
    _staging(session)
    content = HEADER + "GLU;Glucose;1001;\nHB;Hb;1002;\n"
    _import(session, content)
    sql(session, "TRUNCATE mapping.audit_log")
    batch_id = _import(session, content.replace("Hb;", "Hémoglobine;"))
    assert _batch(session, batch_id) == ("loaded", 2, 2, 0)
    audit = sql(session, "SELECT new_row ->> 'source_code', changed_by FROM mapping.audit_log")
    assert audit == [("HB", "testeur")]


def test_import_forces_unchecked_but_keeps_flagged_and_ignored(session: Session) -> None:
    _staging(session)
    content = "code;concept;statut\nA;1001;APPROVED\nB;1001;IGNORED\nC;1001;flagged\nGLU;1001;IGNORED\n"
    mapping = {"source_code": "code", "target_concept_id": "concept", "mapping_status": "statut"}
    _import(session, content, mapping=mapping)
    rows = _rows(session)
    assert (rows["A"][2], rows["B"][2], rows["C"][2]) == ("UNCHECKED", "IGNORED", "FLAGGED")
    assert rows["GLU"][2] == "IGNORED"  # passage explicite à IGNORED : considéré comme une modification


def _loaded_keys(session: Session) -> dict[str, tuple[object, ...]]:
    return _rows(session)


def test_rollback_insert_and_upsert(session: Session) -> None:
    _staging(session)
    before = _loaded_keys(session)
    batch_id = _import(session, HEADER + "GLU;Glucose modifié;1001;\nHB;Hb;1002;\n")
    assert _rows(session)["GLU"][3] == "Glucose modifié"
    effects = import_service.report(session, batch_id, 1).effects
    assert effects == {"INSERT": 1, "UPDATE": 1}
    result = import_service.rollback(session, batch_id, "annuleur")
    assert (result.deleted, result.restored, result.reinserted) == (1, 1, 0)
    assert _loaded_keys(session) == before
    assert _batch(session, batch_id)[0] == "rolled_back"
    with pytest.raises(BusinessError, match="non annulable"):
        import_service.rollback(session, batch_id, "x")


def test_rollback_replace_vocabulary_restores_deleted_rows(session: Session) -> None:
    _staging(session)
    add_mapping(session, "v1.1", "OLD", 1002, source_vocabulary_id="LABO")
    before = _loaded_keys(session)
    batch_id = _import(session, HEADER + "NEW;Nouveau;1001;\n", "replace_vocabulary")
    assert set(_rows(session)) == {"NEW"}
    result = import_service.rollback(session, batch_id, "x")
    assert (result.deleted, result.reinserted) == (1, 2)
    assert _loaded_keys(session) == before


def test_rollback_refused_after_manual_correction(session: Session) -> None:
    _staging(session)
    batch_id = _import(session, HEADER + "HB;Hb;1002;\n")
    sql(
        session,
        "UPDATE mapping.source_to_concept_map SET mapping_comment = 'corrigé' WHERE source_code = 'HB'",
    )
    with pytest.raises(BusinessError, match="modifiée"):
        import_service.rollback(session, batch_id, "x")


def test_rollback_refused_once_release_published(session: Session) -> None:
    _staging(session)
    batch_id = _import(session, HEADER + "HB;Hb;1002;\n")
    release_service.promote(session, "v1.1", "v2.0", "x")
    with pytest.raises(BusinessError, match="archivé"):
        import_service.rollback(session, batch_id, "x")
    batch, release = import_service._get_batch_for_display(session, batch_id)
    assert import_service.batch_state(batch, release) == "archived"


def test_delete_pending_batch(session: Session) -> None:
    _staging(session)
    batch_id = import_service.create_batch(session, io.BytesIO(HEADER.encode()), "f.csv", None, "x")
    assert import_service.delete_batch(session, batch_id) == "f.csv"
    assert sql(
        session, "SELECT count(*) FROM mapping.import_batch WHERE import_batch_id = :b", b=batch_id
    ) == [(0,)]
    loaded = _import(session, HEADER + "HB;Hb;1002;\n")
    with pytest.raises(BusinessError, match="Annuler"):
        import_service.delete_batch(session, loaded)


def test_usagi_concept_dates_are_not_prefilled() -> None:
    columns = [
        "sourceCode",
        "sourceName",
        "conceptId",
        "valid_start_date",
        "valid_end_date",
        "invalid_reason",
    ]
    targets = [
        ("source_code", ("sourcecode",)),
        ("valid_start_date", ()),
        ("valid_end_date", ()),
        ("invalid_reason", ()),
    ]
    choices = import_service.suggest_choices(columns, targets, None)
    assert choices["source_code"]["file_column"] == "sourceCode"
    assert all(
        choices[t]["file_column"] is None for t in ("valid_start_date", "valid_end_date", "invalid_reason")
    )
    # fichier non Usagi : correspondance par nom conservée
    plain = import_service.suggest_choices(["code", "valid_start_date"], targets, None)
    assert plain["valid_start_date"]["file_column"] == "valid_start_date"
