"""Accès aux releases et appels des fonctions PostgreSQL de versionnement."""

from collections.abc import Iterator, Sequence
from typing import Any

from sqlalchemy import RowMapping, func, select, text
from sqlalchemy.orm import Session

from app.models import Release

DIFF_CHANGE_TYPES = ("ADDED", "REMOVED", "MODIFIED")


def list_releases(session: Session) -> Sequence[Release]:
    return session.scalars(
        select(Release).order_by(Release.created_at.desc(), Release.release_id.desc())
    ).all()


def get_by_label(session: Session, label: str) -> Release | None:
    return session.scalars(select(Release).where(Release.label == label)).first()


def get_by_id(session: Session, release_id: int) -> Release | None:
    return session.get(Release, release_id)


def latest_published(session: Session) -> Release | None:
    stmt = (
        select(Release)
        .where(Release.status == "published")
        .order_by(Release.published_at.desc().nulls_last(), Release.release_id.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()


def open_staging(session: Session) -> Release | None:
    stmt = select(Release).where(Release.kind == "staging", Release.status == "open")
    return session.scalars(stmt).first()


def open_releases(session: Session) -> Sequence[Release]:
    """Releases modifiables, la staging d'abord puis la plus récente."""
    stmt = (
        select(Release)
        .where(Release.status == "open")
        .order_by((Release.kind == "staging").desc(), Release.created_at.desc())
    )
    return session.scalars(stmt).all()


def count_releases(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Release)) or 0


def count_mappings(session: Session, release_id: int) -> int:
    stmt = text("SELECT count(*) FROM mapping.source_to_concept_map WHERE release_id = :release_id")
    return int(session.execute(stmt, {"release_id": release_id}).scalar_one())


def create_release(
    session: Session, label: str, kind: str, parent_label: str | None, description: str | None
) -> int:
    stmt = text("SELECT mapping.create_release(:label, :kind, :parent_label, :description)")
    params = {"label": label, "kind": kind, "parent_label": parent_label, "description": description}
    return int(session.execute(stmt, params).scalar_one())


def set_vocabulary_version(session: Session, release_id: int, version: str | None) -> None:
    stmt = text(
        """
        UPDATE mapping.release
           SET vocabulary_version = :version
         WHERE release_id = :release_id
        """
    )
    session.execute(stmt, {"version": version, "release_id": release_id})


def publish_release(session: Session, label: str, cdm_build_ref: str | None) -> None:
    stmt = text("SELECT mapping.publish_release(:label, :cdm_build_ref)")
    session.execute(stmt, {"label": label, "cdm_build_ref": cdm_build_ref})


def promote_staging(session: Session, staging_label: str, new_label: str) -> int:
    stmt = text("SELECT mapping.promote_staging(:staging_label, :new_label)")
    return int(session.execute(stmt, {"staging_label": staging_label, "new_label": new_label}).scalar_one())


def lineage(session: Session) -> Sequence[RowMapping]:
    stmt = text(
        """
        SELECT l.label,
               l.kind,
               l.status,
               l.parent_label,
               l.vocabulary_version,
               l.cdm_build_ref,
               l.created_at,
               l.published_at,
               l.n_mappings,
               r.description
          FROM mapping.v_release_lineage l
          JOIN mapping.release r ON r.label = l.label
         ORDER BY l.created_at DESC
        """
    )
    return session.execute(stmt).mappings().all()


DIFF_FACETS_SQL = """
WITH d AS (
    SELECT * FROM mapping.diff_releases(:from_label, :to_label)
)
SELECT 'change_type' AS facet, d.change_type AS value, count(*) AS n
  FROM d
 WHERE cardinality(CAST(:source_vocabularies AS text[])) = 0
    OR d.source_vocabulary_id = ANY(:source_vocabularies)
 GROUP BY d.change_type
UNION ALL
SELECT 'source_vocabulary' AS facet, d.source_vocabulary_id AS value, count(*) AS n
  FROM d
 WHERE cardinality(CAST(:change_types AS text[])) = 0
    OR d.change_type = ANY(:change_types)
 GROUP BY d.source_vocabulary_id
ORDER BY facet, n DESC, value
"""

DIFF_ROWS_SQL = """
SELECT d.change_type,
       d.source_vocabulary_id,
       d.source_code,
       d.target_concept_id,
       d.relationship_id,
       d.changed_fields,
       d.old_row,
       d.new_row,
       count(*) OVER () AS total
  FROM mapping.diff_releases(:from_label, :to_label) d
 WHERE (cardinality(CAST(:change_types AS text[])) = 0 OR d.change_type = ANY(:change_types))
   AND (cardinality(CAST(:source_vocabularies AS text[])) = 0
        OR d.source_vocabulary_id = ANY(:source_vocabularies))
 ORDER BY d.source_vocabulary_id, d.source_code, d.target_concept_id, d.change_type
 LIMIT :limit OFFSET :offset
"""

DIFF_EXPORT_SQL = """
SELECT d.change_type,
       d.source_vocabulary_id,
       d.source_code,
       d.target_concept_id,
       d.relationship_id,
       d.changed_fields,
       d.old_row,
       d.new_row
  FROM mapping.diff_releases(:from_label, :to_label) d
 WHERE (cardinality(CAST(:change_types AS text[])) = 0 OR d.change_type = ANY(:change_types))
   AND (cardinality(CAST(:source_vocabularies AS text[])) = 0
        OR d.source_vocabulary_id = ANY(:source_vocabularies))
 ORDER BY d.source_vocabulary_id, d.source_code, d.target_concept_id, d.change_type
"""


def _diff_params(
    from_label: str, to_label: str, change_types: Sequence[str], source_vocabularies: Sequence[str]
) -> dict[str, Any]:
    return {
        "from_label": from_label,
        "to_label": to_label,
        "change_types": list(change_types),
        "source_vocabularies": list(source_vocabularies),
    }


def diff_facets(
    session: Session,
    from_label: str,
    to_label: str,
    change_types: Sequence[str],
    source_vocabularies: Sequence[str],
) -> Sequence[RowMapping]:
    params = _diff_params(from_label, to_label, change_types, source_vocabularies)
    return session.execute(text(DIFF_FACETS_SQL), params).mappings().all()


def diff_rows(
    session: Session,
    from_label: str,
    to_label: str,
    change_types: Sequence[str],
    source_vocabularies: Sequence[str],
    limit: int,
    offset: int,
) -> Sequence[RowMapping]:
    params = _diff_params(from_label, to_label, change_types, source_vocabularies)
    params.update({"limit": limit, "offset": offset})
    return session.execute(text(DIFF_ROWS_SQL), params).mappings().all()


def iter_diff(
    session: Session,
    from_label: str,
    to_label: str,
    change_types: Sequence[str],
    source_vocabularies: Sequence[str],
) -> Iterator[RowMapping]:
    params = _diff_params(from_label, to_label, change_types, source_vocabularies)
    stmt = text(DIFF_EXPORT_SQL).execution_options(yield_per=5000)
    yield from session.execute(stmt, params).mappings()
