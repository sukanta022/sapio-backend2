from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin
from app.db.models import (
    Language,
    Narrator,
    SummaryType,
    AudioFormat,
    PopularSummary,
    Translation,
    User,
)
from app.db.session import get_db
from app.schemas.popular_summaries import (
    PopularSummaryCreate,
    PopularSummaryListResponse,
    PopularSummaryResponse,
    PopularSummaryUpdate,
    PublishRequest,
    TranslationResponse,
    TranslationUpsert,
)


router = APIRouter(
    prefix="/popular-summaries",
    tags=["Popular Summaries"],
)


def require_language(db: Session, language_id: int):
    language = db.get(Language, language_id)
    if language is None:
        raise HTTPException(
            422, "Unknown language."
        )
    return language


def require_summary(
    db: Session,
    summary_id: int,
    published_only: bool = False,
):
    statement = select(PopularSummary).where(
        PopularSummary.id == summary_id
    )

    if published_only:
        statement = statement.where(
            PopularSummary.is_published.is_(True)
        )

    summary = db.scalar(statement)

    if summary is None:
        raise HTTPException(
            404, "Popular summary not found."
        )

    return summary


def serialize_summary(
    db: Session,
    summary: PopularSummary,
    language_code: str | None = None,
) -> PopularSummaryResponse:
    statement = (
        select(Translation, Language.code)
        .join(
            Language,
            Translation.language_id == Language.id,
        )
        .where(
            Translation.popular_summary_id == summary.id
        )
        .order_by(Translation.id)
    )

    if language_code:
        statement = statement.where(
            func.lower(Language.code)
            == language_code.lower()
        )

    translations = [
        TranslationResponse(
            id=translation.id,
            language_id=translation.language_id,
            language_code=code,
            translated_title=(
                translation.translated_title
            ),
            translated_description=(
                translation.translated_description
            ),
            created_at=translation.created_at,
        )
        for translation, code in db.execute(statement)
    ]

    return PopularSummaryResponse(
        id=summary.id,
        title=summary.title,
        description=summary.description,
        language_id=summary.language_id,
        narrator_id=summary.narrator_id,
        summary_type_id=summary.summary_type_id,
        audio_format_id=summary.audio_format_id,
        duration_seconds=summary.duration_seconds,
        is_published=summary.is_published,
        has_audio=bool(summary.audio_file_path),
        has_document=bool(summary.file_path),
        has_thumbnail=bool(summary.thumbnail_path),
        created_at=summary.created_at,
        translations=translations,
    )


