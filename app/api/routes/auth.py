from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user

from app.core.security import (
    create_access_token,
    hash_password,
    verify_password,
)

from app.db.models import Role, User
from app.db.session import get_db

from app.schemas.token import TokenResponse
from app.schemas.user import (
    UserRegister,
    UserResponse,
)


router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)


def build_user_response(
    user: User,
    role_name: str,
) -> UserResponse:
    return UserResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        is_active=user.is_active,
        role=role_name,
        created_at=user.created_at,
    )


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_user(
    payload: UserRegister,
    db: Session = Depends(get_db),
):
    email = payload.email.strip().lower()

    existing_user = db.scalar(
        select(User).where(
            User.email == email
        )
    )

    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is already registered",
        )

    standard_role = db.scalar(
        select(Role).where(
            Role.name == "user"
        )
    )

    if standard_role is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User role is not configured",
        )

    user = User(
        name=payload.name,
        email=email,
        password_hash=hash_password(
            payload.password
        ),
        role_id=standard_role.id,
        is_active=True,
    )

    try:
        db.add(user)
        db.commit()
        db.refresh(user)

    except IntegrityError:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Could not register user",
        ) from None

    return build_user_response(
        user,
        standard_role.name,
    )


@router.post(
    "/login",
    response_model=TokenResponse,
)
def login_user(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    email = form_data.username.strip().lower()

    user = db.scalar(
        select(User).where(
            User.email == email
        )
    )

    invalid_credentials = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password",
        headers={
            "WWW-Authenticate": "Bearer",
        },
    )

    if user is None:
        raise invalid_credentials

    if not verify_password(
        form_data.password,
        user.password_hash,
    ):
        raise invalid_credentials

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )

    token = create_access_token(
        user.id
    )

    return TokenResponse(
        access_token=token,
    )


@router.get(
    "/me",
    response_model=UserResponse,
)
def get_my_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    role_name = db.scalar(
        select(Role.name).where(
            Role.id == current_user.role_id
        )
    )

    if role_name is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User role is not configured",
        )

    return build_user_response(
        current_user,
        role_name,
    )
