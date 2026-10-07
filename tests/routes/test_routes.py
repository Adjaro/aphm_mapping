"""Routes principales : statut 200, fragments HTMX, assistant d'import, actions de release, exports."""

import re

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.services import release_service
from tests.conftest import add_mapping, sql

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
    assert sql(session, "SELECT changed_by FROM mapping.audit_log") == [("claire",)]


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
