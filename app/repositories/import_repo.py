"""Imports : lots, table temporaire tmp_import (COPY), contrôles SQL et chargement.

Toutes les requêtes sont statiques ; les noms de colonnes personnalisées sont passés
en paramètres liés (clés jsonb), jamais concaténés.
"""

from collections.abc import Iterable, Iterator, Sequence
from typing import Any

from sqlalchemy import RowMapping, func, select, text
from sqlalchemy.orm import Session

from app.db import raw_connection
from app.models import ImportBatch, ImportErrorRow
from app.schemas.imports import EQUIVALENCES, MAPPING_STATUSES, RELATIONSHIPS

# Ordre des colonnes de tmp_import alimentées par COPY (après row_number et raw_row)
TMP_TEXT_COLUMNS = (
    "source_code",
    "source_concept_id",
    "source_vocabulary_id",
    "source_code_description",
    "target_concept_id",
    "target_vocabulary_id",
    "valid_start_date",
    "valid_end_date",
    "invalid_reason",
    "domain_id",
    "relationship_id",
    "source_frequency",
    "mapping_status",
    "equivalence",
    "mapping_comment",
    "mapped_by",
    "reviewed_by",
)

CREATE_TMP_IMPORT = """
CREATE TEMP TABLE tmp_import (
    row_number               int PRIMARY KEY,
    raw_row                  jsonb,
    source_code              text,
    source_concept_id        text,
    source_vocabulary_id     text,
    source_code_description  text,
    target_concept_id        text,
    target_vocabulary_id     text,
    valid_start_date         text,
    valid_end_date           text,
    invalid_reason           text,
    domain_id                text,
    relationship_id          text,
    source_frequency         text,
    mapping_status           text,
    equivalence              text,
    mapping_comment          text,
    mapped_by                text,
    reviewed_by              text,
    extra_raw                jsonb NOT NULL DEFAULT '{}',
    error                    text
) ON COMMIT DROP
"""

COPY_TMP_IMPORT = """
COPY tmp_import (
    row_number, raw_row, source_code, source_concept_id, source_vocabulary_id, source_code_description,
    target_concept_id, target_vocabulary_id, valid_start_date, valid_end_date, invalid_reason, domain_id,
    relationship_id, source_frequency, mapping_status, equivalence, mapping_comment, mapped_by,
    reviewed_by, extra_raw
) FROM STDIN
"""

NORMALIZE_SQL = text(
    """
    UPDATE tmp_import
       SET mapping_status  = upper(mapping_status),
           equivalence     = upper(equivalence),
           invalid_reason  = upper(invalid_reason),
           relationship_id = CASE upper(replace(relationship_id, '_', ' '))
                                 WHEN 'MAPS TO'       THEN 'Maps to'
                                 WHEN 'MAPS TO VALUE' THEN 'Maps to value'
                                 WHEN 'MAPS TO UNIT'  THEN 'Maps to unit'
                                 ELSE relationship_id
                             END
    """
)

# Contrôles de format sur tmp_import : (condition SQL statique, message affiché)
FORMAT_CHECKS: tuple[tuple[str, str], ...] = (
    ("source_code IS NULL", "source_code obligatoire"),
    ("source_vocabulary_id IS NULL", "source_vocabulary_id obligatoire"),
    ("target_concept_id IS NULL", "target_concept_id obligatoire"),
    ("char_length(source_code) > 50", "source_code dépasse 50 caractères"),
    ("char_length(source_vocabulary_id) > 20", "source_vocabulary_id dépasse 20 caractères"),
    ("char_length(source_code_description) > 255", "source_code_description dépasse 255 caractères"),
    ("char_length(target_vocabulary_id) > 20", "target_vocabulary_id dépasse 20 caractères"),
    ("char_length(domain_id) > 20", "domain_id dépasse 20 caractères"),
    ("char_length(relationship_id) > 20", "relationship_id dépasse 20 caractères"),
    (
        "target_concept_id IS NOT NULL AND mapping.try_cast_int(target_concept_id) IS NULL",
        "target_concept_id n'est pas un entier",
    ),
    (
        "source_concept_id IS NOT NULL AND mapping.try_cast_int(source_concept_id) IS NULL",
        "source_concept_id n'est pas un entier",
    ),
    (
        "source_frequency IS NOT NULL AND mapping.try_cast_int(source_frequency) IS NULL",
        "source_frequency n'est pas un entier",
    ),
    (
        "valid_start_date IS NOT NULL AND mapping.try_cast_date(valid_start_date) IS NULL",
        "valid_start_date n'est pas une date valide (AAAA-MM-JJ, AAAAMMJJ ou JJ/MM/AAAA)",
    ),
    (
        "valid_end_date IS NOT NULL AND mapping.try_cast_date(valid_end_date) IS NULL",
        "valid_end_date n'est pas une date valide (AAAA-MM-JJ, AAAAMMJJ ou JJ/MM/AAAA)",
    ),
    (
        "mapping_status IS NOT NULL AND mapping_status <> ALL(CAST(:mapping_statuses AS text[]))",
        "mapping_status non autorisé",
    ),
    (
        "equivalence IS NOT NULL AND equivalence <> ALL(CAST(:equivalences AS text[]))",
        "equivalence non autorisée",
    ),
    (
        "relationship_id IS NOT NULL AND relationship_id <> ALL(CAST(:relationships AS text[]))",
        "relationship_id non autorisé",
    ),
    ("invalid_reason IS NOT NULL AND invalid_reason NOT IN ('D', 'R', 'U')", "invalid_reason non autorisé"),
    (
        "CAST(:replace_check AS boolean)"
        " AND source_vocabulary_id IS DISTINCT FROM CAST(:replace_vocabulary AS text)",
        "source_vocabulary_id différent du vocabulaire remplacé",
    ),
)

