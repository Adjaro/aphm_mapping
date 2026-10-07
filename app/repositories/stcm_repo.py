"""Requêtes sur source_to_concept_map : recherche à facettes, fiche code source, édition."""

from collections.abc import Iterator, Sequence
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    ColumnElement,
    Integer,
    MetaData,
    RowMapping,
    Select,
    String,
    Table,
    Text,
    and_,
    case,
    cast,
    distinct,
    false,
    func,
    literal,
    or_,
    select,
    text,
    tuple_,
    union_all,
)
from sqlalchemy.orm import Session

from app.models import SourceToConceptMap
from app.schemas.search import FACET_LABELS, NULL_VALUE, SearchFilter

_metadata = MetaData()

# Vue de contrôle qualité (lecture seule ; définie dans 03_mapping.sql)
v_mapping_quality = Table(
    "v_mapping_quality",
    _metadata,
    Column("release_id", Integer),
    Column("stcm_id", BigInteger),
    Column("quality_flag", Text),
    Column("concept_name", String),
    Column("target_domain_id", String),
    Column("standard_concept", String),
    Column("invalid_reason", String),
    schema="mapping",
)

stcm = SourceToConceptMap.__table__
q = v_mapping_quality

VALIDITY_EXPR = case((stcm.c.invalid_reason.is_(None), "valide"), else_="invalide")

# Liste blanche : paramètre de facette -> colonne de la CTE de base
FACET_COLUMNS: dict[str, str] = {
    "source_vocabulary": "source_vocabulary_id",
    "domain": "domain_id",
    "target_vocabulary": "target_vocabulary_id",
    "mapping_status": "mapping_status",
    "equivalence": "equivalence",
    "relationship": "relationship_id",
    "quality": "quality_flag",
    "validity": "validity",
    "source_targets": "source_targets",
    "target_sources": "target_sources",
}

# Liste blanche : clé de tri -> colonne de la CTE de base
SORT_COLUMNS = (
    "source_code",
    "source_code_description",
    "source_vocabulary_id",
    "domain_id",
    "target_concept_id",
    "concept_name",
    "target_vocabulary_id",
    "mapping_status",
    "quality_flag",
)

RESULT_COLUMNS = (
    "stcm_id",
    "source_code",
    "source_code_description",
    "source_vocabulary_id",
    "domain_id",
    "target_concept_id",
    "concept_name",
    "target_vocabulary_id",
    "mapping_status",
    "equivalence",
    "relationship_id",
    "quality_flag",
    "validity",
    "n_targets",
    "n_sources",
)


def _cardinalities(release_id: int) -> tuple[Any, Any]:
    """Nombre de cibles par code source et de codes source par cible, calculés sur toute la release."""
    by_source = (
        select(
            stcm.c.source_vocabulary_id,
            stcm.c.source_code,
            func.count().label("n_targets"),
        )
        .where(stcm.c.release_id == release_id)
        .group_by(stcm.c.source_vocabulary_id, stcm.c.source_code)
        .subquery("by_source")
    )
    by_target = (
        select(
            stcm.c.target_concept_id,
            func.count(distinct(tuple_(stcm.c.source_vocabulary_id, stcm.c.source_code))).label("n_sources"),
        )
        .where(stcm.c.release_id == release_id)
        .group_by(stcm.c.target_concept_id)
        .subquery("by_target")
    )
    return by_source, by_target


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _base_select(release_id: int, flt: SearchFilter) -> Select[Any]:
    """Sélection de base : release + texte + import ; les facettes sont appliquées ensuite."""
    by_source, by_target = _cardinalities(release_id)
    stmt = (
        select(
            stcm.c.stcm_id,
            stcm.c.source_code,
            stcm.c.source_code_description,
            stcm.c.source_vocabulary_id,
            stcm.c.domain_id,
            stcm.c.target_concept_id,
            stcm.c.target_vocabulary_id,
            stcm.c.mapping_status,
            stcm.c.equivalence,
            stcm.c.relationship_id,
            stcm.c.import_batch_id,
            q.c.quality_flag,
            q.c.concept_name,
            VALIDITY_EXPR.label("validity"),
            by_source.c.n_targets,
            by_target.c.n_sources,
            case((by_source.c.n_targets > 1, "multiple"), else_="single").label("source_targets"),
            case((by_target.c.n_sources > 1, "multiple"), else_="single").label("target_sources"),
        )
        .select_from(
            stcm.join(q, q.c.stcm_id == stcm.c.stcm_id)
            .join(
                by_source,
                and_(
                    by_source.c.source_vocabulary_id == stcm.c.source_vocabulary_id,
                    by_source.c.source_code == stcm.c.source_code,
                ),
            )
            .join(by_target, by_target.c.target_concept_id == stcm.c.target_concept_id)
        )
        .where(stcm.c.release_id == release_id, q.c.release_id == release_id)
    )
    if flt.query:
        stmt = stmt.where(or_(*_text_conditions(flt.query, flt.scope)))
    if flt.import_batch is not None:
        stmt = stmt.where(stcm.c.import_batch_id == flt.import_batch)
    if flt.target_concept is not None:
        stmt = stmt.where(stcm.c.target_concept_id == flt.target_concept)
    return stmt


