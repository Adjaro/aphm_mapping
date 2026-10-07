"""Requêtes d'export d'une release (properties, CSV CDM, CSV complet), avec filtres communs."""

from collections.abc import Iterator, Sequence
from typing import Any

from sqlalchemy import RowMapping, text
from sqlalchemy.orm import Session

from app.schemas.export import ExportFilter

# Filtres communs (statiques) : vocabulaires (liste vide = tous), statuts, inclusion des cibles 0
FILTER_CLAUSE = """
     WHERE s.release_id = :release_id
       AND (cardinality(CAST(:source_vocabulary AS text[])) = 0
            OR s.source_vocabulary_id = ANY(:source_vocabulary))
       AND s.mapping_status = ANY(CAST(:mapping_status AS text[]))
       AND (:include_unmapped OR s.target_concept_id <> 0)
"""

# Une ligne par (domaine, code source) ; cibles triées numériquement, codes en ordre binaire (COLLATE "C").
# Domaine : celui du mapping, à défaut celui du concept cible.
PROPERTIES_SQL = text(
    """
    SELECT replace(coalesce(s.domain_id, c.domain_id, 'Unknown'), ' ', '_')     AS domain,
           s.source_code,
           array_to_string(
               array_agg(DISTINCT s.target_concept_id ORDER BY s.target_concept_id), ','
           )                                                                  AS targets
      FROM mapping.source_to_concept_map s
      LEFT JOIN vocab.concept c ON c.concept_id = s.target_concept_id
    """
    + FILTER_CLAUSE
    + """
     GROUP BY 1, 2
     ORDER BY 1, s.source_code COLLATE "C"
    """
).execution_options(yield_per=10000)

CDM_SQL = text(
    """
    SELECT s.source_code,
           s.source_concept_id,
           s.source_vocabulary_id,
           s.source_code_description,
           s.target_concept_id,
           s.target_vocabulary_id,
           s.valid_start_date,
           s.valid_end_date,
           s.invalid_reason
      FROM mapping.source_to_concept_map s
    """
    + FILTER_CLAUSE
    + """
     ORDER BY s.source_vocabulary_id, s.source_code, s.target_concept_id
    """
).execution_options(yield_per=10000)

FULL_SQL = text(
    """
    SELECT s.*,
           c.concept_name AS target_concept_name,
           c.domain_id    AS target_domain_id,
           q.quality_flag
      FROM mapping.source_to_concept_map s
      JOIN mapping.v_mapping_quality q ON q.stcm_id = s.stcm_id
      LEFT JOIN vocab.concept c ON c.concept_id = s.target_concept_id
    """
    + FILTER_CLAUSE
    + """
     ORDER BY s.source_vocabulary_id, s.source_code, s.target_concept_id
    """
).execution_options(yield_per=10000)

SUMMARY_SQL = text(
    """
    SELECT count(*)                                                     AS n_mappings,
           count(DISTINCT (s.source_vocabulary_id, s.source_code))      AS n_codes,
           count(DISTINCT coalesce(s.domain_id, c.domain_id, 'Unknown')) AS n_domains
      FROM mapping.source_to_concept_map s
      LEFT JOIN vocab.concept c ON c.concept_id = s.target_concept_id
    """
    + FILTER_CLAUSE
)

VOCABULARIES_SQL = text(
    """
    SELECT s.source_vocabulary_id,
           count(*) AS n
      FROM mapping.source_to_concept_map s
     WHERE s.release_id = :release_id
     GROUP BY s.source_vocabulary_id
     ORDER BY s.source_vocabulary_id
    """
)


def _params(release_id: int, flt: ExportFilter) -> dict[str, Any]:
    return {
        "release_id": release_id,
        "source_vocabulary": list(flt.source_vocabulary),
        "mapping_status": list(flt.mapping_status),
        "include_unmapped": flt.include_unmapped,
    }


def iter_properties(session: Session, release_id: int, flt: ExportFilter) -> Iterator[RowMapping]:
    yield from session.execute(PROPERTIES_SQL, _params(release_id, flt)).mappings()


def iter_cdm(session: Session, release_id: int, flt: ExportFilter) -> Iterator[RowMapping]:
    yield from session.execute(CDM_SQL, _params(release_id, flt)).mappings()


def iter_full(session: Session, release_id: int, flt: ExportFilter) -> Iterator[RowMapping]:
    yield from session.execute(FULL_SQL, _params(release_id, flt)).mappings()


def summary(session: Session, release_id: int, flt: ExportFilter) -> RowMapping:
    return session.execute(SUMMARY_SQL, _params(release_id, flt)).mappings().one()


def vocabularies(session: Session, release_id: int) -> Sequence[RowMapping]:
    return session.execute(VOCABULARIES_SQL, {"release_id": release_id}).mappings().all()
