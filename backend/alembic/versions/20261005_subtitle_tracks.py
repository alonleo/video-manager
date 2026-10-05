"""Independent, editable subtitle tracks and translation snapshots."""
from alembic import op
import sqlalchemy as sa

revision = '20261005_subtitle_tracks'
down_revision = 'bce0d231243d'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('subtitle_tracks',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('video_id', sa.Integer(), sa.ForeignKey('videos.id', ondelete='CASCADE'), nullable=False),
        sa.Column('label', sa.Text(), nullable=False),
        sa.Column('language', sa.Text(), nullable=False),
        sa.Column('engine', sa.Text(), nullable=False),
        sa.Column('model', sa.Text()),
        sa.Column('source_track_id', sa.Integer(), sa.ForeignKey('subtitle_tracks.id', ondelete='SET NULL')),
        sa.Column('source_cues', sa.JSON()),
        sa.Column('cues', sa.JSON(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()))
    op.create_index('ix_subtitle_tracks_video_id', 'subtitle_tracks', ['video_id'])


def downgrade():
    op.drop_table('subtitle_tracks')