FORMAT_CHECK_SQL = tuple(
    (
        text(
            """
            UPDATE tmp_import
               SET error = concat_ws(' ; ', error, CAST(:message AS text))
             WHERE {condition}
            """.replace("{condition}", condition)
        ),
        message,
    )
    for condition, message in FORMAT_CHECKS
)

CUSTOM_REQUIRED_SQL = text(
    """
    UPDATE tmp_import
       SET error = concat_ws(' ; ', error, CAST(:message AS text))
     WHERE (extra_raw ->> CAST(:name AS text)) IS NULL
    """
)

CUSTOM_ALLOWED_SQL = text(
    """
    UPDATE tmp_import
       SET error = concat_ws(' ; ', error, CAST(:message AS text))
     WHERE (extra_raw ->> CAST(:name AS text)) IS NOT NULL
       AND (extra_raw ->> CAST(:name AS text)) <> ALL(CAST(:allowed AS text[]))
    """
)

# Contrôle de type d'une colonne personnalisée : data_type -> requête statique
CUSTOM_TYPE_SQL: dict[str, Any] = {
    data_type: text(
        """
        UPDATE tmp_import
           SET error = concat_ws(' ; ', error, CAST(:message AS text))
         WHERE (extra_raw ->> CAST(:name AS text)) IS NOT NULL
           AND {cast_fn}(extra_raw ->> CAST(:name AS text)) IS NULL
        """.replace("{cast_fn}", cast_fn)
    )
    for data_type, cast_fn in (
        ("integer", "mapping.try_cast_int"),
        ("numeric", "mapping.try_cast_numeric"),
        ("date", "mapping.try_cast_date"),
        ("boolean", "mapping.try_cast_boolean"),
    )
}

# Conversion typée d'une colonne personnalisée dans tmp_valid.extra
CUSTOM_BUILD_SQL: dict[str, Any] = {
    data_type: text(
        """
        UPDATE tmp_valid v
           SET extra = v.extra || jsonb_build_object(CAST(:name AS text), {expr})
          FROM tmp_import t
         WHERE t.row_number = v.row_number
           AND (t.extra_raw ->> CAST(:name AS text)) IS NOT NULL
        """.replace("{expr}", expr)
    )
    for data_type, expr in (
        ("text", "t.extra_raw ->> CAST(:name AS text)"),
        ("integer", "mapping.try_cast_int(t.extra_raw ->> CAST(:name AS text))"),
        ("numeric", "mapping.try_cast_numeric(t.extra_raw ->> CAST(:name AS text))"),
        ("date", "mapping.try_cast_date(t.extra_raw ->> CAST(:name AS text))"),
        ("boolean", "mapping.try_cast_boolean(t.extra_raw ->> CAST(:name AS text))"),
    )
}

