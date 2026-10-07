"""Assistant d'import CSV / Excel : upload, correspondance des colonnes, validation et chargement.

Le contrôle des lignes et le chargement se font en SQL (repository) dans une seule transaction ;
ce service lit le fichier par blocs, applique la correspondance des colonnes et orchestre.
"""

import hashlib
import json
import logging
import re
import unicodedata
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

import pandas as pd
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CustomColumn, ImportBatch, ImportErrorRow, Release
from app.repositories import audit_repo, custom_column_repo, import_repo, release_repo
from app.schemas.imports import (
    EQUIVALENCES,
    IMPORT_COLUMNS,
    MAPPING_STATUSES,
    RELATIONSHIPS,
    ColumnMappingIn,
    FilePreviewOut,
    ImportColumn,
    LoadResultOut,
)
from app.services import file_reader
from app.services.errors import BusinessError, database_message

logger = logging.getLogger(__name__)

ERRORS_PAGE_SIZE = 100
USAGI_CONCEPT_COLUMNS = {"valid_start_date", "valid_end_date", "invalid_reason"}
REQUIRED_COLUMNS = tuple(column.name for column in IMPORT_COLUMNS if column.required)
ALLOWED_DEFAULTS: dict[str, tuple[str, ...]] = {
    "mapping_status": MAPPING_STATUSES,
    "equivalence": EQUIVALENCES,
    "relationship_id": RELATIONSHIPS,
}


@dataclass
class WizardOut:
    batch: ImportBatch
    release: Release
    preview: FilePreviewOut
    import_columns: tuple[ImportColumn, ...]
    custom_columns: Sequence[CustomColumn]
    choices: dict[str, dict[str, str | None]]
    duplicate_batches: Sequence[int]
    prefilled_from: int | None
    errors: list[str] = field(default_factory=list)


@dataclass
class ValidationOut:
    batch: ImportBatch
    release: Release
    targets: list[tuple[str, str]]
    rows_to_delete: int
    replace_vocabulary: str | None


@dataclass
class ReportOut:
    batch: ImportBatch
    release: Release
    errors: Sequence[ImportErrorRow]
    errors_total: int
    page: int
    pages: int
    effects: dict[str, int] = field(default_factory=dict)


@dataclass
class RollbackOut:
    deleted: int
    restored: int
    reinserted: int


def _get_batch_for_display(session: Session, batch_id: int) -> tuple[ImportBatch, Release]:
    with session.begin():
        return _get_batch(session, batch_id)


def batch_state(batch: ImportBatch, release: Release) -> str:
    """État affiché : en cours (release ouverte), archivé (release publiée/archivée), annulé, en attente…"""
    if batch.status == "rolled_back":
        return "rolled_back"
    if batch.status in ("loaded", "partial") and release.status != "open":
        return "archived"
    return batch.status


def can_rollback(batch: ImportBatch, release: Release) -> bool:
    return batch.status in ("loaded", "partial") and release.status == "open" and batch.loaded_at is not None


def can_delete(batch: ImportBatch) -> bool:
    return batch.status in ("pending", "failed")


def rollback(session: Session, batch_id: int, user: str) -> RollbackOut:
    """Annule un import (fonction PostgreSQL rollback_import) : la release revient à son état d'avant."""
    try:
        with session.begin():
            batch, release = _get_batch(session, batch_id)
            if not can_rollback(batch, release):
                raise BusinessError(
                    f"Import n° {batch_id} non annulable : seul un import chargé dans une release ouverte "
                    "peut être annulé (après publication, il est archivé)."
                )
            audit_repo.set_current_user(session, user)
            deleted, restored, reinserted = import_repo.rollback_import(session, batch_id)
    except DBAPIError as exc:
        raise BusinessError(database_message(exc)) from exc
    logger.info("Import %s annulé par %s : %s supprimées, %s restaurées", batch_id, user, deleted, restored)
    return RollbackOut(deleted, restored, reinserted)


def delete_batch(session: Session, batch_id: int) -> str:
    """Supprime un import jamais chargé (en attente ou en échec) et son fichier."""
    with session.begin():
        batch, _ = _get_batch(session, batch_id)
        if not can_delete(batch):
            raise BusinessError(
                f"Import n° {batch_id} déjà chargé : utiliser « Annuler l'import » pour retirer ses lignes."
            )
        path = batch_file(batch)
        file_name = batch.file_name
        import_repo.delete_batch(session, batch)
    path.unlink(missing_ok=True)
    return file_name


