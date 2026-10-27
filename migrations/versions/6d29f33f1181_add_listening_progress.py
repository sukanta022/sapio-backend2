
"""add listening progress

Revision ID: 6d29f33f1181
Revises: bced5304dfa0
Create Date: 2026-10-19 22:26:31.741859

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa



# Revision identifiers used by Alembic.
revision: str = '6d29f33f1181'
down_revision: Union[str, Sequence[str], None] = 'bced5304dfa0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None



def upgrade() -> None:
    op.create_table(
        "listening_progress",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey(
                "users.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey(
                "projects.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "position_seconds",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "is_completed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "last_listened_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "user_id",
            "project_id",
            name="uq_listening_progress_user_project",
        ),
        sa.CheckConstraint(
            "position_seconds >= 0",
            name="ck_listening_progress_position",
        ),
    )

    op.create_index(
        "ix_listening_progress_recent",
        "listening_progress",
        ["user_id", "last_listened_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_listening_progress_recent",
        table_name="listening_progress",
    )

    op.drop_table("listening_progress")