CREATE_TMP_VALID = text(
    """
    CREATE TEMP TABLE tmp_valid ON COMMIT DROP AS
    SELECT t.row_number,
           t.source_code,
           coalesce(mapping.try_cast_int(t.source_concept_id), 0)           AS source_concept_id,
           t.source_vocabulary_id,
           t.source_code_description,
           mapping.try_cast_int(t.target_concept_id)                        AS target_concept_id,
           t.target_vocabulary_id,
           coalesce(mapping.try_cast_date(t.valid_start_date), DATE '1970-01-01') AS valid_start_date,
           coalesce(mapping.try_cast_date(t.valid_end_date), DATE '2099-12-31')   AS valid_end_date,
           t.invalid_reason,
           t.domain_id,
           coalesce(t.relationship_id, 'Maps to')                           AS relationship_id,
           mapping.try_cast_int(t.source_frequency)                         AS source_frequency,
           coalesce(t.mapping_status, 'UNCHECKED')                          AS mapping_status,
           t.equivalence,
           t.mapping_comment,
           t.mapped_by,
           t.reviewed_by,
           CAST('{}' AS jsonb)                                              AS extra,
           CAST(NULL AS text)                                               AS error
      FROM tmp_import t
     WHERE t.error IS NULL
    """
)

FILL_TARGET_VOCABULARY_SQL = text(
    """
    UPDATE tmp_valid v
       SET target_vocabulary_id = CASE WHEN v.target_concept_id = 0 THEN 'None' ELSE c.vocabulary_id END
      FROM (SELECT DISTINCT target_concept_id FROM tmp_valid WHERE target_vocabulary_id IS NULL) k
      LEFT JOIN vocab.concept c ON c.concept_id = k.target_concept_id
     WHERE v.target_vocabulary_id IS NULL
       AND v.target_concept_id = k.target_concept_id
    """
)

# Contrôles sémantiques sur tmp_valid : (requête, message)
_SEMANTIC_CHECKS: tuple[tuple[str, str], ...] = (
    (
        """
        UPDATE tmp_valid v
           SET error = concat_ws(' ; ', v.error, CAST(:message AS text))
         WHERE v.target_concept_id <> 0
           AND NOT EXISTS (SELECT 1 FROM vocab.concept c WHERE c.concept_id = v.target_concept_id)
        """,
        "target_concept_id absent de vocab.concept",
    ),
    (
        """
        UPDATE tmp_valid v
           SET error = concat_ws(' ; ', v.error, CAST(:message AS text))
          FROM vocab.concept c
         WHERE c.concept_id = v.target_concept_id
           AND v.target_concept_id <> 0
           AND v.target_vocabulary_id IS DISTINCT FROM c.vocabulary_id
        """,
        "target_vocabulary_id incohérent avec le vocabulaire du concept cible",
    ),
    (
        """
        UPDATE tmp_valid v
           SET error = concat_ws(' ; ', v.error, CAST(:message AS text))
         WHERE v.target_vocabulary_id IS NULL
        """,
        "target_vocabulary_id obligatoire",
    ),
    (
        """
        UPDATE tmp_valid v
           SET error = concat_ws(' ; ', v.error, CAST(:message AS text))
          FROM (
                SELECT row_number,
                       row_number() OVER (
                           PARTITION BY source_vocabulary_id, source_code, target_concept_id, relationship_id
                           ORDER BY row_number
                       ) AS rank_in_file
                  FROM tmp_valid
               ) d
         WHERE d.row_number = v.row_number
           AND d.rank_in_file > 1
        """,
        "clé en double dans le fichier",
    ),
    (
        """
        UPDATE tmp_valid v
           SET error = concat_ws(' ; ', v.error, CAST(:message AS text))
         WHERE :load_mode = 'insert'
           AND EXISTS (
                SELECT 1
                  FROM mapping.source_to_concept_map s
                 WHERE s.release_id           = :release_id
                   AND s.source_vocabulary_id = v.source_vocabulary_id
                   AND s.source_code          = v.source_code
                   AND s.target_concept_id    = v.target_concept_id
                   AND s.relationship_id      = v.relationship_id)
        """,
        "clé déjà présente dans la release (mode insert)",
    ),
)

SEMANTIC_CHECK_SQL = tuple((text(sql), message) for sql, message in _SEMANTIC_CHECKS)

INSERT_ERRORS_SQL = text(
    """
    INSERT INTO mapping.import_error (import_batch_id, row_number, raw_row, error_message)
    SELECT :batch_id, t.row_number, t.raw_row, t.error
      FROM tmp_import t
     WHERE t.error IS NOT NULL
    UNION ALL
    SELECT :batch_id, t.row_number, t.raw_row, v.error
      FROM tmp_valid v
      JOIN tmp_import t ON t.row_number = v.row_number
     WHERE v.error IS NOT NULL
    """
)

