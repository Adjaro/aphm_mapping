"""Recherche à facettes : compteurs à la Athena, texte, tri, pagination."""

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Release
from app.schemas.search import NULL_VALUE, SearchFilter
from app.services import release_service, search_service
from tests.conftest import add_mapping


def _release(session: Session) -> Release:
    release_service.create_initial(session, "v1.0", None, "test")
    add_mapping(session, "v1.0", "GLU", 1001, source_vocabulary_id="LABO", mapping_status="APPROVED")
    add_mapping(session, "v1.0", "HB", 1002, source_vocabulary_id="LABO")
    add_mapping(
        session,
        "v1.0",
        "E11",
        2001,
        source_vocabulary_id="CIM10",
        target_vocabulary_id="SNOMED",
        domain_id="Condition",
        description="Diabète de type 2",
    )
    add_mapping(session, "v1.0", "X", 9999, source_vocabulary_id="CIM10", domain_id=None)
    release = release_service.resolve_release(session, "v1.0")
    assert release is not None
    return release


def _facet(result: search_service.SearchResultOut, name: str) -> dict[str, int]:
    facet = next(f for f in result.facets if f.name == name)
    return {v.value: v.count for v in facet.values}


def test_facet_counts_ignore_their_own_filter(session: Session) -> None:
    release = _release(session)
    result = search_service.search(session, release, SearchFilter(source_vocabulary=["LABO"]))
    assert result.total == 2
    # la facette filtrée garde les compteurs de toutes ses valeurs
    assert _facet(result, "source_vocabulary") == {"LABO": 2, "CIM10": 2}
    # les autres facettes sont restreintes au filtre actif
    assert _facet(result, "mapping_status") == {"UNCHECKED": 1, "APPROVED": 1}
    assert _facet(result, "domain") == {"Measurement": 2}


