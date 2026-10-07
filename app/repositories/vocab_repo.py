"""Lecture du vocabulaire (lookup d'un concept, sélecteur de concept cible). Lecture seule."""

from collections.abc import Sequence

from sqlalchemy import RowMapping, text
from sqlalchemy.orm import Session

CONCEPT_SQL = text(
    """
    SELECT c.concept_id,
           c.concept_name,
           c.domain_id,
           c.vocabulary_id,
           c.concept_class_id,
           c.standard_concept,
           c.concept_code,
           c.invalid_reason
      FROM vocab.concept c
     WHERE c.concept_id = :concept_id
    """
)

LOOKUP_SQL = text(
    """
    SELECT c.concept_id,
           c.concept_name,
           c.domain_id,
           c.vocabulary_id,
           c.concept_class_id,
           c.standard_concept,
           c.concept_code,
           c.invalid_reason
      FROM vocab.concept c
     WHERE (c.concept_name ILIKE :pattern ESCAPE '\\'
            OR c.concept_code ILIKE :pattern ESCAPE '\\'
            OR c.concept_id = :concept_id)
       AND (CAST(:domain AS text) IS NULL OR c.domain_id = :domain)
       AND (NOT :standard_only OR (c.standard_concept = 'S' AND c.invalid_reason IS NULL))
     ORDER BY (c.concept_id = :concept_id) DESC,
              (lower(c.concept_name) = lower(:query)) DESC,
              length(c.concept_name),
              c.concept_id
     LIMIT :limit
    """
)

DOMAINS_SQL = text(
    """
    SELECT DISTINCT c.domain_id
      FROM vocab.concept c
     WHERE c.standard_concept = 'S'
     ORDER BY c.domain_id
    """
)

VOCABULARY_VERSION_SQL = text(
    """
    SELECT v.vocabulary_version
      FROM vocab.vocabulary v
     WHERE v.vocabulary_id = 'None'
    """
)


def get_concept(session: Session, concept_id: int) -> RowMapping | None:
    return session.execute(CONCEPT_SQL, {"concept_id": concept_id}).mappings().first()


def lookup(
    session: Session, query: str, domain: str | None, standard_only: bool, limit: int = 30
) -> Sequence[RowMapping]:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    concept_id = int(query) if query.isdigit() and len(query) <= 9 else -1
    params = {
        "pattern": f"%{escaped}%",
        "query": query,
        "concept_id": concept_id,
        "domain": domain or None,
        "standard_only": standard_only,
        "limit": limit,
    }
    return session.execute(LOOKUP_SQL, params).mappings().all()


def standard_domains(session: Session) -> list[str]:
    return [row[0] for row in session.execute(DOMAINS_SQL)]


def vocabulary_version(session: Session) -> str | None:
    return session.execute(VOCABULARY_VERSION_SQL).scalar()
