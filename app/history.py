"""Helpers for workspace task change history (dashboard 最近のタスク)."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from .models import Task, TaskChangeHistory, User

ISSUE_TYPE_LABELS = {
    "task": "タスク",
    "bug": "バグ",
    "request": "要望",
    "other": "その他",
}
STATUS_LABELS = {
    "open": "未対応",
    "in_progress": "処理中",
    "resolved": "処理済み",
    "closed": "完了",
}
PRIORITY_LABELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
}

EMPTY = "（空）"


def _display(value: Any) -> str:
    if value is None:
        return EMPTY
    if isinstance(value, str) and value.strip() == "":
        return EMPTY
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        if not value:
            return EMPTY
        return ", ".join(f"#{n}" for n in value)
    return str(value)


def _assignee_label(db: Session, user_id: int | None) -> str:
    if user_id is None:
        return EMPTY
    u = db.get(User, user_id)
    return u.display_name if u else f"ユーザー {user_id}"


def _parent_label(db: Session, parent_id: int | None) -> str:
    if parent_id is None:
        return EMPTY
    parent = db.get(Task, parent_id)
    if parent is None:
        return EMPTY
    return f"#{parent.number}"


def snapshot_task_fields(
    db: Session, task: Task, related_numbers: list[int]
) -> dict[str, str]:
    return {
        "種別": ISSUE_TYPE_LABELS.get(task.issue_type, task.issue_type),
        "件名": task.title or EMPTY,
        "詳細": task.description if task.description.strip() else EMPTY,
        "状態": STATUS_LABELS.get(task.status, task.status),
        "優先度": PRIORITY_LABELS.get(task.priority, task.priority),
        "担当者": _assignee_label(db, task.assignee_id),
        "開始日": _display(task.start_date),
        "期限日": _display(task.due_date),
        "カテゴリー": _display(task.category),
        "マイルストーン": _display(task.milestone),
        "親課題": _parent_label(db, task.parent_id),
        "関連課題": _display(related_numbers),
    }


def diff_snapshots(
    before: dict[str, str], after: dict[str, str]
) -> list[dict[str, str | None]]:
    changes: list[dict[str, str | None]] = []
    for label in before:
        if before[label] != after.get(label):
            changes.append(
                {
                    "label": label,
                    "before": before[label],
                    "after": after.get(label, EMPTY),
                }
            )
    return changes


def record_create(
    db: Session,
    *,
    workspace_id: int,
    task: Task,
    user_id: int,
) -> None:
    db.add(
        TaskChangeHistory(
            workspace_id=workspace_id,
            task_id=task.id,
            task_number=task.number,
            task_title=task.title,
            kind="create",
            changes=[],
            created_by_id=user_id,
        )
    )


def record_update(
    db: Session,
    *,
    workspace_id: int,
    task: Task,
    user_id: int,
    changes: list[dict[str, str | None]],
) -> None:
    if not changes:
        return
    db.add(
        TaskChangeHistory(
            workspace_id=workspace_id,
            task_id=task.id,
            task_number=task.number,
            task_title=task.title,
            kind="update",
            changes=changes,
            created_by_id=user_id,
        )
    )
