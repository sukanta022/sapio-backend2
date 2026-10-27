from getpass import getpass

from pydantic import EmailStr, TypeAdapter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.security import hash_password
from app.db.models import Role, User
from app.db.session import get_session_factory


def main() -> None:
    print("Sapio AI — Create Administrator")
    print("--------------------------------")

    name = input("Admin name: ").strip()

    if not 2 <= len(name) <= 100:
        print("Name must contain 2 to 100 characters.")
        return

    raw_email = input("Admin email: ").strip().lower()

    try:
        email = TypeAdapter(EmailStr).validate_python(
            raw_email
        )
    except ValueError:
        print("Invalid email address.")
        return

    password = getpass("Admin password: ")
    confirmation = getpass("Confirm password: ")

    if password != confirmation:
        print("Passwords do not match.")
        return

    if not 12 <= len(password) <= 128:
        print(
            "Password must contain "
            "12 to 128 characters."
        )
        return

    if (
        len(password.encode("utf-8")) > 256
        or not password.strip()
    ):
        print("Invalid password.")
        return

    session_factory = get_session_factory()

    with session_factory() as db:
        admin_role = db.scalar(
            select(Role).where(Role.name == "admin")
        )

        if admin_role is None:
            print(
                "Admin role does not exist. "
                "Run the role initialization first."
            )
            return

        existing_user = db.scalar(
            select(User).where(User.email == email)
        )

        if existing_user is not None:
            print(
                "An account with this email "
                "already exists."
            )
            return

        admin = User(
            name=name,
            email=email,
            password_hash=hash_password(password),
            role_id=admin_role.id,
            is_active=True,
        )

        try:
            db.add(admin)
            db.commit()
        except IntegrityError:
            db.rollback()
            print("Could not create administrator.")
            return

    print("Administrator created successfully.")


if __name__ == "__main__":
    main()
