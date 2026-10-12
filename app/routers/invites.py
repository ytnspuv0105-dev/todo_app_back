from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import User, Workspace, WorkspaceInvite, WorkspaceMember
from ..schemas import InviteInfoOut, WorkspaceOut

router = APIRouter(prefix="/invites", tags=["invites"])


def _workspace_out(ws: Workspace, my_role: str | None) -> WorkspaceOut:
    return WorkspaceOut(
        id=ws.id,
        name=ws.name,
        created_by_id=ws.created_by_id,
        created_at=ws.created_at,
        my_role=my_role,
    )


@router.get("/{token}", response_model=InviteInfoOut)
def get_invite_info(token: str, db: Session = Depends(get_db)):
    invite = db.execute(
        select(WorkspaceInvite).where(WorkspaceInvite.token == token)
    ).scalar_one_or_none()
    if invite is None or invite.revoked:
        raise HTTPException(status_code=404, detail="招待リンクが無効です")
    if invite.expires_at and invite.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="招待リンクの有効期限が切れています")
    ws = db.get(Workspace, invite.workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="ワークスペースが見つかりません")
    return InviteInfoOut(
        workspace_id=ws.id,
        workspace_name=ws.name,
        role=invite.role,
        expires_at=invite.expires_at,
    )


@router.post("/{token}/join", response_model=WorkspaceOut)
def join_via_invite(
    token: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    invite = db.execute(
        select(WorkspaceInvite).where(WorkspaceInvite.token == token)
    ).scalar_one_or_none()
    if invite is None or invite.revoked:
        raise HTTPException(status_code=404, detail="招待リンクが無効です")
    if invite.expires_at and invite.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="招待リンクの有効期限が切れています")

    existing = db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == invite.workspace_id,
            WorkspaceMember.user_id == user.id,
        )
    ).scalar_one_or_none()
    if existing:
        ws = db.get(Workspace, invite.workspace_id)
        return _workspace_out(ws, existing.role)

    member = WorkspaceMember(
        workspace_id=invite.workspace_id,
        user_id=user.id,
        role=invite.role,
    )
    db.add(member)
    db.commit()
    ws = db.get(Workspace, invite.workspace_id)
    return _workspace_out(ws, member.role)
