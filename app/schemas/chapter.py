from pydantic import BaseModel, ConfigDict

class ChapterResponse(BaseModel):
    id: int
    extraction_job_id: int
    chapter_number: int
    title: str | None
    start_position: int | None
    end_position: int | None

    model_config = ConfigDict(from_attributes=True)


class ChapterDetailResponse(ChapterResponse):
    content: str | None


class ChapterDetectionResponse(BaseModel):
    document_id: int
    extraction_job_id: int
    total_chapters: int
    chapters: list[ChapterResponse]