DELETE_VOCABULARY_SQL = text(
    """
    DELETE FROM mapping.source_to_concept_map
     WHERE release_id = :release_id
       AND source_vocabulary_id = :source_vocabulary_id
    """
)

LOAD_SQL = text(
    """
    WITH loaded AS (
        INSERT INTO mapping.source_to_concept_map AS s (
            release_id, source_code, source_concept_id, source_vocabulary_id, source_code_description,
            target_concept_id, target_vocabulary_id, valid_start_date, valid_end_date, invalid_reason,
            domain_id, relationship_id, source_frequency, mapping_status, equivalence, mapping_comment,
            mapped_by, reviewed_by, import_batch_id, extra
        )
        SELECT :release_id, v.source_code, v.source_concept_id, v.source_vocabulary_id,
               v.source_code_description, v.target_concept_id, v.target_vocabulary_id, v.valid_start_date,
               v.valid_end_date, v.invalid_reason, v.domain_id, v.relationship_id, v.source_frequency,
               v.mapping_status, v.equivalence, v.mapping_comment, v.mapped_by, v.reviewed_by,
               :batch_id, v.extra
          FROM tmp_valid v
         WHERE v.error IS NULL
         ORDER BY v.row_number
        ON CONFLICT ON CONSTRAINT uq_stcm_release_key DO UPDATE
           SET source_concept_id       = CASE WHEN :u_source_concept_id THEN EXCLUDED.source_concept_id
                                              ELSE s.source_concept_id END,
               source_code_description = CASE WHEN :u_source_code_description
                                              THEN EXCLUDED.source_code_description
                                              ELSE s.source_code_description END,
               target_vocabulary_id    = EXCLUDED.target_vocabulary_id,
               valid_start_date        = CASE WHEN :u_valid_start_date THEN EXCLUDED.valid_start_date
                                              ELSE s.valid_start_date END,
               valid_end_date          = CASE WHEN :u_valid_end_date THEN EXCLUDED.valid_end_date
                                              ELSE s.valid_end_date END,
               invalid_reason          = CASE WHEN :u_invalid_reason THEN EXCLUDED.invalid_reason
                                              ELSE s.invalid_reason END,
               domain_id               = CASE WHEN :u_domain_id THEN EXCLUDED.domain_id
                                              ELSE s.domain_id END,
               source_frequency        = CASE WHEN :u_source_frequency THEN EXCLUDED.source_frequency
                                              ELSE s.source_frequency END,
               mapping_status          = CASE WHEN :u_mapping_status THEN EXCLUDED.mapping_status
                                              ELSE s.mapping_status END,
               equivalence             = CASE WHEN :u_equivalence THEN EXCLUDED.equivalence
                                              ELSE s.equivalence END,
               mapping_comment         = CASE WHEN :u_mapping_comment THEN EXCLUDED.mapping_comment
                                              ELSE s.mapping_comment END,
               mapped_by               = CASE WHEN :u_mapped_by THEN EXCLUDED.mapped_by
                                              ELSE s.mapped_by END,
               reviewed_by             = CASE WHEN :u_reviewed_by THEN EXCLUDED.reviewed_by
                                              ELSE s.reviewed_by END,
               extra                   = s.extra || EXCLUDED.extra,
               import_batch_id         = EXCLUDED.import_batch_id
         WHERE :load_mode = 'upsert'
           -- les lignes identiques ne sont pas réécrites (ni auditées)
           AND (EXCLUDED.target_vocabulary_id IS DISTINCT FROM s.target_vocabulary_id
                OR (s.extra || EXCLUDED.extra) IS DISTINCT FROM s.extra
                OR (:u_source_concept_id AND EXCLUDED.source_concept_id IS DISTINCT FROM s.source_concept_id)
                OR (:u_source_code_description
                    AND EXCLUDED.source_code_description IS DISTINCT FROM s.source_code_description)
                OR (:u_valid_start_date AND EXCLUDED.valid_start_date IS DISTINCT FROM s.valid_start_date)
                OR (:u_valid_end_date AND EXCLUDED.valid_end_date IS DISTINCT FROM s.valid_end_date)
                OR (:u_invalid_reason AND EXCLUDED.invalid_reason IS DISTINCT FROM s.invalid_reason)
                OR (:u_domain_id AND EXCLUDED.domain_id IS DISTINCT FROM s.domain_id)
                OR (:u_source_frequency AND EXCLUDED.source_frequency IS DISTINCT FROM s.source_frequency)
                OR (:u_mapping_status AND EXCLUDED.mapping_status IS DISTINCT FROM s.mapping_status)
                OR (:u_equivalence AND EXCLUDED.equivalence IS DISTINCT FROM s.equivalence)
                OR (:u_mapping_comment AND EXCLUDED.mapping_comment IS DISTINCT FROM s.mapping_comment)
                OR (:u_mapped_by AND EXCLUDED.mapped_by IS DISTINCT FROM s.mapped_by)
                OR (:u_reviewed_by AND EXCLUDED.reviewed_by IS DISTINCT FROM s.reviewed_by))
        RETURNING (xmax = 0) AS inserted
    )
    SELECT count(*) FILTER (WHERE inserted)     AS n_inserted,
           count(*) FILTER (WHERE NOT inserted) AS n_updated
      FROM loaded
    """
)

