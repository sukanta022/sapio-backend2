
"""add background jobs

Revision ID: bced5304dfa0
Revises: 7aa10282b70c
Create Date: 2026-10-18 23:23:39.253355

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa



# Revision identifiers used by Alembic.
revision: str = 'bced5304dfa0'
down_revision: Union[str, Sequence[str], None] = '7aa10282b70c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "background_jobs",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
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
            "job_type",
            sa.String(20),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="queued",
        ),
        sa.Column(
            "progress",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            nullable=False,
            server_default="3",
        ),
        sa.Column(
            "run_after",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "worker_id",
            sa.String(100),
            nullable=True,
        ),
        sa.Column(
            "error_message",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "job_type IN ('summary', 'audio')",
            name="ck_background_jobs_type",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'queued', 'running', 'completed', "
            "'failed', 'cancelled')",
            name="ck_background_jobs_status",
        ),
        sa.CheckConstraint(
            "progress BETWEEN 0 AND 100",
            name="ck_background_jobs_progress",
        ),
    )

    op.create_index(
        "ix_background_jobs_queue",
        "background_jobs",
        ["status", "run_after", "created_at"],
    )

    op.create_index(
        "uq_background_jobs_active_project",
        "background_jobs",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('queued', 'running')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_background_jobs_active_project",
        table_name="background_jobs",
    )
    op.drop_index(
        "ix_background_jobs_queue",
        table_name="background_jobs",
    )
    op.drop_table("background_jobs")

