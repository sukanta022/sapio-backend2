from datetime import datetime

from pydantic import BaseModel, Field


class AdminUserResponse(BaseModel):
    id: int
    name: str
    email: str
    role: str
    is_active: bool
    created_at: datetime


class AdminUserListResponse(BaseModel):
    items: list[AdminUserResponse]
    total: int
    limit: int
    offset: int


class UserStatusUpdate(BaseModel):
    is_active: bool


class AdminProjectResponse(BaseModel):
    id: int
    user_id: int
    document_id: int
    status: str
    has_script: bool
    has_audio: bool
    total_duration: int | None
    created_at: datetime


class AdminProjectListResponse(BaseModel):
    items: list[AdminProjectResponse]
    total: int
    limit: int
    offset: int


class StatusCount(BaseModel):
    status: str
    count: int


class AdminDashboardResponse(BaseModel):
    total_users: int
    active_users: int
    disabled_users: int
    total_documents: int
    total_projects: int
    completed_projects: int
    total_generated_files: int
    published_summaries: int
    total_translations: int
    total_background_jobs: int
    project_statuses: list[StatusCount] = Field(
        default_factory=list
    )
    background_job_statuses: list[StatusCount] = Field(
        default_factory=list
    )