# Colonnes dont la mise à jour en mode upsert dépend du choix de l'utilisateur
UPSERT_FLAG_COLUMNS = (
    "source_concept_id",
    "source_code_description",
    "valid_start_date",
    "valid_end_date",
    "invalid_reason",
    "domain_id",
    "source_frequency",
    "mapping_status",
    "equivalence",
    "mapping_comment",
    "mapped_by",
    "reviewed_by",
)

COUNTS_SQL = text(
    """
    SELECT (SELECT count(*) FROM tmp_import)                                       AS n_read,
           (SELECT count(*) FROM tmp_import WHERE error IS NOT NULL)
         + (SELECT count(*) FROM tmp_valid WHERE error IS NOT NULL)                AS n_rejected,
           (SELECT count(*) FROM tmp_valid WHERE error IS NULL)                    AS n_valid
    """
)


# ---------------------------------------------------------------------------
# Lots d'import (ORM)
# ---------------------------------------------------------------------------


def add_batch(session: Session, batch: ImportBatch) -> ImportBatch:
    session.add(batch)
    session.flush()
    return batch


def get_batch(session: Session, batch_id: int) -> ImportBatch | None:
    return session.get(ImportBatch, batch_id)


def list_batches(session: Session, limit: int = 200) -> Sequence[RowMapping]:
    stmt = text(
        """
        SELECT b.import_batch_id,
               b.file_name,
               b.load_mode,
               b.status,
               b.rows_read,
               b.rows_loaded,
               b.rows_rejected,
               b.source_vocabulary_id,
               b.created_by,
               b.created_at,
               r.label AS release_label
          FROM mapping.import_batch b
          JOIN mapping.release r ON r.release_id = b.release_id
         ORDER BY b.created_at DESC, b.import_batch_id DESC
         LIMIT :limit
        """
    )
    return session.execute(stmt, {"limit": limit}).mappings().all()


def same_file_batches(session: Session, release_id: int, sha256: str, exclude_batch_id: int) -> Sequence[int]:
    stmt = select(ImportBatch.import_batch_id).where(
        ImportBatch.release_id == release_id,
        ImportBatch.file_sha256 == sha256,
        ImportBatch.import_batch_id != exclude_batch_id,
        ImportBatch.status.in_(("loaded", "partial")),
    )
    return session.scalars(stmt).all()


