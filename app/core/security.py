from datetime import (
    datetime,
    timedelta,
    timezone,
)

import jwt
from pwdlib import PasswordHash

from app.core.config import (
    get_jwt_secret,
    get_settings,
)


password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(
    plain_password: str,
    hashed_password: str,
) -> bool:
    return password_hash.verify(
        plain_password,
        hashed_password,
    )


def create_access_token(user_id: int) -> str:
    if user_id <= 0:
        raise ValueError(
            "User ID must be positive."
        )

    settings = get_settings()
    now = datetime.now(timezone.utc)

    expires_at = now + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )

    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": expires_at,
        "type": "access",
    }

    return jwt.encode(
        payload,
        get_jwt_secret(),
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> int:
    settings = get_settings()

    payload = jwt.decode(
        token,
        get_jwt_secret(),
        algorithms=[settings.JWT_ALGORITHM],
        options={
            "require": [
                "sub",
                "iat",
                "exp",
                "type",
            ],
            "verify_signature": True,
            "verify_exp": True,
            "verify_iat": True,
        },
        leeway=0,
    )

    if payload.get("type") != "access":
        raise jwt.InvalidTokenError(
            "Invalid token type."
        )

    subject = payload.get("sub")

    if (
        not isinstance(subject, str)
        or not subject.isascii()
        or not subject.isdecimal()
    ):
        raise jwt.InvalidTokenError(
            "Invalid token subject."
        )

    user_id = int(subject)

    if user_id <= 0:
        raise jwt.InvalidTokenError(
            "Invalid token subject."
        )

    return user_id
