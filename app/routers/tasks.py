from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import UPLOAD_DIR
from ..database import get_db
from ..deps import get_current_user, get_membership
from ..history import (
    diff_snapshots,
    record_comment,
    record_create,
    record_update,
    snapshot_task_fields,
)
from ..models import (
    Attachment,
    Task,
    TaskChangeHistory,
    TaskComment,
    TaskRelation,
    User,
    Workspace,
    WorkspaceMember,
)
from ..schemas import (
    AttachmentOut,
    TaskChangeHistoryOut,
    TaskCommentCreate,
    TaskCommentOut,
    TaskCommentUpdate,
    TaskCreate,
    TaskOut,
    TaskUpdate,
)

router = APIRouter(tags=["tasks"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _related_ids(db: Session, task_id: int) -> list[int]:
    rows = db.execute(
        select(TaskRelation).where(
            or_(TaskRelation.task_id == task_id, TaskRelation.related_task_id == task_id)
        )
    ).scalars().all()
    ids: set[int] = set()
    for r in rows:
        ids.add(r.related_task_id if r.task_id == task_id else r.task_id)
    return sorted(ids)


def _ids_to_numbers(db: Session, task_ids: list[int]) -> list[int]:
    if not task_ids:
        return []
    rows = db.execute(select(Task.id, Task.number).where(Task.id.in_(task_ids))).all()
    by_id = {r[0]: r[1] for r in rows}
    return [by_id[i] for i in task_ids if i in by_id]


def _child_rows(db: Session, task_id: int) -> list[Task]:
    return list(
        db.execute(select(Task).where(Task.parent_id == task_id).order_by(Task.number.asc()))
        .scalars()
        .all()
    )


def _task_by_number(db: Session, workspace_id: int, number: int) -> Task | None:
    return db.execute(
        select(Task).where(Task.workspace_id == workspace_id, Task.number == number)
    ).scalar_one_or_none()


def _next_task_number(db: Session, workspace_id: int) -> int:
    # Lock workspace row so concurrent creates get distinct numbers.
    db.execute(select(Workspace).where(Workspace.id == workspace_id).with_for_update()).scalar_one()
    max_n = db.execute(
        select(func.max(Task.number)).where(Task.workspace_id == workspace_id)
    ).scalar()
    return int(max_n or 0) + 1


def _task_out(db: Session, task: Task) -> TaskOut:
    assignee_name = None
    if task.assignee_id:
        u = db.get(User, task.assignee_id)
        if u:
            assignee_name = u.display_name
    related_ids = _related_ids(db, task.id)
    children = _child_rows(db, task.id)
    parent_number = None
    if task.parent_id:
        parent = db.get(Task, task.parent_id)
        if parent:
            parent_number = parent.number
    return TaskOut(
        id=task.id,
        number=task.number,
        workspace_id=task.workspace_id,
        issue_type=task.issue_type,
        title=task.title,
        description=task.description,
        status=task.status,
        priority=task.priority,
        assignee_id=task.assignee_id,
        due_date=task.due_date,
        start_date=task.start_date,
        category=task.category,
        milestone=task.milestone,
        parent_id=task.parent_id,
        parent_number=parent_number,
        created_by_id=task.created_by_id,
        created_at=task.created_at,
        updated_at=task.updated_at,
        related_task_ids=related_ids,
        related_task_numbers=_ids_to_numbers(db, related_ids),
        child_ids=[c.id for c in children],
        child_numbers=[c.number for c in children],
        attachments=[AttachmentOut.model_validate(a) for a in task.attachments],
        assignee_name=assignee_name,
    )


def _set_relations(db: Session, task_id: int, related_ids: list[int], workspace_id: int) -> None:
    existing = db.execute(
        select(TaskRelation).where(
            or_(TaskRelation.task_id == task_id, TaskRelation.related_task_id == task_id)
        )
    ).scalars().all()
    for rel in existing:
        db.delete(rel)
    db.flush()

    unique = sorted({i for i in related_ids if i != task_id})
    for rid in unique:
        other = db.get(Task, rid)
        if other is None or other.workspace_id != workspace_id:
            raise HTTPException(status_code=400, detail=f"関連課題が無効です")
        a, b = sorted([task_id, rid])
        db.add(TaskRelation(task_id=a, related_task_id=b))


def _resolve_parent_id(
    db: Session, workspace_id: int, parent_number: int | None, self_id: int | None = None
) -> int | None:
    if parent_number is None:
        return None
    parent = _task_by_number(db, workspace_id, parent_number)
    if parent is None:
        raise HTTPException(status_code=400, detail="親課題が無効です")
    if self_id is not None and parent.id == self_id:
        raise HTTPException(status_code=400, detail="自分自身を親課題にはできません")
    return parent.id


def _resolve_related_ids(
    db: Session, workspace_id: int, numbers: list[int], self_id: int | None = None
) -> list[int]:
    ids: list[int] = []
    for n in numbers:
        t = _task_by_number(db, workspace_id, n)
        if t is None:
            raise HTTPException(status_code=400, detail=f"関連課題 #{n} が無効です")
        if self_id is not None and t.id == self_id:
            continue
        ids.append(t.id)
    return ids


@router.get("/workspaces/{workspace_id}/tasks", response_model=list[TaskOut])
def list_tasks(
    workspace_id: int,
    membership: WorkspaceMember = Depends(get_membership),
    db: Session = Depends(get_db),
):
    tasks = (
        db.execute(
            select(Task)
            .where(Task.workspace_id == workspace_id)
            .order_by(Task.updated_at.desc())
        )
        .scalars()
        .all()
    )
    return [_task_out(db, t) for t in tasks]


@router.post(
    "/workspaces/{workspace_id}/tasks",
    response_model=TaskOut,
    status_code=status.HTTP_201_CREATED,
)
def create_task(
    workspace_id: int,
    payload: TaskCreate,
    membership: WorkspaceMember = Depends(get_membership),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="件名は必須です")

    parent_id = _resolve_parent_id(db, workspace_id, payload.parent_number)

    if payload.assignee_id is not None:
        m = db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == payload.assignee_id,
            )
        ).scalar_one_or_none()
        if m is None:
            raise HTTPException(status_code=400, detail="担当者がワークスペースのメンバーではありません")

    number = _next_task_number(db, workspace_id)
    task = Task(
        workspace_id=workspace_id,
        number=number,
        issue_type=payload.issue_type,
        title=title,
        description=payload.description or "",
        status=payload.status,
        priority=payload.priority,
        assignee_id=payload.assignee_id,
        due_date=payload.due_date,
        start_date=payload.start_date,
        category=payload.category,
        milestone=payload.milestone,
        parent_id=parent_id,
        created_by_id=user.id,
    )
    db.add(task)
    db.flush()
    if payload.related_task_numbers:
        related_ids = _resolve_related_ids(
            db, workspace_id, payload.related_task_numbers, self_id=task.id
        )
        _set_relations(db, task.id, related_ids, workspace_id)
    record_create(db, workspace_id=workspace_id, task=task, user_id=user.id)
    db.commit()
    db.refresh(task)
    return _task_out(db, task)