# ---------------------------------------------------------------------------
# Étape 1 : upload
# ---------------------------------------------------------------------------


def target_release(session: Session) -> Release:
    """Release ouverte ciblée par un import (la staging en priorité)."""
    releases = release_repo.open_releases(session)
    if not releases:
        raise BusinessError("Aucune release ouverte : créer une staging avant d'importer.")
    return releases[0]


def open_release(session: Session) -> Release | None:
    with session.begin():
        releases = release_repo.open_releases(session)
    return releases[0] if releases else None


def batch_file(batch: ImportBatch) -> Path:
    return get_settings().upload_dir / f"batch_{batch.import_batch_id}{Path(batch.file_name).suffix.lower()}"


def create_batch(
    session: Session, stream: BinaryIO, file_name: str, source_vocabulary_id: str | None, user: str
) -> int:
    file_name = Path(file_name or "").name
    suffix = Path(file_name).suffix.lower()
    if suffix not in file_reader.SUPPORTED_EXTENSIONS:
        raise BusinessError("Format non pris en charge : fichiers .csv, .txt ou .xlsx uniquement.")
    upload_dir = get_settings().upload_dir
    upload_dir.mkdir(parents=True, exist_ok=True)
    temp_path = upload_dir / f"upload_{uuid.uuid4().hex}{suffix}"
    sha256 = _save_stream(stream, temp_path)
    try:
        with session.begin():
            release = target_release(session)
            batch = ImportBatch(
                release_id=release.release_id,
                file_name=file_name,
                file_sha256=sha256,
                source_vocabulary_id=(source_vocabulary_id or "").strip() or None,
                created_by=user,
                column_mapping={},
                default_values={},
                load_mode="upsert",
                status="pending",
            )
            if file_reader.is_excel(temp_path):
                sheets = file_reader.excel_sheets(temp_path)
                batch.sheet_name = sheets[0] if sheets else None
            import_repo.add_batch(session, batch)
            batch_id = batch.import_batch_id
            temp_path.replace(batch_file(batch))
    finally:
        temp_path.unlink(missing_ok=True)
    logger.info("Import %s créé (%s) par %s", batch_id, file_name, user)
    return batch_id


def _save_stream(stream: BinaryIO, path: Path) -> str:
    digest = hashlib.sha256()
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    size = 0
    with path.open("wb") as handle:
        while block := stream.read(1 << 20):
            size += len(block)
            if size > max_bytes:
                handle.close()
                path.unlink(missing_ok=True)
                raise BusinessError(f"Fichier trop volumineux (maximum {get_settings().max_upload_mb} Mo).")
            digest.update(block)
            handle.write(block)
    if size == 0:
        path.unlink(missing_ok=True)
        raise BusinessError("Le fichier est vide.")
    return digest.hexdigest()


def list_batches(session: Session) -> Sequence[Any]:
    with session.begin():
        return import_repo.list_batches(session)


def display_state(status: str, release_status: str) -> str:
    """Même règle que batch_state, à partir des colonnes de la liste des imports."""
    if status in ("loaded", "partial") and release_status != "open":
        return "archived"
    return status


# ---------------------------------------------------------------------------
# Étape 2 : correspondance des colonnes
# ---------------------------------------------------------------------------


def _get_batch(session: Session, batch_id: int) -> tuple[ImportBatch, Release]:
    batch = import_repo.get_batch(session, batch_id)
    if batch is None:
        raise BusinessError(f"Import n° {batch_id} introuvable.")
    release = release_repo.get_by_id(session, batch.release_id)
    assert release is not None
    return batch, release


def _require_pending(batch: ImportBatch, release: Release) -> None:
    if batch.status != "pending":
        raise BusinessError(f"L'import n° {batch.import_batch_id} est déjà traité (statut {batch.status}).")
    if release.status != "open":
        raise BusinessError(f"La release {release.label} n'est plus ouverte (statut {release.status}).")


def file_preview(batch: ImportBatch, sheet_name: str | None) -> FilePreviewOut:
    path = batch_file(batch)
    if not path.is_file():
        raise BusinessError("Le fichier de cet import n'est plus disponible sur le serveur.")
    if file_reader.is_excel(path):
        sheets = file_reader.excel_sheets(path)
        sheet = sheet_name if sheet_name in sheets else (batch.sheet_name or (sheets[0] if sheets else None))
        columns, rows = file_reader.preview(path, sheet)
        return FilePreviewOut(columns=columns, rows=rows, sheets=sheets, sheet_name=sheet)
    fmt = file_reader.detect_csv_format(path)
    columns, rows = file_reader.preview(path, None)
    separator = {"\t": "tabulation"}.get(fmt.separator, fmt.separator)
    return FilePreviewOut(
        columns=columns, rows=rows, encoding=fmt.encoding.replace("-sig", ""), separator=separator
    )


