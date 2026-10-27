from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

class ProjectCreate(BaseModel):
    document_id: int = Field(gt=0)
    summary_type_id: int = Field(gt=0)
    narrator_id: int = Field(gt=0)
    language_id: int = Field(gt=0)
    audio_format_id: int = Field(gt=0)

    model_config = ConfigDict(extra="forbid")


class SummaryTypeOption(BaseModel):
    id: int
    name: str
    description: str | None

    model_config = ConfigDict(from_attributes=True)


class NarratorOption(BaseModel):
    id: int
    name: str
    description: str | None
    voice_id: str
    gender: str | None
    accent: str | None

    model_config = ConfigDict(from_attributes=True)


class LanguageOption(BaseModel):
    id: int
    name: str
    code: str
    direction: str | None

    model_config = ConfigDict(from_attributes=True)


class AudioFormatOption(BaseModel):
    id: int
    name: str
    description: str | None

    model_config = ConfigDict(from_attributes=True)


class ProjectOptionsResponse(BaseModel):
    summary_types: list[SummaryTypeOption]
    narrators: list[NarratorOption]
    languages: list[LanguageOption]
    audio_formats: list[AudioFormatOption]


class ProjectChapterResponse(BaseModel):
    id: int
    chapter_id: int
    chapter_order: int
    chapter_title: str

    model_config = ConfigDict(from_attributes=True)


class ProjectResponse(BaseModel):
    id: int
    user_id: int
    document_id: int
    summary_type_id: int
    narrator_id: int
    language_id: int
    audio_format_id: int
    status: str
    created_at: datetime
    completed_at: datetime | None
    total_duration: int | None

    model_config = ConfigDict(from_attributes=True)


class ProjectDetailResponse(ProjectResponse):
    chapters: list[ProjectChapterResponse]