@router.get("/workspaces/{workspace_id}/tasks/{task_id}", response_model=TaskOut)
def get_task(
    workspace_id: int,
    task_id: int,
    membership: WorkspaceMember = Depends(get_membership),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)
    if task is None or task.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="課題が見つかりません")
    return _task_out(db, task)


@router.patch("/workspaces/{workspace_id}/tasks/{task_id}", response_model=TaskOut)
def update_task(
    workspace_id: int,
    task_id: int,
    payload: TaskUpdate,
    membership: WorkspaceMember = Depends(get_membership),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)
    if task is None or task.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="課題が見つかりません")

    before_related = _ids_to_numbers(db, _related_ids(db, task.id))
    before = snapshot_task_fields(db, task, before_related)

    data = payload.model_dump(exclude_unset=True)
    comment_text = data.pop("comment", None)
    for key in (
        "issue_type",
        "title",
        "description",
        "status",
        "priority",
        "assignee_id",
        "due_date",
        "start_date",
        "category",
        "milestone",
    ):
        if key in data and data[key] is not None:
            if key == "title":
                title = data[key].strip()
                if not title:
                    raise HTTPException(status_code=422, detail="件名は必須です")
                setattr(task, key, title)
            else:
                setattr(task, key, data[key])

    if payload.clear_assignee:
        task.assignee_id = None
    if payload.clear_due_date:
        task.due_date = None
    if payload.clear_start_date:
        task.start_date = None
    if payload.clear_category:
        task.category = None
    if payload.clear_milestone:
        task.milestone = None
    if payload.clear_parent:
        task.parent_id = None
    elif "parent_number" in data:
        task.parent_id = _resolve_parent_id(
            db, workspace_id, payload.parent_number, self_id=task.id
        )

    if task.parent_id is not None:
        if task.parent_id == task.id:
            raise HTTPException(status_code=400, detail="自分自身を親課題にはできません")
        parent = db.get(Task, task.parent_id)
        if parent is None or parent.workspace_id != workspace_id:
            raise HTTPException(status_code=400, detail="親課題が無効です")

    if task.assignee_id is not None:
        m = db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == task.assignee_id,
            )
        ).scalar_one_or_none()
        if m is None:
            raise HTTPException(status_code=400, detail="担当者がワークスペースのメンバーではありません")

    if payload.related_task_numbers is not None:
        related_ids = _resolve_related_ids(
            db, workspace_id, payload.related_task_numbers, self_id=task.id
        )
        _set_relations(db, task.id, related_ids, workspace_id)

    after_related = _ids_to_numbers(db, _related_ids(db, task.id))
    after = snapshot_task_fields(db, task, after_related)
    changes = diff_snapshots(before, after)

    comment_body = (comment_text or "").strip() if isinstance(comment_text, str) else ""
    if comment_body:
        comment = TaskComment(task_id=task.id, author_id=user.id, body=comment_body)
        db.add(comment)
        db.flush()
        record_comment(
            db,
            workspace_id=workspace_id,
            task=task,
            user_id=user.id,
            body=comment_body,
            before=None,
            extra_changes=changes or None,
            comment_id=comment.id,
        )
    else:
        record_update(
            db,
            workspace_id=workspace_id,
            task=task,
            user_id=user.id,
            changes=changes,
        )

    task.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(task)
    return _task_out(db, task)


