from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AudioFormat,
    Language,
    Narrator,
    SummaryType,
)
from app.db.session import get_session_factory


SUMMARY_TYPES = [
    {
        "name": "10-Min Express",
        "description": "Key Takeaways",
    },
    {
        "name": "30-Min Recap",
        "description": "Deep Insights",
    },
    {
        "name": "Chapter Breakdown",
        "description": "Full Outline",
    },
]

NARRATORS = [
    {
        "name": "James",
        "description": "Conversational & Engaging",
        "voice_id": "james",
        "gender": "male",
        "accent": "US",
    },
    {
        "name": "Elena",
        "description": "Authoritative & Clear",
        "voice_id": "elena",
        "gender": "female",
        "accent": "UK",
    },
    {
        "name": "Marcus",
        "description": "Executive Deep Tone",
        "voice_id": "marcus",
        "gender": "male",
        "accent": "US",
    },
    {
        "name": "Sophia",
        "description": "Calm & Educational",
        "voice_id": "sophia",
        "gender": "female",
        "accent": "AU",
    },
]

LANGUAGES = [
    {
        "name": "English",
        "code": "en",
        "direction": "LTR",
    },
    {
        "name": "Bangla",
        "code": "bn",
        "direction": "LTR",
    },
]

AUDIO_FORMATS = [
    {
        "name": "MP3",
        "description": "Compressed audio format",
    },
    {
        "name": "WAV",
        "description": "Uncompressed audio format",
    },
]


def seed_options(db: Session) -> None:
    for item in SUMMARY_TYPES:
        existing = db.scalar(
            select(SummaryType).where(
                SummaryType.name == item["name"]
            )
        )

        if existing is None:
            db.add(SummaryType(**item))

    for item in NARRATORS:
        existing = db.scalar(
            select(Narrator).where(
                Narrator.name == item["name"]
            )
        )

        if existing is None:
            db.add(Narrator(**item))

    for item in LANGUAGES:
        existing = db.scalar(
            select(Language).where(
                Language.code == item["code"]
            )
        )

        if existing is None:
            db.add(Language(**item))

    for item in AUDIO_FORMATS:
        existing = db.scalar(
            select(AudioFormat).where(
                AudioFormat.name == item["name"]
            )
        )

        if existing is None:
            db.add(AudioFormat(**item))

    db.flush()


def main() -> None:
    session_factory = get_session_factory()

    with session_factory() as db:
        try:
            seed_options(db)
            db.commit()
        except Exception:
            db.rollback()
            raise

    print("Project options initialized successfully.")


if __name__ == "__main__":
    main()