def _text_conditions(query: str, scope: str) -> list[ColumnElement[bool]]:
    """Recherche texte : codes source (code, description), concepts cibles (libellé, ID) ou les deux."""
    pattern = f"%{_escape_like(query)}%"
    conditions: list[ColumnElement[bool]] = []
    if scope in ("all", "source"):
        conditions.append(stcm.c.source_code.ilike(pattern, escape="\\"))
        conditions.append(stcm.c.source_code_description.ilike(pattern, escape="\\"))
    if scope in ("all", "target"):
        conditions.append(q.c.concept_name.ilike(pattern, escape="\\"))
        if query.isdigit() and len(query) <= 9:
            conditions.append(stcm.c.target_concept_id == int(query))
    return conditions


def _facet_condition(column: ColumnElement[Any], values: Sequence[str]) -> ColumnElement[bool]:
    plain = [v for v in values if v != NULL_VALUE]
    parts: list[ColumnElement[bool]] = []
    if plain:
        parts.append(cast(column, Text).in_(plain))
    if NULL_VALUE in values:
        parts.append(column.is_(None))
    return or_(*parts) if parts else false()


def _facet_filters(base: Any, flt: SearchFilter, exclude: str | None = None) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = []
    for facet, column_name in FACET_COLUMNS.items():
        values = flt.facet_values(facet)
        if facet != exclude and values:
            filters.append(_facet_condition(base.c[column_name], values))
    return filters


def count_results(session: Session, release_id: int, flt: SearchFilter) -> int:
    base = _base_select(release_id, flt).cte("base")
    stmt = select(func.count()).select_from(base).where(and_(True, *_facet_filters(base, flt)))
    return int(session.execute(stmt).scalar_one())


def _results_select(release_id: int, flt: SearchFilter) -> Select[Any]:
    base = _base_select(release_id, flt).cte("base")
    sort_column = base.c[flt.sort if flt.sort in SORT_COLUMNS else "source_code"]
    ordering = sort_column.desc().nulls_last() if flt.order == "desc" else sort_column.asc().nulls_last()
    return (
        select(*(base.c[name] for name in RESULT_COLUMNS))
        .where(and_(True, *_facet_filters(base, flt)))
        .order_by(ordering, base.c.stcm_id)
    )


def search(session: Session, release_id: int, flt: SearchFilter) -> Sequence[RowMapping]:
    stmt = _results_select(release_id, flt).limit(flt.page_size).offset((flt.page - 1) * flt.page_size)
    return session.execute(stmt).mappings().all()


def iter_search(session: Session, release_id: int, flt: SearchFilter) -> Iterator[RowMapping]:
    """Tous les résultats filtrés, lus par blocs (export CSV en streaming)."""
    stmt = _results_select(release_id, flt).execution_options(yield_per=5000)
    yield from session.execute(stmt).mappings()