def list_summaries(
    db: Session,
    published_only: bool,
    search: str | None,
    language_id: int | None,
    limit: int,
    offset: int,
) -> PopularSummaryListResponse:
    filters = []

    if published_only:
        filters.append(
            PopularSummary.is_published.is_(True)
        )

    if search:
        filters.append(
            PopularSummary.title.ilike(
                f"%{search.strip()}%"
            )
        )

    if language_id is not None:
        filters.append(
            PopularSummary.language_id == language_id
        )

    total = db.scalar(
        select(func.count(PopularSummary.id)).where(
            *filters
        )
    ) or 0

    summaries = db.scalars(
        select(PopularSummary)
        .where(*filters)
        .order_by(
            PopularSummary.created_at.desc(),
            PopularSummary.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    ).all()

    return PopularSummaryListResponse(
        items=[
            serialize_summary(db, summary)
            for summary in summaries
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "",
    response_model=PopularSummaryListResponse,
)
def public_popular_summaries(
    search: str | None = Query(
        default=None, max_length=100
    ),
    language_id: int | None = Query(
        default=None, gt=0
    ),
    limit: int = Query(
        default=20, ge=1, le=100
    ),
    offset: int = Query(
        default=0, ge=0
    ),
    db: Session = Depends(get_db),
):
    return list_summaries(
        db, True, search, language_id, limit, offset
    )


@router.get(
    "/admin/all",
    response_model=PopularSummaryListResponse,
)
def admin_popular_summaries(
    search: str | None = Query(
        default=None, max_length=100
    ),
    language_id: int | None = Query(
        default=None, gt=0
    ),
    limit: int = Query(
        default=20, ge=1, le=100
    ),
    offset: int = Query(
        default=0, ge=0
    ),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    return list_summaries(
        db, False, search, language_id, limit, offset
    )


@router.post(
    "",
    response_model=PopularSummaryResponse,
    status_code=201,
)
def create_popular_summary(
    payload: PopularSummaryCreate,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_language(db, payload.language_id)

    if db.get(Narrator, payload.narrator_id) is None:
        raise HTTPException(
            422, "Unknown narrator."
        )

    if payload.summary_type_id is not None and db.get(SummaryType, payload.summary_type_id) is None:
        raise HTTPException(422, "Unknown summary type.")
    if payload.audio_format_id is not None and db.get(AudioFormat, payload.audio_format_id) is None:
        raise HTTPException(422, "Unknown audio format.")

    summary = PopularSummary(
        title=payload.title.strip(),
        description=payload.description,
        language_id=payload.language_id,
        narrator_id=payload.narrator_id,
        summary_type_id=payload.summary_type_id,
        audio_format_id=payload.audio_format_id,
        duration_seconds=payload.duration_seconds,
        is_published=False,
    )

    db.add(summary)
    db.commit()
    db.refresh(summary)

    return serialize_summary(db, summary)


@router.get(
    "/{summary_id}",
    response_model=PopularSummaryResponse,
)
def get_public_popular_summary(
    summary_id: int,
    language: str | None = Query(
        default=None, max_length=10
    ),
    db: Session = Depends(get_db),
):
    summary = require_summary(
        db, summary_id, published_only=True
    )

    return serialize_summary(db, summary, language)


@router.get(
    "/{summary_id}/admin",
    response_model=PopularSummaryResponse,
)
def get_admin_popular_summary(
    summary_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    summary = require_summary(db, summary_id)
    return serialize_summary(db, summary)


@router.patch(
    "/{summary_id}",
    response_model=PopularSummaryResponse,
)
def update_popular_summary(
    summary_id: int,
    payload: PopularSummaryUpdate,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    summary = require_summary(db, summary_id)

    changes = payload.model_dump(exclude_unset=True)

    if "title" in changes:
        title = changes["title"].strip()
        if not title:
            raise HTTPException(
                422, "Title cannot be blank."
            )
        changes["title"] = title

    if changes.get("summary_type_id") is not None and db.get(SummaryType, changes["summary_type_id"]) is None:
        raise HTTPException(422, "Unknown summary type.")
    if changes.get("audio_format_id") is not None and db.get(AudioFormat, changes["audio_format_id"]) is None:
        raise HTTPException(422, "Unknown audio format.")

    for name, value in changes.items():
        setattr(summary, name, value)

    db.commit()
    db.refresh(summary)

    return serialize_summary(db, summary)


@router.patch(
    "/{summary_id}/publish",
    response_model=PopularSummaryResponse,
)
def set_publication(
    summary_id: int,
    payload: PublishRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    summary = require_summary(db, summary_id)

    summary.is_published = payload.is_published
    db.commit()
    db.refresh(summary)

    return serialize_summary(db, summary)


@router.put(
    "/{summary_id}/translations",
    response_model=TranslationResponse,
)
def upsert_translation(
    summary_id: int,
    payload: TranslationUpsert,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_summary(db, summary_id)
    language = require_language(
        db, payload.language_id
    )

    translation = db.scalar(
        select(Translation).where(
            Translation.popular_summary_id == summary_id,
            Translation.language_id == payload.language_id,
        )
        .with_for_update()
    )

    if translation is None:
        translation = Translation(
            popular_summary_id=summary_id,
            language_id=payload.language_id,
            translated_title=(
                payload.translated_title.strip()
            ),
            translated_description=(
                payload.translated_description
            ),
        )
        db.add(translation)
    else:
        translation.translated_title = (
            payload.translated_title.strip()
        )
        translation.translated_description = (
            payload.translated_description
        )

    if not translation.translated_title:
        raise HTTPException(
            422, "Translated title cannot be blank."
        )

    try:
        db.commit()
        db.refresh(translation)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            409, "Translation could not be saved."
        ) from None

    return TranslationResponse(
        id=translation.id,
        language_id=translation.language_id,
        language_code=language.code,
        translated_title=translation.translated_title,
        translated_description=(
            translation.translated_description
        ),
        created_at=translation.created_at,
    )


@router.delete(
    "/{summary_id}/translations/{language_id}",
    status_code=204,
)
def delete_translation(
    summary_id: int,
    language_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_summary(db, summary_id)

    translations = db.scalars(
        select(Translation).where(
            Translation.popular_summary_id == summary_id,
            Translation.language_id == language_id,
        )
    ).all()

    if not translations:
        raise HTTPException(
            404, "Translation not found."
        )

    for translation in translations:
        db.delete(translation)

    db.commit()


@router.delete(
    "/{summary_id}",
    status_code=204,
)
def delete_popular_summary(
    summary_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    summary = require_summary(db, summary_id)

    # Delete child records before the parent.
    translations = db.scalars(
        select(Translation).where(
            Translation.popular_summary_id == summary_id
        )
    ).all()

    for translation in translations:
        db.delete(translation)

    db.flush()
    db.delete(summary)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            409, "Summary cannot be deleted."
        ) from None
