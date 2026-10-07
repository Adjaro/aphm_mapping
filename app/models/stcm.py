"""Modèle mappé sur mapping.source_to_concept_map."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Computed, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SourceToConceptMap(Base):
    __tablename__ = "source_to_concept_map"
    __table_args__ = {"schema": "mapping"}

    stcm_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    release_id: Mapped[int] = mapped_column(ForeignKey("mapping.release.release_id"))
    source_code: Mapped[str] = mapped_column(String(50))
    source_concept_id: Mapped[int]
    source_vocabulary_id: Mapped[str] = mapped_column(String(20))
    source_code_description: Mapped[str | None] = mapped_column(String(255))
    target_concept_id: Mapped[int]
    target_vocabulary_id: Mapped[str] = mapped_column(String(20))
    valid_start_date: Mapped[date]
    valid_end_date: Mapped[date]
    invalid_reason: Mapped[str | None] = mapped_column(String(1))
    domain_id: Mapped[str | None] = mapped_column(String(20))
    relationship_id: Mapped[str] = mapped_column(String(20))
    source_frequency: Mapped[int | None]
    mapping_status: Mapped[str] = mapped_column(Text)
    equivalence: Mapped[str | None] = mapped_column(Text)
    mapping_comment: Mapped[str | None] = mapped_column(Text)
    mapped_by: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None]
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("mapping.import_batch.import_batch_id"))
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    # Colonne générée par PostgreSQL (migration 11) : code + description, minuscules sans accents
    search_text: Mapped[str | None] = mapped_column(
        Text,
        Computed("mapping.normalize_text(source_code || ' ' || coalesce(source_code_description, ''))"),
    )