def facet_counts(
    session: Session, release_id: int, flt: SearchFilter
) -> dict[str, list[tuple[str | None, int]]]:
    """Compteurs par facette ; chaque facette ignore son propre filtre (comportement Athena)."""
    base = _base_select(release_id, flt).cte("base")
    parts = []
    for facet, column_name in FACET_COLUMNS.items():
        column = base.c[column_name]
        parts.append(
            select(
                literal(facet).label("facet"),
                cast(column, Text).label("value"),
                func.count().label("n"),
            )
            .where(and_(True, *_facet_filters(base, flt, exclude=facet)))
            .group_by(column)
        )
    rows = session.execute(union_all(*parts)).all()
    result: dict[str, list[tuple[str | None, int]]] = {facet: [] for facet in FACET_LABELS}
    for facet, value, n in rows:
        result[facet].append((value, int(n)))
    for values in result.values():
        values.sort(key=lambda item: (-item[1], item[0] or ""))
    return result


TARGET_SUMMARY_SQL = text(
    """
    SELECT count(DISTINCT (s.source_vocabulary_id, s.source_code))              AS n_source_codes,
           array_agg(DISTINCT s.source_vocabulary_id ORDER BY s.source_vocabulary_id) AS source_vocabularies
      FROM mapping.source_to_concept_map s
     WHERE s.release_id = :release_id
       AND s.target_concept_id = :concept_id
    """
)


def target_summary(session: Session, release_id: int, concept_id: int) -> RowMapping:
    """Codes source qui pointent vers un concept cible dans la release (recherche inverse)."""
    params = {"release_id": release_id, "concept_id": concept_id}
    return session.execute(TARGET_SUMMARY_SQL, params).mappings().one()


# ---------------------------------------------------------------------------
# Fiche d'un code source
# ---------------------------------------------------------------------------

DETAIL_ROWS_SQL = text(
    """
    SELECT s.*,
           q.quality_flag,
           c.concept_name,
           c.domain_id         AS target_domain_id,
           c.vocabulary_id     AS concept_vocabulary_id,
           c.concept_class_id,
           c.standard_concept,
           c.concept_code,
           c.invalid_reason    AS concept_invalid_reason,
           c.valid_end_date    AS concept_valid_end_date
      FROM mapping.source_to_concept_map s
      JOIN mapping.v_mapping_quality q ON q.stcm_id = s.stcm_id
      LEFT JOIN vocab.concept c ON c.concept_id = s.target_concept_id
     WHERE s.release_id = :release_id
       AND s.source_vocabulary_id = :source_vocabulary_id
       AND s.source_code = :source_code
     ORDER BY s.relationship_id, s.target_concept_id
    """
)

HISTORY_SQL = text(
    """
    SELECT r.release_id,
           r.label,
           r.status,
           r.created_at,
           s.target_concept_id,
           s.relationship_id,
           s.target_vocabulary_id,
           s.source_code_description,
           s.domain_id,
           s.mapping_status,
           s.equivalence,
           s.mapping_comment,
           s.invalid_reason,
           c.concept_name
      FROM mapping.release r
      LEFT JOIN mapping.source_to_concept_map s
             ON s.release_id = r.release_id
            AND s.source_vocabulary_id = :source_vocabulary_id
            AND s.source_code = :source_code
      LEFT JOIN vocab.concept c ON c.concept_id = s.target_concept_id
     ORDER BY r.created_at, r.release_id, s.relationship_id, s.target_concept_id
    """
)


def detail_rows(
    session: Session, release_id: int, source_vocabulary_id: str, source_code: str
) -> Sequence[RowMapping]:
    params = {
        "release_id": release_id,
        "source_vocabulary_id": source_vocabulary_id,
        "source_code": source_code,
    }
    return session.execute(DETAIL_ROWS_SQL, params).mappings().all()


