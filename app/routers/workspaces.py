import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, get_membership, require_global_admin, require_workspace_admin
from ..models import User, Workspace, WorkspaceInvite, WorkspaceMember
from ..schemas import (
    InviteCreate,
    InviteOut,
    WorkspaceCreate,
    WorkspaceMemberOut,
    WorkspaceOut,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _workspace_out(ws: Workspace, my_role: str | None) -> WorkspaceOut:
    return WorkspaceOut(
        id=ws.id,
        name=ws.name,
        created_by_id=ws.created_by_id,
        created_at=ws.created_at,
        my_role=my_role,
    )


@router.get("", response_model=list[WorkspaceOut])
def list_workspaces(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(Workspace.created_at.desc())
    ).all()
    return [_workspace_out(ws, role) for ws, role in rows]


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
def create_workspace(
    payload: WorkspaceCreate,
    admin: User = Depends(require_global_admin),
    db: Session = Depends(get_db),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="名前は必須です")

    ws = Workspace(name=name, created_by_id=admin.id)
    db.add(ws)
    db.flush()
    db.add(
        WorkspaceMember(
            workspace_id=ws.id,
            user_id=admin.id,
            role="admin",
        )
    )
    db.commit()
    db.refresh(ws)
    return _workspace_out(ws, "admin")


@router.get("/{workspace_id}", response_model=WorkspaceOut)
def get_workspace(
    workspace_id: int,
    membership: WorkspaceMember = Depends(get_membership),
    db: Session = Depends(get_db),
):
    ws = db.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="ワークスペースが見つかりません")
    return _workspace_out(ws, membership.role)


@router.get("/{workspace_id}/members", response_model=list[WorkspaceMemberOut])
def list_members(
    workspace_id: int,
    membership: WorkspaceMember = Depends(get_membership),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(WorkspaceMember.joined_at.asc())
    ).all()
    return [
        WorkspaceMemberOut(
            id=m.id,
            user_id=m.user_id,
            role=m.role,
            joined_at=m.joined_at,
            email=u.email,
            display_name=u.display_name,
        )
        for m, u in rows
    ]


@router.post(
    "/{workspace_id}/invites",
    response_model=InviteOut,
    status_code=status.HTTP_201_CREATED,
)
def create_invite(
    workspace_id: int,
    payload: InviteCreate,
    membership: WorkspaceMember = Depends(require_workspace_admin),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    token = secrets.token_urlsafe(24)
    invite = WorkspaceInvite(
        workspace_id=workspace_id,
        token=token,
        role=payload.role,
        created_by_id=user.id,
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return InviteOut(
        id=invite.id,
        workspace_id=invite.workspace_id,
        token=invite.token,
        role=invite.role,
        created_at=invite.created_at,
        invite_path=f"/invite/{invite.token}",
    )
