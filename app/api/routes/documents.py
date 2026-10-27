from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import Document, User
from app.db.session import get_db
from app.schemas.document import DocumentResponse
from app.services.document_storage import (
    remove_stored_document,
    save_uploaded_document,
)


router = APIRouter(
    prefix="/documents",
    tags=["Documents"],
)


@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    (
        original_name,
        file_type,
        relative_path,
        file_size,
    ) = await save_uploaded_document(
        file,
        current_user.id,
    )

    document = Document(
        user_id=current_user.id,
        file_name=original_name,
        file_type=file_type,
        file_path=relative_path,
        file_size=file_size,
        status="uploaded",
    )

    try:
        db.add(document)
        db.commit()
        db.refresh(document)

    except SQLAlchemyError:
        db.rollback()

        remove_stored_document(relative_path)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not save document metadata.",
        ) from None

    return document


@router.get(
    "",
    response_model=list[DocumentResponse],
)
def list_my_documents(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    documents = db.scalars(
        select(Document)
        .where(Document.user_id == current_user.id)
        .order_by(Document.created_at.desc(), Document.id.desc())
    ).all()

    return documents


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
)
def get_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document = db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.user_id == current_user.id,
        )
    )

    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )

    return document
