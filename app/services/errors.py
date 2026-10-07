"""Erreurs métier affichées telles quelles à l'utilisateur."""

from sqlalchemy.exc import DBAPIError


class BusinessError(Exception):
    """Règle métier non respectée ; le message (en français) est destiné à l'utilisateur."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def database_message(exc: DBAPIError) -> str:
    """Message lisible d'une erreur PostgreSQL (RAISE EXCEPTION, contrainte…)."""
    orig = exc.orig
    diag = getattr(orig, "diag", None)
    primary = getattr(diag, "message_primary", None)
    return str(primary or orig or exc).strip()
