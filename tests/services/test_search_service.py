"""Recherche à facettes : compteurs à la Athena, texte, tri, pagination."""

from sqlalchemy.orm import Session

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


def test_null_values_and_quality_facet(session: Session) -> None:
    release = _release(session)
    result = search_service.search(session, release, SearchFilter(domain=[NULL_VALUE]))
    assert [row["source_code"] for row in result.rows] == ["X"]
    assert _facet(result, "quality") == {"TARGET_NOT_FOUND": 1}
    assert _facet(result, "domain")[NULL_VALUE] == 1


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
    # « diabète » n'apparaît que dans la description source de E11 (le concept cible dit « diabetes »)
    assert search_service.search(session, release, SearchFilter(query="diabète", scope="target")).total == 0
    assert search_service.search(session, release, SearchFilter(query="diabète", scope="source")).total == 1
    assert "scope=target" in SearchFilter(query="x", scope="target").url()
    assert "scope" not in SearchFilter(query="x").url()
