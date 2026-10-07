"""Modèle mappé sur mapping.release."""

from datetime import datetime

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Release(Base):
    __tablename__ = "release"
    __table_args__ = {"schema": "mapping"}

    release_id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    parent_release_id: Mapped[int | None] = mapped_column(ForeignKey("mapping.release.release_id"))
    vocabulary_version: Mapped[str | None] = mapped_column(Text)
    cdm_build_ref: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default="now()")
    frozen_at: Mapped[datetime | None]
    published_at: Mapped[datetime | None]

    @property
    def is_open(self) -> bool:
        return self.status == "open"
