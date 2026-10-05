"""Add photo entries while preserving existing video IDs and relationships."""
from alembic import op
import sqlalchemy as sa

revision = "20261005_photo_library"
down_revision = "20261005_subtitle_tracks"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("videos", sa.Column("media_type", sa.Text(), nullable=False, server_default="video"))
    op.create_index("ix_videos_media_type", "videos", ["media_type"])


def downgrade():
    op.drop_index("ix_videos_media_type", table_name="videos")
    op.drop_column("videos", "media_type")
