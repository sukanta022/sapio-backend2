from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class JobCreateRequest(BaseModel):
    job_type: Literal["summary", "audio"]


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    job_type: str
    status: str
    progress: int
    attempts: int
    max_attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