def _history_out(
    rows: list[TaskChangeHistory], db: Session
) -> list[TaskChangeHistoryOut]:
    actor_ids = {r.created_by_id for r in rows}
    actors = {
        u.id: u.display_name
        for u in db.execute(select(User).where(User.id.in_(actor_ids))).scalars().all()
    } if actor_ids else {}
    return [
        TaskChangeHistoryOut(
            id=r.id,
            workspace_id=r.workspace_id,
            task_id=r.task_id,
            task_number=r.task_number,
            task_title=r.task_title,
            kind=r.kind,
            changes=r.changes or [],
            comment_id=r.comment_id,
            created_by_id=r.created_by_id,
            created_by_name=actors.get(r.created_by_id),
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get(
    "/workspaces/{workspace_id}/task-history",
    response_model=list[TaskChangeHistoryOut],
)
def list_task_history(
    workspace_id: int,
    limit: int = 20,
    membership: WorkspaceMember = Depends(get_membership),
    db: Session = Depends(get_db),
):
    lim = max(1, min(limit, 100))
    rows = (
        db.execute(
            select(TaskChangeHistory)
            .where(TaskChangeHistory.workspace_id == workspace_id)
            .order_by(TaskChangeHistory.created_at.desc(), TaskChangeHistory.id.desc())
            .limit(lim)
        )
        .scalars()
        .all()
    )
    return _history_out(rows, db)


@router.get(
    "/workspaces/{workspace_id}/tasks/{task_id}/history",
    response_model=list[TaskChangeHistoryOut],
)
def list_task_change_history(
    workspace_id: int,
    task_id: int,
    limit: int = 100,
    membership: WorkspaceMember = Depends(get_membership),
    db: Session = Depends(get_db),
):
    """Chronological change timeline for one task (oldest first)."""
    task = db.get(Task, task_id)
    if task is None or task.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="課題が見つかりません")
    lim = max(1, min(limit, 200))
    rows = (
        db.execute(
            select(TaskChangeHistory)
            .where(
                TaskChangeHistory.workspace_id == workspace_id,
                TaskChangeHistory.task_id == task_id,
            )
            .order_by(TaskChangeHistory.created_at.asc(), TaskChangeHistory.id.asc())
            .limit(lim)
        )
        .scalars()
        .all()
    )
    return _history_out(rows, db)


def _comment_out(db: Session, comment: TaskComment) -> TaskCommentOut:
    author = db.get(User, comment.author_id)
    return TaskCommentOut(
        id=comment.id,
        task_id=comment.task_id,
        author_id=comment.author_id,
        author_name=author.display_name if author else None,
        body=comment.body,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
    )


@router.get(
    "/workspaces/{workspace_id}/tasks/{task_id}/comments",
    response_model=list[TaskCommentOut],
)
def list_comments(
    workspace_id: int,
    task_id: int,
    membership: WorkspaceMember = Depends(get_membership),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)
    if task is None or task.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="課題が見つかりません")
    rows = (
        db.execute(
            select(TaskComment)
            .where(TaskComment.task_id == task_id)
            .order_by(TaskComment.created_at.asc(), TaskComment.id.asc())
        )
        .scalars()
        .all()
    )
    return [_comment_out(db, c) for c in rows]


