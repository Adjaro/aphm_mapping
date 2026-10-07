"""Journal d'audit et transmission de l'utilisateur courant à PostgreSQL."""

from collections.abc import Sequence

from sqlalchemy import RowMapping, text
from sqlalchemy.orm import Session

SET_USER_SQL = text("SELECT set_config('app.user', :user, true)")

CODE_AUDIT_SQL = text(
    """
    SELECT a.audit_id,
           a.operation,
           a.changed_by,
           a.changed_at,
           a.old_row,
           a.new_row,
           r.label AS release_label
      FROM mapping.audit_log a
      LEFT JOIN mapping.release r ON r.release_id = a.release_id
     WHERE a.old_row ->> 'source_vocabulary_id' = :source_vocabulary_id
       AND a.old_row ->> 'source_code' = :source_code
     ORDER BY a.changed_at DESC, a.audit_id DESC
     LIMIT :limit
    """
)


def set_current_user(session: Session, user: str) -> None:
    """Alimente audit_log.changed_by pour la transaction courante."""
    session.execute(SET_USER_SQL, {"user": user})


def code_audit(
    session: Session, source_vocabulary_id: str, source_code: str, limit: int = 500
) -> Sequence[RowMapping]:
    params = {"source_vocabulary_id": source_vocabulary_id, "source_code": source_code, "limit": limit}
    return session.execute(CODE_AUDIT_SQL, params).mappings().all()
