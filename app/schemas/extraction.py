from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ExtractionResponse(BaseModel):
    id: int
    document_id: int
    status: str
    character_count: int
    created_at: datetime


class ExtractionStatusResponse(BaseModel):
    id: int
    document_id: int
    status: str
    has_extracted_text: bool
    has_cleaned_text: bool
    character_count: int

    model_config = ConfigDict(from_attributes=True)
