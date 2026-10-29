"""Editable dubbing projects, with immutable source identity."""
import sqlalchemy as sa

from alembic import op

revision = "20261006_dubbing"
down_revision = "20261005_trusted_devices"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "dubbing_projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("video_id", sa.Integer(), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("video_sources.id", ondelete="SET NULL")),
        sa.Column("source_fingerprint", sa.JSON(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("cues", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_dubbing_projects_video_id", "dubbing_projects", ["video_id"])


def downgrade():
    op.drop_index("ix_dubbing_projects_video_id", table_name="dubbing_projects")
    op.drop_table("dubbing_projects")
