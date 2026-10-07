"""Routes principales : statut 200, fragments HTMX, assistant d'import, actions de release, exports."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import get_settings
from app.services import release_service
from tests.conftest import _TEST_URL, add_mapping, sql

HX = {"HX-Request": "true"}


def _cycle(session: Session) -> None:
    release_service.create_initial(session, "v1.0", None, "test")
    add_mapping(session, "v1.0", "GLU", 1001, source_vocabulary_id="LABO")
    add_mapping(
        session,
        "v1.0",
        "/mL",
        3001,
        source_vocabulary_id="UNIT",
        target_vocabulary_id="UCUM",
        domain_id="Unit",
    )
    release_service.publish(session, "v1.0", "build-1", "test")
    release_service.create_staging(session, "v1.1", None, "test")


def test_pages_without_release(client: TestClient) -> None:
    assert client.get("/").status_code == 200
    assert client.get("/releases").status_code == 200
    assert client.get("/search-terms/terms").status_code == 404
    assert client.get("/imports/new").status_code == 200


def test_main_pages_return_200(client: TestClient, session: Session) -> None:
    _cycle(session)
    for url in (
        "/",
        "/search-terms/terms",
        "/search-terms/terms?release=v1.1&query=glu&source_vocabulary=LABO&page=1&page_size=30&sort=source_code&order=asc",
        "/mappings/LABO/GLU?release=v1.1",
        "/mappings/UNIT/%2FmL?release=v1.0",
        "/imports",
        "/releases",
        "/releases/diff?from=v1.0&to=v1.1",
        "/quality?release=v1.1",
        "/settings/custom-columns",
        "/api/releases",
        "/vocab/concepts/lookup?q=glucose",
    ):
        response = client.get(url)
        assert response.status_code == 200, url
    assert client.get("/mappings/LABO/INCONNU").status_code == 404


def test_search_returns_fragment_for_htmx(client: TestClient, session: Session) -> None:
    _cycle(session)
    full = client.get("/search-terms/terms?release=v1.0")
    fragment = client.get("/search-terms/terms?release=v1.0", headers=HX)
    assert "<html" in full.text and "<html" not in fragment.text
    assert 'id="ref-facet-panel"' in fragment.text
    history = client.get(
        "/search-terms/terms?release=v1.0", headers={**HX, "HX-History-Restore-Request": "true"}
    )
    assert "<html" in history.text


def test_search_export_streams_all_rows(client: TestClient, session: Session) -> None:
    _cycle(session)
    response = client.get("/search-terms/terms/export.csv?release=v1.1&page_size=15")
    lines = response.text.lstrip("﻿").strip().splitlines()
    assert lines[0].startswith("source_code;")
    assert len(lines) == 3


def test_cdm_api_export(client: TestClient, session: Session) -> None:
    _cycle(session)
    response = client.get("/api/releases/v1.0/source_to_concept_map.csv")
    assert response.status_code == 200
    assert response.text.splitlines()[0] == (
        "source_code,source_concept_id,source_vocabulary_id,source_code_description,target_concept_id,"
        "target_vocabulary_id,valid_start_date,valid_end_date,invalid_reason"
    )
    assert client.get("/api/releases/v9.9/source_to_concept_map.csv").status_code == 404
    assert [r["label"] for r in client.get("/api/releases").json()] == ["v1.1", "v1.0"]


def test_edit_mapping_through_form(client: TestClient, session: Session) -> None:
    _cycle(session)
    stcm_id = sql(
        session,
        "SELECT stcm_id FROM mapping.source_to_concept_map s JOIN mapping.release r USING (release_id) "
        "WHERE r.label = 'v1.1' AND s.source_code = 'GLU'",
    )[0][0]
    form = client.get(f"/mappings/{stcm_id}/edit", headers=HX)
    assert form.status_code == 200 and "target_concept_id" in form.text
    client.cookies.set("ref_user", "claire")
    invalid = client.post(f"/mappings/{stcm_id}/edit", data={"target_concept_id": "abc"}, headers=HX)
    assert invalid.status_code == 422 and "is-invalid" in invalid.text
    response = client.post(
        f"/mappings/{stcm_id}/edit",
        data={"target_concept_id": "1002", "relationship_id": "Maps to", "mapping_status": "APPROVED"},
        headers=HX,
    )
    assert response.status_code == 204
    assert response.headers["HX-Redirect"].startswith("/mappings/LABO/GLU?release=v1.1")
    claire = sql(session, "SELECT changed_by FROM mapping.audit_log WHERE changed_by = 'claire'")
    assert claire == [("claire",)]


def test_release_actions(client: TestClient, session: Session) -> None:
    _cycle(session)
    refused = client.post("/releases/staging", data={"label": "v1.2"}, follow_redirects=False)
    assert refused.status_code == 303 and "error=" in refused.headers["location"]
    promoted = client.post("/releases/v1.1/promote", data={"new_label": "v2.0"}, headers=HX)
    assert promoted.status_code == 204 and "notice=" in promoted.headers["HX-Redirect"]
    published = client.post(
        "/releases/v2.0/publish", data={"cdm_build_ref": "build-2"}, follow_redirects=False
    )
    assert published.status_code == 303
    statuses = {str(k): str(v) for k, v in sql(session, "SELECT label, status FROM mapping.release")}
    assert statuses == {"v1.0": "published", "v1.1": "archived", "v2.0": "published"}


def test_import_wizard_end_to_end(client: TestClient, session: Session) -> None:
    _cycle(session)
    content = "CODE_LOCAL;Libellé;concept_id\nHB;Hémoglobine;1002\nBAD;Inconnu;424242\n".encode()
    upload = client.post(
        "/imports/new",
        files={"file": ("labo.csv", content, "text/csv")},
        data={"source_vocabulary_id": "LABO"},
        follow_redirects=False,
    )
    assert upload.status_code == 303
    columns_url = upload.headers["location"]
    batch_id = int(re.findall(r"\d+", columns_url)[0])
    page = client.get(columns_url)
    assert page.status_code == 200
    # pré-remplissage par nom de colonne et valeur par défaut issue de l'upload
    assert "<option selected>CODE_LOCAL</option>" in page.text
    assert 'name="default__source_vocabulary_id"' in page.text and 'value="LABO"' in page.text
    saved = client.post(
        columns_url,
        data={
            "file__source_code": "CODE_LOCAL",
            "file__source_code_description": "Libellé",
            "file__target_concept_id": "concept_id",
            "default__source_vocabulary_id": "LABO",
            "load_mode": "upsert",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303 and saved.headers["location"].endswith("/validate")
    assert client.get(f"/imports/{batch_id}/validate").status_code == 200
    loaded = client.post(f"/imports/{batch_id}/validate", headers=HX)
    assert loaded.status_code == 204
    report = client.get(f"/imports/{batch_id}")
    assert "absent de vocab.concept" in report.text
    errors = client.get(f"/imports/{batch_id}/errors.csv")
    assert "424242" in errors.text
    duplicate = client.post(
        "/imports/new", files={"file": ("labo.csv", content, "text/csv")}, follow_redirects=True
    )
    assert "déjà été importé" in duplicate.text


def test_upload_rejects_unsupported_format(client: TestClient, session: Session) -> None:
    _cycle(session)
    response = client.post("/imports/new", files={"file": ("notes.pdf", b"%PDF", "application/pdf")})
    assert response.status_code == 422
    assert "Format non pris en charge" in response.text


def test_custom_columns_page(client: TestClient, session: Session) -> None:
    invalid = client.post(
        "/settings/custom-columns", data={"action": "create", "column_name": "Bad Name", "label": "x"}
    )
    assert invalid.status_code == 422 and "is-invalid" in invalid.text
    created = client.post(
        "/settings/custom-columns",
        data={
            "action": "create",
            "column_name": "unite",
            "label": "Unité",
            "data_type": "text",
            "allowed_values": "mg, g",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    assert sql(session, "SELECT allowed_values FROM mapping.custom_column") == [(["mg", "g"],)]


def test_compare_and_athena_pages(client: TestClient, session: Session) -> None:
    _cycle(session)
    sql(
        session,
        """
        UPDATE mapping.source_to_concept_map s SET mapping_status = 'FLAGGED'
          FROM mapping.release r
         WHERE r.release_id = s.release_id AND r.label = 'v1.1' AND s.source_code = 'GLU'
        """,
    )
    page = client.get("/compare")
    assert page.status_code == 200
    assert "Autre modification" in page.text and "GLU" in page.text
    assert (
        client.get("/compare?from=v1.0&to=v1.1&change_kind=MODIFIED&field=mapping_status").status_code == 200
    )
    assert "Choisir deux releases" in client.get("/compare?from=v1.0&to=v1.0").text
    export = client.get("/compare/export.csv?from=v1.0&to=v1.1")
    assert "MODIFIED;LABO;GLU" in export.text
    assert client.get("/athena?release=v1.1").status_code == 200
    assert client.get("/athena/export.csv?release=v1.1").status_code == 200
    assert client.get("/settings/athena").status_code == 200


def test_athena_settings_forms(client: TestClient, session: Session) -> None:
    invalid = client.post(
        "/settings/athena/connections", data={"label": "x", "host": "h", "schema_name": "bad name"}
    )
    assert invalid.status_code == 422 and "is-invalid" in invalid.text
    created = client.post(
        "/settings/athena/connections",
        data={
            "label": "Athena",
            "host": "127.0.0.1",
            "port": str(make_url(_TEST_URL).port or 5432),
            "database_name": "base_absente",
            "username": "u",
            "password": "secret",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    page = client.get("/settings/athena")
    assert "secret" not in page.text and "mot de passe enregistré" in page.text
    saved = client.post(
        "/settings/athena/vocabulary-map",
        data={"source_vocabulary_id": "ICD10", "athena_vocabulary_id": "CIM10", "ignore_dots": "on"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert sql(session, "SELECT ignore_dots, ignore_case FROM mapping.athena_vocabulary_map") == [
        (True, False)
    ]
    failed = client.post("/settings/athena/connections/1/test", follow_redirects=False)
    assert "error=" in failed.headers["location"]


def test_author_cookie_is_decoded(client: TestClient, session: Session) -> None:
    _cycle(session)
    client.cookies.set("ref_user", "Jean%20Dupont")
    assert 'value="Jean Dupont"' in client.get("/").text


def test_export_page_download_and_write(
    client: TestClient, session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cycle(session)
    monkeypatch.setattr(get_settings(), "export_dir", tmp_path)
    page = client.get("/export?release=v1.0")
    assert page.status_code == 200 and "Écrire dans le dossier du serveur" in page.text
    csv_page = client.get("/export?release=v1.0&format=csv_full&source_vocabulary=LABO")
    assert "Télécharger le CSV" in csv_page.text
    archive = client.get("/export/download?release=v1.0&format=properties")
    assert archive.headers["content-type"] == "application/zip"
    cdm = client.get("/export/download?release=v1.0&format=csv_cdm&source_vocabulary=UNIT")
    assert cdm.text.splitlines()[1].startswith("/mL,0,UNIT")
    written = client.post(
        "/export/write",
        data={"release": "v1.0", "format": "properties", "source_vocabulary": "LABO"},
        headers=HX,
    )
    assert written.status_code == 204 and "notice=" in written.headers["HX-Redirect"]
    assert (tmp_path / "30_properties" / "Measurement.properties").read_text(encoding="utf-8") == "GLU=1001\n"


def test_api_page_and_properties_endpoint(client: TestClient, session: Session) -> None:
    _cycle(session)
    page = client.get("/api")
    assert page.status_code == 200 and "properties.zip" in page.text and "cdn" not in page.text.lower()
    assert client.get("/api/releases/v1.0/properties.zip").headers["content-type"] == "application/zip"
    assert client.get("/api/docs").status_code == 404  # Swagger UI (CDN) désactivé


def test_user_friendly_pages(client: TestClient, session: Session) -> None:
    _cycle(session)
    help_page = client.get("/aide")
    assert help_page.status_code == 200 and "Version modifiable en ce moment" in help_page.text
    releases = client.get("/releases")
    assert (
        "Prochaine étape" in releases.text
        and "publiée" in releases.text
        and ">published<" not in releases.text
    )
    detail = client.get("/mappings/LABO/GLU?release=v1.0")
    assert "Corriger dans la staging v1.1" in detail.text
    same = client.get("/compare?from=v1.0&to=v1.1")
    assert "sont identiques" in same.text
    athena = client.get("/athena?release=v1.1")
    assert "Mettre en place la comparaison avec Athena" in athena.text


def test_encoded_author_is_displayed_decoded(client: TestClient, session: Session) -> None:
    _cycle(session)
    sql(session, "UPDATE mapping.import_batch SET created_by = 'Jean%20Dupont'")
    content = b"code;concept\nX;1001\n"
    client.post(
        "/imports/new", files={"file": ("x.csv", content, "text/csv")}, data={"source_vocabulary_id": "LABO"}
    )
    sql(session, "UPDATE mapping.import_batch SET created_by = 'Jean%20Dupont'")
    assert "Jean Dupont" in client.get("/imports").text


def test_target_is_clickable_in_search_and_detail(client: TestClient, session: Session) -> None:
    _cycle(session)
    page = client.get("/search-terms/terms?release=v1.0")
    assert "target_concept=1001" in page.text
    reverse = client.get("/search-terms/terms?release=v1.0&target_concept=1001", headers=HX)
    assert "Codes source mappés vers le concept" in reverse.text and "GLU" in reverse.text
    assert "/mL" not in reverse.text
    detail = client.get("/mappings/LABO/GLU?release=v1.0")
    assert "target_concept=1001" in detail.text


def test_suggest_route_and_highlight(client: TestClient, session: Session) -> None:
    _cycle(session)
    fragment = client.get("/search-terms/suggest?release=v1.0&query=glu&scope=all")
    assert fragment.status_code == 200
    assert "<mark>GLU</mark>" in fragment.text and "target_concept=1001" in fragment.text
    assert client.get("/search-terms/suggest?release=v1.0&query=g").text.strip() == ""
    page = client.get("/search-terms/terms?release=v1.0&query=glu", headers=HX)
    assert "<mark>GLU</mark>" in page.text and "triés par pertinence" in page.text
    assert "Qualité" not in page.text  # rubrique masquée par défaut


def test_import_rollback_and_archive_routes(client: TestClient, session: Session) -> None:
    _cycle(session)
    content = "code;libelle;concept\nHB;Hémoglobine;1002\n".encode()
    upload = client.post(
        "/imports/new",
        files={"file": ("labo.csv", content, "text/csv")},
        data={"source_vocabulary_id": "LABO"},
        follow_redirects=False,
    )
    batch_id = int(re.findall(r"\d+", upload.headers["location"])[0])
    pending_list = client.get("/imports")
    assert f"/imports/{batch_id}/delete" in pending_list.text  # import en attente : supprimable
    client.post(
        f"/imports/{batch_id}/columns",
        data={
            "file__source_code": "code",
            "file__target_concept_id": "concept",
            "default__source_vocabulary_id": "LABO",
        },
    )
    client.post(f"/imports/{batch_id}/validate", headers=HX)
    report = client.get(f"/imports/{batch_id}")
    assert "Annuler l'import" in report.text and "1 ligne(s) ajoutée(s)" in report.text.replace(" ", "")
    undone = client.post(f"/imports/{batch_id}/rollback", headers=HX)
    assert "notice=" in undone.headers["HX-Redirect"]
    assert sql(session, "SELECT count(*) FROM mapping.source_to_concept_map WHERE source_code = 'HB'") == [
        (0,)
    ]
    assert "annulé" in client.get("/imports").text
    # Après promotion de la staging, les imports chargés dans v1.1 sont archivés
    second = client.post(
        "/imports/new", files={"file": ("labo2.csv", content, "text/csv")}, follow_redirects=False
    ).headers["location"]
    second_id = int(re.findall(r"\d+", second)[0])
    client.post(
        f"/imports/{second_id}/columns",
        data={
            "file__source_code": "code",
            "file__target_concept_id": "concept",
            "default__source_vocabulary_id": "LABO",
        },
    )
    client.post(f"/imports/{second_id}/validate", headers=HX)
    release_service.promote(session, "v1.1", "v2.0", "x")
    assert "archivé" in client.get(f"/imports/{second_id}").text
    refused = client.post(f"/imports/{second_id}/rollback", follow_redirects=False)
    assert "error=" in refused.headers["location"]