def last_batch_for_vocabulary(
    session: Session, source_vocabulary_id: str, exclude_batch_id: int
) -> ImportBatch | None:
    stmt = (
        select(ImportBatch)
        .where(
            ImportBatch.source_vocabulary_id == source_vocabulary_id,
            ImportBatch.import_batch_id != exclude_batch_id,
            ImportBatch.status.in_(("loaded", "partial")),
        )
        .order_by(ImportBatch.created_at.desc(), ImportBatch.import_batch_id.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()


def count_vocabulary_rows(session: Session, release_id: int, source_vocabulary_id: str) -> int:
    stmt = text(
        """
        SELECT count(*)
          FROM mapping.source_to_concept_map
         WHERE release_id = :release_id
           AND source_vocabulary_id = :source_vocabulary_id
        """
    )
    params = {"release_id": release_id, "source_vocabulary_id": source_vocabulary_id}
    return int(session.execute(stmt, params).scalar_one())


def list_errors(session: Session, batch_id: int, limit: int, offset: int) -> Sequence[ImportErrorRow]:
    stmt = (
        select(ImportErrorRow)
        .where(ImportErrorRow.import_batch_id == batch_id)
        .order_by(ImportErrorRow.row_number, ImportErrorRow.import_error_id)
        .limit(limit)
        .offset(offset)
    )
    return session.scalars(stmt).all()


def count_errors(session: Session, batch_id: int) -> int:
    stmt = select(func.count()).where(ImportErrorRow.import_batch_id == batch_id)
    return int(session.execute(stmt).scalar_one())


def iter_errors(session: Session, batch_id: int) -> Iterator[ImportErrorRow]:
    stmt = (
        select(ImportErrorRow)
        .where(ImportErrorRow.import_batch_id == batch_id)
        .order_by(ImportErrorRow.row_number, ImportErrorRow.import_error_id)
        .execution_options(yield_per=5000)
    )
    yield from session.scalars(stmt)


# ---------------------------------------------------------------------------
# Chargement (dans la transaction ouverte par le service)
# ---------------------------------------------------------------------------


def create_tmp_import(session: Session) -> None:
    session.execute(text(CREATE_TMP_IMPORT))


def copy_rows(session: Session, rows: Iterable[Sequence[Any]]) -> None:
    """COPY des lignes (row_number, raw_row json, colonnes TMP_TEXT_COLUMNS..., extra_raw json)."""
    conn = raw_connection(session)
    with conn.cursor() as cur, cur.copy(COPY_TMP_IMPORT) as copy:
        for row in rows:
            copy.write_row(row)


def run_format_checks(session: Session, load_mode: str, replace_vocabulary: str | None) -> None:
    session.execute(NORMALIZE_SQL)
    params: dict[str, Any] = {
        "mapping_statuses": list(MAPPING_STATUSES),
        "equivalences": list(EQUIVALENCES),
        "relationships": list(RELATIONSHIPS),
        "replace_vocabulary": replace_vocabulary,
        "replace_check": load_mode == "replace_vocabulary",
    }
    for statement, message in FORMAT_CHECK_SQL:
        session.execute(statement, {**params, "message": message})


def run_custom_checks(
    session: Session, name: str, data_type: str, required: bool, allowed: list[str] | None
) -> None:
    if required:
        session.execute(CUSTOM_REQUIRED_SQL, {"name": name, "message": f"{name} obligatoire"})
    if data_type in CUSTOM_TYPE_SQL:
        session.execute(
            CUSTOM_TYPE_SQL[data_type], {"name": name, "message": f"{name} : type {data_type} attendu"}
        )
    if allowed:
        session.execute(
            CUSTOM_ALLOWED_SQL,
            {"name": name, "allowed": allowed, "message": f"{name} : valeur non autorisée"},
        )


def build_valid_rows(session: Session, custom_types: dict[str, str]) -> None:
    session.execute(CREATE_TMP_VALID)
    for name, data_type in custom_types.items():
        session.execute(CUSTOM_BUILD_SQL.get(data_type, CUSTOM_BUILD_SQL["text"]), {"name": name})
    session.execute(FILL_TARGET_VOCABULARY_SQL)


def run_semantic_checks(session: Session, release_id: int, load_mode: str) -> None:
    for statement, message in SEMANTIC_CHECK_SQL:
        session.execute(statement, {"message": message, "release_id": release_id, "load_mode": load_mode})


def save_errors(session: Session, batch_id: int) -> None:
    session.execute(INSERT_ERRORS_SQL, {"batch_id": batch_id})


def delete_vocabulary(session: Session, release_id: int, source_vocabulary_id: str) -> int:
    params = {"release_id": release_id, "source_vocabulary_id": source_vocabulary_id}
    return session.execute(DELETE_VOCABULARY_SQL, params).rowcount  # type: ignore[attr-defined]


def load_valid_rows(
    session: Session, release_id: int, batch_id: int, load_mode: str, provided_columns: set[str]
) -> tuple[int, int]:
    params: dict[str, Any] = {"release_id": release_id, "batch_id": batch_id, "load_mode": load_mode}
    params.update({f"u_{name}": name in provided_columns for name in UPSERT_FLAG_COLUMNS})
    row = session.execute(LOAD_SQL, params).one()
    return int(row.n_inserted), int(row.n_updated)


def tmp_counts(session: Session) -> tuple[int, int, int]:
    """(lignes lues, lignes rejetées, lignes valides)."""
    row = session.execute(COUNTS_SQL).one()
    return int(row.n_read), int(row.n_rejected), int(row.n_valid)