def test_null_values_and_quality_facet(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    release = _release(session)
    result = search_service.search(session, release, SearchFilter(domain=[NULL_VALUE]))
    assert [row["source_code"] for row in result.rows] == ["X"]
    assert _facet(result, "domain")[NULL_VALUE] == 1
    # Rubrique Qualité masquée par défaut (SHOW_QUALITY=false), disponible si on la réactive
    assert all(f.name != "quality" for f in result.facets)
    monkeypatch.setattr(get_settings(), "show_quality", True)
    search_service.clear_search_cache()
    result = search_service.search(session, release, SearchFilter(domain=[NULL_VALUE]))
    assert _facet(result, "quality") == {"TARGET_NOT_FOUND": 1}


def test_text_search_on_code_description_and_target(session: Session) -> None:
    release = _release(session)
    for query, expected in (("diab", {"E11"}), ("hemoglobin", {"HB"}), ("glu", {"GLU"}), ("1002", {"HB"})):
        result = search_service.search(session, release, SearchFilter(query=query))
        assert {row["source_code"] for row in result.rows} == expected, query


def test_sort_and_pagination(session: Session) -> None:
    release = _release(session)
    flt = SearchFilter(sort="source_code", order="desc", page_size=15)
    result = search_service.search(session, release, flt)
    assert [row["source_code"] for row in result.rows] == ["X", "HB", "GLU", "E11"]
    invalid = search_service.search(session, release, SearchFilter(sort="DROP TABLE", page_size=7, page=99))
    assert invalid.filter.sort == "source_code"
    assert invalid.filter.page_size == 30
    assert invalid.filter.page == 1


def test_url_round_trip() -> None:
    flt = SearchFilter(release="v1.1", query="hb", domain=["Measurement", "Observation"], page=2)
    url = flt.url()
    assert url.startswith("/search-terms/terms?release=v1.1&query=hb&domain=Measurement&domain=Observation")
    assert "page=2" in flt.url()
    assert "domain=Observation" not in flt.without("domain", "Observation")


def test_cardinality_facets(session: Session) -> None:
    release = _release(session)
    add_mapping(session, "v1.0", "GLU", 1002, source_vocabulary_id="LABO")  # GLU -> 1001 et 1002
    result = search_service.search(session, release, SearchFilter(source_targets=["multiple"]))
    assert sorted(row["source_code"] for row in result.rows) == ["GLU", "GLU"]
    assert all(row["n_targets"] == 2 for row in result.rows)
    assert _facet(result, "source_targets") == {"multiple": 2, "single": 3}
    shared = search_service.search(session, release, SearchFilter(target_sources=["multiple"]))
    # 1002 est la cible de HB et de GLU
    assert sorted(row["source_code"] for row in shared.rows) == ["GLU", "HB"]
    labels = {
        v.value: (v.label, v.help) for f in shared.facets if f.name == "target_sources" for v in f.values
    }
    assert labels["multiple"][0] == "Cible partagée par plusieurs codes"
    assert labels["multiple"][1] is not None


def test_facet_values_have_help(session: Session) -> None:
    release = _release(session)
    result = search_service.search(session, release, SearchFilter())
    status = next(f for f in result.facets if f.name == "mapping_status")
    unchecked = next(v for v in status.values if v.value == "UNCHECKED")
    assert unchecked.help is not None and "pas encore relu" in unchecked.help
    equivalence = next(f for f in result.facets if f.name == "equivalence")
    empty = next(v for v in equivalence.values if v.value == NULL_VALUE)
    assert empty.label == "(non renseignée)" and empty.help is not None


def test_reverse_search_by_target_concept(session: Session) -> None:
    release = _release(session)
    add_mapping(session, "v1.0", "GLY", 1001, source_vocabulary_id="AUTRE")
    result = search_service.search(session, release, SearchFilter(target_concept=1001))
    assert sorted(row["source_code"] for row in result.rows) == ["GLU", "GLY"]
    assert result.target is not None
    assert result.target["concept"]["concept_name"] == "Glucose [Mass/volume] in Serum"
    assert result.target["n_source_codes"] == len(result.rows)
    assert ("target_concept", "1001", "Concept cible n° 1001") in result.filter.active_filters()


def test_search_scope(session: Session) -> None:
    release = _release(session)
    # « glucose » n'apparaît que dans le libellé du concept cible de GLU
    assert search_service.search(session, release, SearchFilter(query="glucose", scope="source")).total == 0
    assert search_service.search(session, release, SearchFilter(query="glucose", scope="target")).total == 1
    # « E11 » n'est que le code source (le concept cible s'appelle « Type 2 diabetes mellitus »)
    assert search_service.search(session, release, SearchFilter(query="E11", scope="target")).total == 0
    assert search_service.search(session, release, SearchFilter(query="E11", scope="source")).total == 1
    assert "scope=target" in SearchFilter(query="x", scope="target").url()
    assert "scope" not in SearchFilter(query="x").url()


def test_free_text_multi_words_accents_and_relevance(session: Session) -> None:
    release = _release(session)
    add_mapping(session, "v1.0", "GLY2", 1001, description="Glycémie à jeun sur sérum")
    add_mapping(session, "v1.0", "SERUM-GLU", 1001, description="Sérum glycémie")
    # mots dans le désordre, sans accents, répartis entre libellé source et libellé cible
    words = search_service.search(session, release, SearchFilter(query="serum glycemie"))
    assert sorted(row["source_code"] for row in words.rows) == ["GLY2", "SERUM-GLU"]
    across = search_service.search(session, release, SearchFilter(query="jeun glucose"))
    assert [row["source_code"] for row in across.rows] == ["GLY2"]  # « glucose » = libellé du concept cible
    # code exact en premier (tri par pertinence)
    ranked = search_service.search(session, release, SearchFilter(query="GLU"))
    assert ranked.filter.sort == "relevance"
    assert ranked.rows[0]["source_code"] == "GLU"


def test_suggestions(session: Session) -> None:
    release = _release(session)
    found = search_service.suggestions(session, release, "glu", "all")
    assert [s["source_code"] for s in found.sources] == ["GLU"]
    assert [t["target_concept_id"] for t in found.targets] == [1001]
    assert found.targets[0]["n_sources"] == 1
    assert search_service.suggestions(session, release, "g", "all").sources == []
    only_targets = search_service.suggestions(session, release, "1002", "target")
    assert only_targets.sources == [] and [t["target_concept_id"] for t in only_targets.targets] == [1002]


def test_search_cache_invalidated_by_changes(session: Session) -> None:
    release = _release(session)
    before = search_service.search(session, release, SearchFilter())
    assert search_service.search(session, release, SearchFilter()) is before  # résultat réutilisé
    add_mapping(session, "v1.0", "NOUVEAU", 1001)
    after = search_service.search(session, release, SearchFilter())
    assert after.total == before.total + 1
