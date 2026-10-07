"""Modèles mappés sur mapping.import_batch et mapping.import_error."""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ImportBatch(Base):
    __tablename__ = "import_batch"
    __table_args__ = {"schema": "mapping"}

    import_batch_id: Mapped[int] = mapped_column(primary_key=True)
    release_id: Mapped[int] = mapped_column(ForeignKey("mapping.release.release_id"))
    file_name: Mapped[str] = mapped_column(Text)
    file_sha256: Mapped[str | None] = mapped_column(Text)
    sheet_name: Mapped[str | None] = mapped_column(Text)
    domain_id: Mapped[str | None] = mapped_column(String(20))
    source_vocabulary_id: Mapped[str | None] = mapped_column(String(20))
    column_mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    default_values: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    load_mode: Mapped[str] = mapped_column(Text, default="upsert")
    rows_read: Mapped[int] = mapped_column(default=0)
    rows_loaded: Mapped[int] = mapped_column(default=0)
    rows_rejected: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(Text, default="pending")
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default="now()")
    loaded_at: Mapped[datetime | None]


class ImportErrorRow(Base):
    """Ligne rejetée (table mapping.import_error ; nom de classe évitant le builtin ImportError)."""

    __tablename__ = "import_error"
    __table_args__ = {"schema": "mapping"}

    import_error_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    import_batch_id: Mapped[int] = mapped_column(ForeignKey("mapping.import_batch.import_batch_id"))
    row_number: Mapped[int | None]
    raw_row: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_message: Mapped[str] = mapped_column(Text)