@router.post(
    "/workspaces/{workspace_id}/tasks/{task_id}/comments",
    response_model=TaskCommentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_comment(
    workspace_id: int,
    task_id: int,
    payload: TaskCommentCreate,
    membership: WorkspaceMember = Depends(get_membership),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)
    if task is None or task.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="課題が見つかりません")
    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=422, detail="コメントを入力してください")

    comment = TaskComment(task_id=task.id, author_id=user.id, body=body)
    db.add(comment)
    db.flush()
    record_comment(
        db,
        workspace_id=workspace_id,
        task=task,
        user_id=user.id,
        body=body,
        before=None,
        comment_id=comment.id,
    )
    db.commit()
    db.refresh(comment)
    return _comment_out(db, comment)


@router.patch(
    "/workspaces/{workspace_id}/tasks/{task_id}/comments/{comment_id}",
    response_model=TaskCommentOut,
)
def update_comment(
    workspace_id: int,
    task_id: int,
    comment_id: int,
    payload: TaskCommentUpdate,
    membership: WorkspaceMember = Depends(get_membership),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)
    if task is None or task.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="課題が見つかりません")
    comment = db.get(TaskComment, comment_id)
    if comment is None or comment.task_id != task_id:
        raise HTTPException(status_code=404, detail="コメントが見つかりません")
    if comment.author_id != user.id:
        raise HTTPException(status_code=403, detail="自分のコメントのみ編集できます")

    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=422, detail="コメントを入力してください")
    if body == comment.body:
        return _comment_out(db, comment)

    before = comment.body
    comment.body = body
    comment.updated_at = datetime.now(timezone.utc)
    record_comment(
        db,
        workspace_id=workspace_id,
        task=task,
        user_id=user.id,
        body=body,
        before=before,
        comment_id=comment.id,
    )
    db.commit()
    db.refresh(comment)
    return _comment_out(db, comment)


@router.delete(
    "/workspaces/{workspace_id}/tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_task(
    workspace_id: int,
    task_id: int,
    membership: WorkspaceMember = Depends(get_membership),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)
    if task is None or task.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="課題が見つかりません")

    # Detach children
    children = db.execute(select(Task).where(Task.parent_id == task_id)).scalars().all()
    for c in children:
        c.parent_id = None

    rels = db.execute(
        select(TaskRelation).where(
            or_(TaskRelation.task_id == task_id, TaskRelation.related_task_id == task_id)
        )
    ).scalars().all()
    for r in rels:
        db.delete(r)

    for att in list(task.attachments):
        path = UPLOAD_DIR / att.stored_name
        if path.exists():
            path.unlink()
        db.delete(att)

    db.delete(task)
    db.commit()
    return None


@router.post(
    "/workspaces/{workspace_id}/tasks/{task_id}/attachments",
    response_model=AttachmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    workspace_id: int,
    task_id: int,
    file: UploadFile = File(...),
    membership: WorkspaceMember = Depends(get_membership),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)
    if task is None or task.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="課題が見つかりません")

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="ファイルサイズは10MBまでです")
    if not file.filename:
        raise HTTPException(status_code=400, detail="ファイル名が必要です")

    stored = f"{uuid4().hex}_{Path(file.filename).name}"
    (UPLOAD_DIR / stored).write_bytes(data)

    att = Attachment(
        task_id=task.id,
        filename=Path(file.filename).name,
        content_type=file.content_type or "application/octet-stream",
        stored_name=stored,
        size_bytes=len(data),
        uploaded_by_id=user.id,
    )
    db.add(att)
    task.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(att)
    return AttachmentOut.model_validate(att)


@router.get("/workspaces/{workspace_id}/attachments/{attachment_id}")
def download_attachment(
    workspace_id: int,
    attachment_id: int,
    membership: WorkspaceMember = Depends(get_membership),
    db: Session = Depends(get_db),
):
    att = db.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(status_code=404, detail="添付ファイルが見つかりません")
    task = db.get(Task, att.task_id)
    if task is None or task.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="添付ファイルが見つかりません")
    path = UPLOAD_DIR / att.stored_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="ファイル実体が見つかりません")
    return FileResponse(path, filename=att.filename, media_type=att.content_type)


@router.delete(
    "/workspaces/{workspace_id}/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_attachment(
    workspace_id: int,
    attachment_id: int,
    membership: WorkspaceMember = Depends(get_membership),
    db: Session = Depends(get_db),
):
    att = db.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(status_code=404, detail="添付ファイルが見つかりません")
    task = db.get(Task, att.task_id)
    if task is None or task.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="添付ファイルが見つかりません")
    path = UPLOAD_DIR / att.stored_name
    if path.exists():
        path.unlink()
    db.delete(att)
    db.commit()
    return None
