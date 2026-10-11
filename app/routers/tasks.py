from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..config import UPLOAD_DIR
from ..database import get_db
from ..deps import get_current_user, get_membership
from ..models import Attachment, Task, TaskRelation, User, WorkspaceMember
from ..schemas import AttachmentOut, TaskCreate, TaskOut, TaskUpdate

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


def _child_ids(db: Session, task_id: int) -> list[int]:
    rows = db.execute(select(Task.id).where(Task.parent_id == task_id)).scalars().all()
    return list(rows)


def _task_out(db: Session, task: Task) -> TaskOut:
    assignee_name = None
    if task.assignee_id:
        u = db.get(User, task.assignee_id)
        if u:
            assignee_name = u.display_name
    return TaskOut(
        id=task.id,
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
        created_by_id=task.created_by_id,
        created_at=task.created_at,
        updated_at=task.updated_at,
        related_task_ids=_related_ids(db, task.id),
        child_ids=_child_ids(db, task.id),
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
            raise HTTPException(status_code=400, detail=f"関連課題 {rid} が無効です")
        a, b = sorted([task_id, rid])
        db.add(TaskRelation(task_id=a, related_task_id=b))


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

    if payload.parent_id is not None:
        parent = db.get(Task, payload.parent_id)
        if parent is None or parent.workspace_id != workspace_id:
            raise HTTPException(status_code=400, detail="親課題が無効です")

    if payload.assignee_id is not None:
        m = db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == payload.assignee_id,
            )
        ).scalar_one_or_none()
        if m is None:
            raise HTTPException(status_code=400, detail="担当者がワークスペースのメンバーではありません")

    task = Task(
        workspace_id=workspace_id,
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
        parent_id=payload.parent_id,
        created_by_id=user.id,
    )
    db.add(task)
    db.flush()
    if payload.related_task_ids:
        _set_relations(db, task.id, payload.related_task_ids, workspace_id)
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
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)
    if task is None or task.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="課題が見つかりません")

    data = payload.model_dump(exclude_unset=True)
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
        "parent_id",
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

    if payload.related_task_ids is not None:
        _set_relations(db, task.id, payload.related_task_ids, workspace_id)

    task.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(task)
    return _task_out(db, task)


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