def history_rows(session: Session, source_vocabulary_id: str, source_code: str) -> Sequence[RowMapping]:
    params = {"source_vocabulary_id": source_vocabulary_id, "source_code": source_code}
    return session.execute(HISTORY_SQL, params).mappings().all()


# ---------------------------------------------------------------------------
# Édition d'un mapping
# ---------------------------------------------------------------------------


def get_mapping(session: Session, stcm_id: int) -> SourceToConceptMap | None:
    return session.get(SourceToConceptMap, stcm_id)


def key_exists(
    session: Session,
    release_id: int,
    source_vocabulary_id: str,
    source_code: str,
    target_concept_id: int,
    relationship_id: str,
    exclude_stcm_id: int,
) -> bool:
    stmt = select(func.count()).where(
        stcm.c.release_id == release_id,
        stcm.c.source_vocabulary_id == source_vocabulary_id,
        stcm.c.source_code == source_code,
        stcm.c.target_concept_id == target_concept_id,
        stcm.c.relationship_id == relationship_id,
        stcm.c.stcm_id != exclude_stcm_id,
    )
    return bool(session.execute(stmt).scalar_one())


# ---------------------------------------------------------------------------
# Chiffres clés et qualité
# ---------------------------------------------------------------------------

KEY_FIGURES_SQL = text(
    """
    SELECT count(*)                                              AS n_mappings,
           count(DISTINCT (s.source_vocabulary_id, s.source_code)) AS n_source_codes,
           count(DISTINCT s.source_vocabulary_id)                AS n_source_vocabularies,
           count(*) FILTER (WHERE s.mapping_status = 'APPROVED') AS n_approved,
           count(*) FILTER (WHERE s.mapping_status = 'UNCHECKED') AS n_unchecked,
           count(*) FILTER (WHERE s.mapping_status = 'FLAGGED')  AS n_flagged,
           count(*) FILTER (WHERE q.quality_flag = 'OK')         AS n_quality_ok
      FROM mapping.source_to_concept_map s
      JOIN mapping.v_mapping_quality q ON q.stcm_id = s.stcm_id
     WHERE s.release_id = :release_id
    """
)

QUALITY_SUMMARY_SQL = text(
    """
    SELECT q.source_vocabulary_id,
           q.quality_flag,
           count(*) AS n
      FROM mapping.v_mapping_quality q
     WHERE q.release_id = :release_id
     GROUP BY q.source_vocabulary_id, q.quality_flag
     ORDER BY q.source_vocabulary_id, q.quality_flag
    """
)


def key_figures(session: Session, release_id: int) -> RowMapping:
    return session.execute(KEY_FIGURES_SQL, {"release_id": release_id}).mappings().one()


def quality_summary(session: Session, release_id: int) -> Sequence[RowMapping]:
    return session.execute(QUALITY_SUMMARY_SQL, {"release_id": release_id}).mappings().all()


# ---------------------------------------------------------------------------
# Export CDM strict d'une release (pipeline)
# ---------------------------------------------------------------------------

CDM_COLUMNS = (
    "source_code",
    "source_concept_id",
    "source_vocabulary_id",
    "source_code_description",
    "target_concept_id",
    "target_vocabulary_id",
    "valid_start_date",
    "valid_end_date",
    "invalid_reason",
)

CDM_EXPORT_SQL = text(
    """
    SELECT v.source_code,
           v.source_concept_id,
           v.source_vocabulary_id,
           v.source_code_description,
           v.target_concept_id,
           v.target_vocabulary_id,
           v.valid_start_date,
           v.valid_end_date,
           v.invalid_reason
      FROM mapping.v_stcm_cdm v
     WHERE v.release_label = :label
     ORDER BY v.source_vocabulary_id, v.source_code, v.target_concept_id
    """
).execution_options(yield_per=10000)


def iter_cdm_export(session: Session, label: str) -> Iterator[RowMapping]:
    yield from session.execute(CDM_EXPORT_SQL, {"label": label}).mappings()