def normalize_name(name: str) -> str:
    """Nom de colonne comparable : minuscules, sans accents ni séparateurs."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", ascii_name.lower())


def suggest_choices(
    file_columns: list[str], targets: list[tuple[str, tuple[str, ...]]], previous: ImportBatch | None
) -> dict[str, dict[str, str | None]]:
    """Pré-remplissage : dernier import du même vocabulaire, puis correspondance par nom / alias."""
    by_name = {normalize_name(c): c for c in file_columns}
    # Export Usagi : valid_start_date / valid_end_date / invalid_reason y décrivent le CONCEPT cible,
    # pas le mapping ; ne pas les associer automatiquement.
    usagi = {"sourcecode", "conceptid"} <= set(by_name)
    choices: dict[str, dict[str, str | None]] = {}
    for target, aliases in targets:
        if usagi and target in USAGI_CONCEPT_COLUMNS and previous is None:
            choices[target] = {"file_column": None, "default_value": None}
            continue
        file_column: str | None = None
        default_value: str | None = None
        if previous is not None:
            candidate = previous.column_mapping.get(target)
            if candidate in file_columns:
                file_column = candidate
            default_value = previous.default_values.get(target)
        if file_column is None and default_value is None:
            for name in (target, *aliases):
                if normalize_name(name) in by_name:
                    file_column = by_name[normalize_name(name)]
                    break
        choices[target] = {"file_column": file_column, "default_value": default_value}
    return choices


def _all_targets(custom_columns: Sequence[CustomColumn]) -> list[tuple[str, tuple[str, ...]]]:
    targets = [(column.name, column.aliases) for column in IMPORT_COLUMNS]
    targets.extend((column.column_name, (column.label,)) for column in custom_columns)
    return targets


def wizard(session: Session, batch_id: int, sheet_name: str | None, user: str) -> WizardOut:
    with session.begin():
        batch, release = _get_batch(session, batch_id)
        custom_columns = custom_column_repo.list_columns(session)
        duplicates = import_repo.same_file_batches(
            session, release.release_id, batch.file_sha256 or "", batch_id
        )
        previous = None
        if batch.source_vocabulary_id:
            previous = import_repo.last_batch_for_vocabulary(session, batch.source_vocabulary_id, batch_id)
    preview = file_preview(batch, sheet_name)
    if batch.column_mapping or batch.default_values:
        choices = {
            target: {
                "file_column": batch.column_mapping.get(target),
                "default_value": batch.default_values.get(target),
            }
            for target, _ in _all_targets(custom_columns)
        }
    else:
        choices = suggest_choices(preview.columns, _all_targets(custom_columns), previous)
        if batch.source_vocabulary_id and not choices["source_vocabulary_id"]["file_column"]:
            choices["source_vocabulary_id"]["default_value"] = batch.source_vocabulary_id
        if not choices["mapped_by"]["file_column"] and not choices["mapped_by"]["default_value"]:
            choices["mapped_by"]["default_value"] = user
    return WizardOut(
        batch=batch,
        release=release,
        preview=preview,
        import_columns=IMPORT_COLUMNS,
        custom_columns=custom_columns,
        choices=choices,
        duplicate_batches=duplicates,
        prefilled_from=previous.import_batch_id if previous else None,
    )


def save_mapping(session: Session, batch_id: int, data: ColumnMappingIn) -> None:
    with session.begin():
        batch, release = _get_batch(session, batch_id)
        _require_pending(batch, release)
        custom_columns = custom_column_repo.list_columns(session)
        preview = file_preview(batch, data.sheet_name)
        column_mapping, default_values = _validate_choices(data, preview.columns, custom_columns)
        replace_vocabulary = default_values.get("source_vocabulary_id") or batch.source_vocabulary_id
        if data.load_mode == "replace_vocabulary" and (
            "source_vocabulary_id" in column_mapping and not batch.source_vocabulary_id
        ):
            raise BusinessError(
                "Le mode remplacement exige un vocabulaire source unique : "
                "saisir une valeur par défaut pour source_vocabulary_id."
            )
        batch.column_mapping = column_mapping
        batch.default_values = default_values
        batch.load_mode = data.load_mode
        batch.sheet_name = preview.sheet_name
        batch.source_vocabulary_id = replace_vocabulary
        batch.domain_id = default_values.get("domain_id")


def _validate_choices(
    data: ColumnMappingIn, file_columns: list[str], custom_columns: Sequence[CustomColumn]
) -> tuple[dict[str, str], dict[str, str]]:
    column_mapping: dict[str, str] = {}
    default_values: dict[str, str] = {}
    errors: list[str] = []
    required = set(REQUIRED_COLUMNS) | {c.column_name for c in custom_columns if c.is_required}
    for target, _ in _all_targets(custom_columns):
        choice = data.choices.get(target)
        file_column = (choice.file_column or "").strip() if choice else ""
        default_value = (choice.default_value or "").strip() if choice else ""
        if file_column:
            if file_column not in file_columns:
                errors.append(f"{target} : colonne « {file_column} » absente du fichier.")
            column_mapping[target] = file_column
        elif default_value:
            allowed = ALLOWED_DEFAULTS.get(target)
            if allowed and default_value not in allowed:
                errors.append(f"{target} : valeur par défaut non autorisée ({', '.join(allowed)}).")
            default_values[target] = default_value
        elif target in required:
            errors.append(
                f"{target} est obligatoire : choisir une colonne du fichier ou une valeur par défaut."
            )
    if errors:
        raise BusinessError(" ".join(errors))
    return column_mapping, default_values


# ---------------------------------------------------------------------------
# Étape 3 : validation et chargement
# ---------------------------------------------------------------------------


def validation_summary(session: Session, batch_id: int) -> ValidationOut:
    with session.begin():
        batch, release = _get_batch(session, batch_id)
        if not batch.column_mapping and not batch.default_values:
            raise BusinessError("Correspondance des colonnes non renseignée (étape 2).")
        replace_vocabulary = batch.source_vocabulary_id if batch.load_mode == "replace_vocabulary" else None
        rows_to_delete = (
            import_repo.count_vocabulary_rows(session, release.release_id, replace_vocabulary)
            if replace_vocabulary
            else 0
        )
    targets = [(target, f"colonne « {col} »") for target, col in batch.column_mapping.items()]
    targets += [(target, f"valeur par défaut « {value} »") for target, value in batch.default_values.items()]
    return ValidationOut(batch, release, targets, rows_to_delete, replace_vocabulary)


def run_import(session: Session, batch_id: int, user: str) -> LoadResultOut:
    """Contrôle et charge le fichier dans une seule transaction ; en cas d'échec le lot passe en failed."""
    try:
        with session.begin():
            batch, release = _get_batch(session, batch_id)
            _require_pending(batch, release)
            custom_columns = list(custom_column_repo.list_columns(session))
            audit_repo.set_current_user(session, user)
            result = _load(session, batch, release, custom_columns)
            import_repo.mark_loaded(session, batch.import_batch_id)
            batch.rows_read = result.rows_read
            batch.rows_loaded = result.rows_inserted + result.rows_updated + result.rows_unchanged
            batch.rows_rejected = result.rows_rejected
            batch.status = result.status
    except DBAPIError as exc:
        _mark_failed(session, batch_id)
        raise BusinessError(f"Échec du chargement : {database_message(exc)}") from exc
    except BusinessError:
        _mark_failed(session, batch_id)
        raise
    logger.info(
        "Import %s : %s lues, %s insérées, %s mises à jour, %s inchangées, %s rejetées",
        batch_id,
        result.rows_read,
        result.rows_inserted,
        result.rows_updated,
        result.rows_unchanged,
        result.rows_rejected,
    )
    return result


