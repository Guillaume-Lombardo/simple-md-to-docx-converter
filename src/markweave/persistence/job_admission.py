"""Shared atomic admission primitives for forward and reverse queues."""

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session as DatabaseSession

from markweave.persistence.schema import ConversionJobRow, ReversionJobRow

GLOBAL_ADMISSION_LOCK = 1_830_285_106
REVERSE_CLAIM_LOCK = 1_830_285_107
ACTIVE_JOB_STATES = ("queued", "running")


def lock_global_admission(database: DatabaseSession, dialect_name: str) -> None:
    """Serialize coupled capacity reads in PostgreSQL.

    SQLite callers acquire ``BEGIN IMMEDIATE`` before invoking this helper.
    """

    if dialect_name == "postgresql":
        database.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": GLOBAL_ADMISSION_LOCK},
        )


def global_active_job_count(database: DatabaseSession) -> int:
    """Count active jobs across both durable job families."""

    forward = database.scalar(
        select(func.count())
        .select_from(ConversionJobRow)
        .where(ConversionJobRow.state.in_(ACTIVE_JOB_STATES))
    )
    reverse = database.scalar(
        select(func.count())
        .select_from(ReversionJobRow)
        .where(ReversionJobRow.state.in_(ACTIVE_JOB_STATES))
    )
    return int(forward or 0) + int(reverse or 0)


def lock_reverse_claim(database: DatabaseSession, dialect_name: str) -> None:
    """Serialize the global reverse-running limit across PostgreSQL principals."""

    if dialect_name == "postgresql":
        database.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": REVERSE_CLAIM_LOCK},
        )
