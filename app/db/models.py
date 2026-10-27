from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from app.db.base import Base


# ========================================
# 1. USERS AND ROLES
# ========================================

class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    name: Mapped[str] = mapped_column(
        String(50), unique=True
    )
    description: Mapped[str | None] = mapped_column(Text)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(
        String(100), unique=True
    )
    password_hash: Mapped[str] = mapped_column(
        String(255)
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


# ========================================
# 2. DOCUMENTS AND EXTRACTION
# ========================================

class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id")
    )
    file_name: Mapped[str] = mapped_column(
        String(255)
    )
    file_type: Mapped[str] = mapped_column(
        String(10)
    )
    file_path: Mapped[str] = mapped_column(Text)
    file_size: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class ExtractionJob(Base):
    __tablename__ = "extraction_jobs"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id"),
        unique=True,
    )
    extracted_text: Mapped[str | None] = mapped_column(
        Text
    )
    cleaned_text: Mapped[str | None] = mapped_column(
        Text
    )
    chapters_detected: Mapped[dict | list | None] = (
        mapped_column(JSONB)
    )
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class Chapter(Base):
    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    extraction_job_id: Mapped[int] = mapped_column(
        ForeignKey("extraction_jobs.id")
    )
    chapter_number: Mapped[int] = mapped_column(
        Integer
    )
    title: Mapped[str | None] = mapped_column(
        String(255)
    )
    content: Mapped[str | None] = mapped_column(Text)
    start_position: Mapped[int | None] = mapped_column(
        Integer
    )
    end_position: Mapped[int | None] = mapped_column(
        Integer
    )


# ========================================
# 3. SUMMARY AND NARRATION OPTIONS
# ========================================

class SummaryType(Base):
    __tablename__ = "summary_types"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    name: Mapped[str] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)


class Narrator(Base):
    __tablename__ = "narrators"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    name: Mapped[str] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(
        Text
    )
    voice_id: Mapped[str] = mapped_column(String(100))
    gender: Mapped[str | None] = mapped_column(
        String(10)
    )
    accent: Mapped[str | None] = mapped_column(
        String(50)
    )


class Language(Base):
    __tablename__ = "languages"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    name: Mapped[str] = mapped_column(String(50))
    code: Mapped[str] = mapped_column(String(10))
    direction: Mapped[str | None] = mapped_column(
        String(50)
    )


class AudioFormat(Base):
    __tablename__ = "audio_formats"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    name: Mapped[str] = mapped_column(String(10))
    description: Mapped[str | None] = mapped_column(
        Text
    )


# ========================================
# 4. PROJECTS AND GENERATED FILES
# ========================================

class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id")
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id")
    )
    summary_type_id: Mapped[int] = mapped_column(
        ForeignKey("summary_types.id")
    )
    narrator_id: Mapped[int] = mapped_column(
        ForeignKey("narrators.id")
    )
    language_id: Mapped[int] = mapped_column(
        ForeignKey("languages.id")
    )
    audio_format_id: Mapped[int] = mapped_column(
        ForeignKey("audio_formats.id")
    )
    status: Mapped[str] = mapped_column(String(20))
    script: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = (
        mapped_column(DateTime)
    )
    audio_file_path: Mapped[str | None] = (
        mapped_column(Text)
    )
    total_duration: Mapped[int | None] = (
        mapped_column(Integer)
    )


class GeneratedFile(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id")
    )
    file_type: Mapped[str] = mapped_column(
        String(10)
    )
    file_path: Mapped[str] = mapped_column(Text)
    file_size: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class ProjectChapter(Base):
    __tablename__ = "project_chapters"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id")
    )
    chapter_id: Mapped[int] = mapped_column(
        ForeignKey("chapters.id")
    )
    chapter_order: Mapped[int] = mapped_column(
        Integer
    )
    chapter_title: Mapped[str] = mapped_column(
        String(255)
    )


# ========================================
# 5. POPULAR SUMMARIES AND TRANSLATIONS
# ========================================

class PopularSummary(Base):
    __tablename__ = "popular_summaries"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(
        Text
    )
    file_path: Mapped[str | None] = mapped_column(
        Text
    )
    audio_file_path: Mapped[str | None] = (
        mapped_column(Text)
    )
    thumbnail_path: Mapped[str | None] = (
        mapped_column(Text)
    )
    summary_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("summary_types.id"), nullable=True
    )
    audio_format_id: Mapped[int | None] = mapped_column(
        ForeignKey("audio_formats.id"), nullable=True
    )
    duration_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    language_id: Mapped[int] = mapped_column(
        ForeignKey("languages.id")
    )
    narrator_id: Mapped[int] = mapped_column(
        ForeignKey("narrators.id")
    )
    is_published: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class Translation(Base):
    __tablename__ = "translations"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    popular_summary_id: Mapped[int] = mapped_column(
        ForeignKey("popular_summaries.id")
    )
    language_id: Mapped[int] = mapped_column(
        ForeignKey("languages.id")
    )
    translated_title: Mapped[str] = mapped_column(
        String(255)
    )
    translated_description: Mapped[str | None] = (
        mapped_column(Text)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


# ========================================
# 6. PROCESSING LOGS
# ========================================

class ProcessingLog(Base):
    __tablename__ = "processing_logs"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id")
    )
    stage: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20))
    message: Mapped[str | None] = mapped_column(
        Text
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class ListeningProgress(Base):
    __tablename__ = "listening_progress"

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "project_id",
            name="uq_listening_progress_user_project",
        ),
        CheckConstraint(
            "position_seconds >= 0",
            name="ck_listening_progress_position",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    position_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    is_completed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )

    last_listened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
