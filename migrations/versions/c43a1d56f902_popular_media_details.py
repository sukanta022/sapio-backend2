
"""Popular-summary selectable metadata (media paths already exist).

Revision ID: c43a1d56f902
Revises: 6d29f33f1181
"""
from alembic import op
import sqlalchemy as sa

revision = "c43a1d56f902"
down_revision = "6d29f33f1181"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("popular_summaries", sa.Column("summary_type_id", sa.Integer(), nullable=True))
    op.add_column("popular_summaries", sa.Column("audio_format_id", sa.Integer(), nullable=True))
    op.add_column("popular_summaries", sa.Column("duration_seconds", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_popular_summary_type", "popular_summaries", "summary_types", ["summary_type_id"], ["id"])
    op.create_foreign_key("fk_popular_audio_format", "popular_summaries", "audio_formats", ["audio_format_id"], ["id"])
    op.create_check_constraint("ck_popular_duration_nonnegative", "popular_summaries", "duration_seconds IS NULL OR duration_seconds >= 0")


def downgrade() -> None:
    op.drop_constraint("ck_popular_duration_nonnegative", "popular_summaries", type_="check")
    op.drop_constraint("fk_popular_audio_format", "popular_summaries", type_="foreignkey")
    op.drop_constraint("fk_popular_summary_type", "popular_summaries", type_="foreignkey")
    op.drop_column("popular_summaries", "duration_seconds")
    op.drop_column("popular_summaries", "audio_format_id")
    op.drop_column("popular_summaries", "summary_type_id")
