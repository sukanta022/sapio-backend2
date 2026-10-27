from pydantic import BaseModel


class AudioResponse(BaseModel):
    project_id: int
    status: str
    language: str
    narrator: str
    audio_format: str
    duration_seconds: int
    file_size: int
    chunks_processed: int


class AudioMetadataResponse(BaseModel):
    project_id: int
    status: str
    audio_format: str
    duration_seconds: int
    file_size: int
    download_url: str
