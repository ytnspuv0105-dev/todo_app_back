"""Lightweight schema fixes for local PostgreSQL (no Alembic)."""

from __future__ import annotations

from sqlalchemy import inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Task, Workspace


def ensure_task_numbers(engine: Engine) -> None:
    """Add tasks.number if missing, backfill per workspace by id, enforce uniqueness."""
    insp = inspect(engine)
    if "tasks" not in insp.get_table_names():
        return

    cols = {c["name"] for c in insp.get_columns("tasks")}
    with engine.begin() as conn:
        if "number" not in cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN number INTEGER"))

        # Unique per workspace (idempotent)
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_workspace_number "
                "ON tasks (workspace_id, number)"
            )
        )

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db: Session = SessionLocal()
    try:
        workspaces = db.execute(select(Workspace.id)).scalars().all()
        for ws_id in workspaces:
            tasks = (
                db.execute(
                    select(Task)
                    .where(Task.workspace_id == ws_id)
                    .order_by(Task.id.asc())
                )
                .scalars()
                .all()
            )
            # Assign missing numbers in stable id order without renumbering existing ones.
            used = {t.number for t in tasks if t.number is not None}
            next_n = 1
            for t in tasks:
                if t.number is not None:
                    continue
                while next_n in used:
                    next_n += 1
                t.number = next_n
                used.add(next_n)
                next_n += 1
        db.commit()
    finally:
        db.close()

    # Enforce NOT NULL after backfill
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE tasks ALTER COLUMN number SET NOT NULL"))


def ensure_task_comments_schema(engine: Engine) -> None:
    """Add task_comments.updated_at when the table predates author edits."""
    insp = inspect(engine)
    if "task_comments" not in insp.get_table_names():
        return

    cols = {c["name"] for c in insp.get_columns("task_comments")}
    if "updated_at" in cols:
        return

    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE task_comments "
                "ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE"
            )
        )
