"""Independent tag identities and persistent directory membership for each library."""

import json
from pathlib import Path

from alembic import op
import sqlalchemy as sa

revision = "20261005_library_scopes"
down_revision = "20261005_photo_library"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    # The Alembic connection has foreign_keys off (unlike runtime connections).
    # Refuse an unsafe rebuild rather than cascading away associations.
    if conn.exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise RuntimeError("Tag migration requires a dedicated connection with foreign_keys OFF")
    with op.batch_alter_table(
        "tags", naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"}
    ) as batch:
        batch.drop_constraint("uq_tags_name", type_="unique")
        batch.add_column(sa.Column("media_type", sa.Text(), nullable=False, server_default="video"))
        batch.create_unique_constraint("uq_tags_media_name", ["media_type", "name"])
    # Preserve every existing association; a shared old tag becomes two independent tags.
    tags = conn.execute(sa.text("SELECT id, name, color FROM tags")).mappings().all()
    for tag in tags:
        kinds = set(
            conn.execute(
                sa.text(
                    "SELECT DISTINCT v.media_type FROM videos v JOIN video_tags vt ON vt.video_id=v.id WHERE vt.tag_id=:id"
                ),
                {"id": tag["id"]},
            ).scalars()
        )
        if "photo" not in kinds:
            continue
        if "video" not in kinds:
            conn.execute(
                sa.text("UPDATE tags SET media_type='photo' WHERE id=:id"), {"id": tag["id"]}
            )
        else:
            new_id = conn.execute(
                sa.text(
                    "INSERT INTO tags(name,color,media_type) VALUES (:name,:color,'photo') RETURNING id"
                ),
                dict(tag),
            ).scalar_one()
            conn.execute(
                sa.text(
                    "UPDATE video_tags SET tag_id=:new_id WHERE tag_id=:id AND video_id IN (SELECT id FROM videos WHERE media_type='photo')"
                ),
                {"id": tag["id"], "new_id": new_id},
            )
    op.create_table(
        "category_scopes",
        sa.Column(
            "storage_id",
            sa.Integer(),
            sa.ForeignKey("storages.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("path", sa.Text(), primary_key=True),
        sa.Column("media_type", sa.Text(), primary_key=True),
    )
    rows = conn.execute(
        sa.text(
            "SELECT s.id, s.config, vs.local_path, v.media_type FROM video_sources vs JOIN storages s ON s.id=vs.storage_id JOIN videos v ON v.id=vs.video_id WHERE vs.source_type='local'"
        )
    ).mappings()
    for row in rows:
        try:
            config = json.loads(row["config"]) if isinstance(row["config"], str) else row["config"]
            root = Path(config["root"]).expanduser().resolve()
            rel = Path(row["local_path"]).parent.relative_to(root).as_posix()
        except (TypeError, ValueError, KeyError):
            continue
        conn.execute(
            sa.text(
                "INSERT OR IGNORE INTO category_scopes(storage_id,path,media_type) VALUES(:id,:path,:kind)"
            ),
            {"id": row["id"], "path": "" if rel == "." else rel, "kind": row["media_type"]},
        )


def downgrade():
    # An explicit merge is needed for same-name tags before restoring global uniqueness.
    conn = op.get_bind()
    for name in (
        conn.execute(sa.text("SELECT name FROM tags GROUP BY name HAVING count(*)>1"))
        .scalars()
        .all()
    ):
        ids = (
            conn.execute(
                sa.text("SELECT id FROM tags WHERE name=:name ORDER BY id"), {"name": name}
            )
            .scalars()
            .all()
        )
        for old_id in ids[1:]:
            conn.execute(
                sa.text(
                    "INSERT OR IGNORE INTO video_tags(video_id,tag_id) SELECT video_id,:keep FROM video_tags WHERE tag_id=:old"
                ),
                {"keep": ids[0], "old": old_id},
            )
            conn.execute(sa.text("DELETE FROM video_tags WHERE tag_id=:old"), {"old": old_id})
            conn.execute(sa.text("DELETE FROM tags WHERE id=:old"), {"old": old_id})
    with op.batch_alter_table("tags") as batch:
        batch.drop_constraint("uq_tags_media_name", type_="unique")
        batch.drop_column("media_type")
        batch.create_unique_constraint("uq_tags_name", ["name"])
    op.drop_table("category_scopes")
