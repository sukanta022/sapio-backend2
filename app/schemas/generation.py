from typing import Literal

from pydantic import BaseModel


LanguageCode = Literal["en", "bn"]


class GenerationResponse(BaseModel):
    project_id: int
    status: str
    summary_type: str
    language: LanguageCode
    chunks_processed: int
    script_length: int


class ScriptResponse(BaseModel):
    project_id: int
    status: str
    language: LanguageCode
    script: str
    character_count: int
