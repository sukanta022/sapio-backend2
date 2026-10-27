from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
)


class UserRegister(BaseModel):
    name: str = Field(
        min_length=2,
        max_length=100,
    )
    email: EmailStr
    password: str = Field(
        min_length=12,
        max_length=128,
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = value.strip()

        if len(cleaned) < 2:
            raise ValueError(
                "Name must contain at least 2 characters."
            )

        return cleaned

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.lower()

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 256:
            raise ValueError(
                "Password is too long."
            )

        if not value.strip():
            raise ValueError(
                "Password cannot be blank."
            )

        return value


class UserResponse(BaseModel):
    id: int
    name: str
    email: EmailStr
    is_active: bool
    role: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