def _mark_failed(session: Session, batch_id: int) -> None:
    with session.begin():
        batch = import_repo.get_batch(session, batch_id)
        if batch is not None and batch.status == "pending":
            batch.status = "failed"


def _load(
    session: Session, batch: ImportBatch, release: Release, custom_columns: list[CustomColumn]
) -> LoadResultOut:
    replace_vocabulary = batch.source_vocabulary_id if batch.load_mode == "replace_vocabulary" else None
    import_repo.create_tmp_import(session)
    import_repo.copy_rows(session, _iter_copy_rows(batch, custom_columns))
    import_repo.run_format_checks(session, batch.load_mode, replace_vocabulary)
    for column in custom_columns:
        import_repo.run_custom_checks(
            session, column.column_name, column.data_type, column.is_required, column.allowed_values
        )
    import_repo.build_valid_rows(session, {c.column_name: c.data_type for c in custom_columns})
    import_repo.run_semantic_checks(session, release.release_id, batch.load_mode)
    import_repo.save_errors(session, batch.import_batch_id)
    deleted = 0
    if replace_vocabulary:
        deleted = import_repo.delete_vocabulary(session, release.release_id, replace_vocabulary)
    provided = set(batch.column_mapping) | set(batch.default_values)
    inserted, updated = import_repo.load_valid_rows(
        session, release.release_id, batch.import_batch_id, batch.load_mode, provided
    )
    rows_read, rejected, valid = import_repo.tmp_counts(session)
    unchanged = valid - inserted - updated
    loaded = inserted + updated + unchanged
    if rejected == 0:
        status = "loaded"
    elif loaded > 0:
        status = "partial"
    else:
        status = "failed"
    return LoadResultOut(rows_read, inserted, updated, unchanged, deleted, rejected, status)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


