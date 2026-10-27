from sqlalchemy import select

from app.db.models import Role
from app.db.session import get_session_factory


DEFAULT_ROLES = {
    "user": "Standard Sapio AI user",
    "admin": "Sapio AI administrator",
}


def main() -> None:
    session_factory = get_session_factory()

    with session_factory() as db:
        for name, description in DEFAULT_ROLES.items():
            existing = db.scalar(
                select(Role).where(Role.name == name)
            )

            if existing is not None:
                print(f"Role already exists: {name}")
                continue

            db.add(
                Role(
                    name=name,
                    description=description,
                )
            )

            print(f"Created role: {name}")

        db.commit()

    print("Role initialization completed.")


if __name__ == "__main__":
    main()
