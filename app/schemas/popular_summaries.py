from datetime import datetime

from pydantic import BaseModel, Field


class PopularSummaryCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    language_id: int = Field(gt=0)
    narrator_id: int = Field(gt=0)
    summary_type_id: int | None = Field(default=None, gt=0)
    audio_format_id: int | None = Field(default=None, gt=0)
    duration_seconds: int | None = Field(default=None, ge=0)


class PopularSummaryUpdate(BaseModel):
    title: str | None = Field(
        default=None, min_length=1, max_length=255
    )
    description: str | None = None
    summary_type_id: int | None = Field(default=None, gt=0)
    audio_format_id: int | None = Field(default=None, gt=0)
    duration_seconds: int | None = Field(default=None, ge=0)


class PublishRequest(BaseModel):
    is_published: bool


class TranslationUpsert(BaseModel):
    language_id: int = Field(gt=0)
    translated_title: str = Field(
        min_length=1, max_length=255
    )
    translated_description: str | None = None


class TranslationResponse(BaseModel):
    id: int
    language_id: int
    language_code: str
    translated_title: str
    translated_description: str | None
    created_at: datetime


class PopularSummaryResponse(BaseModel):
    id: int
    title: str
    description: str | None
    language_id: int
    narrator_id: int
    summary_type_id: int | None = None
    audio_format_id: int | None = None
    duration_seconds: int | None = None
    is_published: bool
    has_audio: bool
    has_document: bool
    has_thumbnail: bool
    created_at: datetime
    translations: list[TranslationResponse] = Field(
        default_factory=list
    )


class PopularSummaryListResponse(BaseModel):
    items: list[PopularSummaryResponse]
    total: int
    limit: int
    offset: int