def _iter_copy_rows(batch: ImportBatch, custom_columns: list[CustomColumn]) -> Iterator[list[Any]]:
    """Lignes pour COPY : row_number, raw_row, colonnes de tmp_import, extra_raw."""
    path = batch_file(batch)
    custom_names = [c.column_name for c in custom_columns]
    line = 1  # ligne d'en-tête
    for chunk in file_reader.iter_chunks(path, batch.sheet_name, get_settings().import_chunk_size):
        columns = list(chunk.columns)
        sources = [_column_values(chunk, batch, name) for name in import_repo.TMP_TEXT_COLUMNS]
        custom_sources = [_column_values(chunk, batch, name) for name in custom_names]
        raw_values = [chunk[c].tolist() for c in columns]
        for index in range(len(chunk)):
            line += 1
            raw = json.dumps({c: raw_values[i][index] for i, c in enumerate(columns)}, ensure_ascii=False)
            values = [_clean(source[index]) for source in sources]
            extra = {
                name: v
                for name, src in zip(custom_names, custom_sources, strict=True)
                if (v := _clean(src[index]))
            }
            yield [line, raw, *values, json.dumps(extra, ensure_ascii=False)]


class _Constant:
    """Valeur par défaut appliquée à toute la colonne (indexable comme une liste)."""

    def __init__(self, value: str | None) -> None:
        self.value = value

    def __getitem__(self, index: int) -> str | None:
        return self.value


def _column_values(chunk: pd.DataFrame, batch: ImportBatch, target: str) -> Any:
    file_column = batch.column_mapping.get(target)
    if file_column:
        if file_column not in chunk.columns:
            raise BusinessError(f"Colonne « {file_column} » absente du fichier.")
        return chunk[file_column].tolist()
    return _Constant(batch.default_values.get(target))


# ---------------------------------------------------------------------------
# Étape 4 : rapport
# ---------------------------------------------------------------------------


def report(session: Session, batch_id: int, page: int) -> ReportOut:
    with session.begin():
        batch, release = _get_batch(session, batch_id)
        total = import_repo.count_errors(session, batch_id)
        pages = max(1, -(-total // ERRORS_PAGE_SIZE))
        page = min(max(page, 1), pages)
        errors = import_repo.list_errors(session, batch_id, ERRORS_PAGE_SIZE, (page - 1) * ERRORS_PAGE_SIZE)
        effects = import_repo.import_effects(session, batch_id) if can_rollback(batch, release) else {}
    return ReportOut(batch, release, errors, total, page, pages, effects)


def import_local_file(
    session: Session,
    path: Path,
    column_mapping: dict[str, str],
    default_values: dict[str, str],
    load_mode: str,
    user: str,
) -> LoadResultOut:
    """Import non interactif (scripts de chargement initial) via le même circuit que l'assistant."""
    with path.open("rb") as stream:
        batch_id = create_batch(session, stream, path.name, default_values.get("source_vocabulary_id"), user)
    choices = {k: {"file_column": v} for k, v in column_mapping.items()}
    choices.update({k: {"default_value": v} for k, v in default_values.items()})
    save_mapping(
        session, batch_id, ColumnMappingIn.model_validate({"load_mode": load_mode, "choices": choices})
    )
    return run_import(session, batch_id, user)
