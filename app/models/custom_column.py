"""Modèle mappé sur mapping.custom_column."""

from datetime import datetime

from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CustomColumn(Base):
    __tablename__ = "custom_column"
    __table_args__ = {"schema": "mapping"}

    column_name: Mapped[str] = mapped_column(Text, primary_key=True)
    label: Mapped[str] = mapped_column(Text)
    data_type: Mapped[str] = mapped_column(Text, default="text")
    allowed_values: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    is_required: Mapped[bool] = mapped_column(default=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default="now()")
