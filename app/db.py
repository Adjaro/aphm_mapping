"""Moteur SQLAlchemy, fabrique de sessions et dépendance FastAPI."""

from collections.abc import Iterator

import psycopg
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """Base des modèles mappés sur les tables EXISTANTES (jamais de create_all)."""


def build_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, future=True)


engine: Engine = build_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_session() -> Iterator[Session]:
    """Dépendance FastAPI : une session par requête ; les transactions sont ouvertes par les services."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def libpq_dsn(url: str) -> str:
    """Convertit une URL SQLAlchemy en DSN libpq (scripts utilisant psycopg directement)."""
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def raw_connection(session: Session) -> psycopg.Connection:
    """Connexion psycopg sous-jacente à la transaction courante de la session (pour COPY)."""
    dbapi_conn = session.connection().connection.driver_connection
    assert isinstance(dbapi_conn, psycopg.Connection)
    return dbapi_conn
