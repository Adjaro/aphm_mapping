"""Modèles mappés sur mapping.athena_connection et mapping.athena_vocabulary_map."""

from datetime import datetime

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AthenaConnection(Base):
    __tablename__ = "athena_connection"
    __table_args__ = {"schema": "mapping"}

    athena_connection_id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(Text)
    host: Mapped[str] = mapped_column(Text)
    port: Mapped[int] = mapped_column(default=5432)
    database_name: Mapped[str] = mapped_column(Text)
    username: Mapped[str] = mapped_column(Text)
    password: Mapped[str | None] = mapped_column(Text)
    schema_name: Mapped[str] = mapped_column(Text, default="public")
    is_active: Mapped[bool] = mapped_column(default=False)
    athena_vocabulary_version: Mapped[str | None] = mapped_column(Text)
    last_sync_at: Mapped[datetime | None]
    last_sync_status: Mapped[str | None] = mapped_column(Text)
    last_sync_message: Mapped[str | None] = mapped_column(Text)
    last_sync_rows: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(server_default="now()")


class AthenaVocabularyMap(Base):
    __tablename__ = "athena_vocabulary_map"
    __table_args__ = {"schema": "mapping"}

    source_vocabulary_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    athena_vocabulary_id: Mapped[str] = mapped_column(String(20))
    ignore_dots: Mapped[bool] = mapped_column(default=True)
    ignore_case: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default="now()")
