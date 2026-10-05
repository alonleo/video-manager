"""Add trusted-device pairing, challenge and revocable sessions."""

import sqlalchemy as sa

from alembic import op

revision = "20261005_trusted_devices"
down_revision = "20261005_library_scopes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "trusted_devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("public_key", sa.JSON(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("is_bootstrap", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("approved_at", sa.DateTime()),
        sa.Column("last_seen_at", sa.DateTime()),
        sa.Column("revoked_at", sa.DateTime()),
    )
    op.create_index("ix_trusted_devices_status", "trusted_devices", ["status"])
    op.create_index(
        "uq_single_bootstrap_device",
        "trusted_devices",
        ["is_bootstrap"],
        unique=True,
        sqlite_where=sa.text("is_bootstrap = 1"),
        postgresql_where=sa.text("is_bootstrap IS TRUE"),
    )
    op.create_table(
        "device_pairings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("claim_secret_hash", sa.Text(), unique=True),
        sa.Column("verification_code", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_by_device_id",
            sa.Integer(),
            sa.ForeignKey("trusted_devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requested_device_id",
            sa.Integer(),
            sa.ForeignKey("trusted_devices.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime()),
        sa.Column("used_at", sa.DateTime()),
    )
    op.create_index("ix_device_pairings_token_hash", "device_pairings", ["token_hash"])
    op.create_index("ix_device_pairings_status", "device_pairings", ["status"])
    op.create_table(
        "device_auth_challenges",
        sa.Column("challenge_hash", sa.Text(), primary_key=True),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("trusted_devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_device_auth_challenges_device_id", "device_auth_challenges", ["device_id"])
    op.create_table(
        "device_sessions",
        sa.Column("token_hash", sa.Text(), primary_key=True),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("trusted_devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime()),
    )
    op.create_index("ix_device_sessions_device_id", "device_sessions", ["device_id"])


def downgrade():
    op.drop_table("device_sessions")
    op.drop_table("device_auth_challenges")
    op.drop_table("device_pairings")
    op.drop_table("trusted_devices")
