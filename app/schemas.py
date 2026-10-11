from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    display_name: str
    role: str
    created_at: datetime


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class WorkspaceMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    role: str
    joined_at: datetime
    email: EmailStr | None = None
    display_name: str | None = None


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    created_by_id: int
    created_at: datetime
    my_role: str | None = None


class InviteCreate(BaseModel):
    role: str = Field(default="worker", pattern="^(admin|worker)$")


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    token: str
    role: str
    created_at: datetime
    invite_path: str


class InviteInfoOut(BaseModel):
    workspace_id: int
    workspace_name: str
    role: str


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    filename: str
    content_type: str
    size_bytes: int
    uploaded_by_id: int
    created_at: datetime


class TaskCreate(BaseModel):
    issue_type: str = Field(default="task", max_length=40)
    title: str = Field(min_length=1, max_length=500)
    description: str = ""
    status: str = Field(default="open", max_length=40)
    priority: str = Field(default="medium", max_length=40)
    assignee_id: int | None = None
    due_date: date | None = None
    start_date: date | None = None
    category: str | None = Field(default=None, max_length=120)
    milestone: str | None = Field(default=None, max_length=120)
    parent_id: int | None = None
    related_task_ids: list[int] = Field(default_factory=list)


class TaskUpdate(BaseModel):
    issue_type: str | None = Field(default=None, max_length=40)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    status: str | None = Field(default=None, max_length=40)
    priority: str | None = Field(default=None, max_length=40)
    assignee_id: int | None = None
    clear_assignee: bool = False
    due_date: date | None = None
    clear_due_date: bool = False
    start_date: date | None = None
    clear_start_date: bool = False
    category: str | None = Field(default=None, max_length=120)
    clear_category: bool = False
    milestone: str | None = Field(default=None, max_length=120)
    clear_milestone: bool = False
    parent_id: int | None = None
    clear_parent: bool = False
    related_task_ids: list[int] | None = None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    issue_type: str
    title: str
    description: str
    status: str
    priority: str
    assignee_id: int | None
    due_date: date | None
    start_date: date | None
    category: str | None
    milestone: str | None
    parent_id: int | None
    created_by_id: int
    created_at: datetime
    updated_at: datetime
    related_task_ids: list[int] = Field(default_factory=list)
    child_ids: list[int] = Field(default_factory=list)
    attachments: list[AttachmentOut] = Field(default_factory=list)
    assignee_name: str | None = None
