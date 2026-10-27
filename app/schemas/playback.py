
from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class PlaybackUpdateRequest(BaseModel):
    position_seconds: int = Field(
        ge=0,
        description="Current audio position in seconds.",
    )


class PlaybackResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    project_id: int
    position_seconds: int
    total_duration_seconds: int
    is_completed: bool

    last_listened_at: datetime | None = None
    completed_at: datetime | None = None


class ListeningHistoryItem(BaseModel):
    project_id: int
    document_name: str
    position_seconds: int
    total_duration_seconds: int
    is_completed: bool
    last_listened_at: datetime
    completed_at: datetime | None


class ListeningHistoryResponse(BaseModel):
    items: list[ListeningHistoryItem]
    limit: int
    offset: int
