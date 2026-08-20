"""Add Git repository driven plugin publication.

Revision ID: b4c5d6e7f809
Revises: 64356fbc03dd
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from shared.models.db.types import big_integer_id_type

revision: str = "b4c5d6e7f809"
down_revision: str | Sequence[str] | None = "64356fbc03dd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bigint = big_integer_id_type()
    op.create_table(
        "plugin_repositories",
        sa.Column("id", bigint, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False, server_default=""),
        sa.Column("provider", sa.String(20), nullable=False, server_default="github"),
        sa.Column("repository_url", sa.String(500), nullable=False, server_default=""),
        sa.Column(
            "visibility", sa.String(20), nullable=False, server_default="workspace"
        ),
        sa.Column("default_ref", sa.String(200), nullable=False, server_default="main"),
        sa.Column(
            "marketplace_path",
            sa.String(300),
            nullable=False,
            server_default=".agents/plugins/marketplace.json",
        ),
        sa.Column("allowed_branch_patterns_json", sa.JSON(), nullable=False),
        sa.Column("allowed_tag_patterns_json", sa.JSON(), nullable=False),
        sa.Column(
            "credential_encrypted", sa.String(4096), nullable=False, server_default=""
        ),
        sa.Column(
            "is_internal", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("created_by_user_id", bigint, nullable=False, server_default="0"),
        sa.Column(
            "last_validated_at",
            sa.DateTime(),
            nullable=False,
            server_default="1970-01-01 00:00:00",
        ),
        sa.Column("last_error", sa.String(1000), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("repository_url", name="uniq_plugin_repository_url"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "idx_plugin_repositories_enabled",
        "plugin_repositories",
        ["is_enabled", "visibility"],
    )
    op.create_table(
        "plugin_repository_publications",
        sa.Column("id", bigint, primary_key=True, autoincrement=True),
        sa.Column("repository_id", bigint, nullable=False, server_default="0"),
        sa.Column("plugin_slug", sa.String(100), nullable=False, server_default=""),
        sa.Column("requested_ref", sa.String(200), nullable=False, server_default=""),
        sa.Column("ref_kind", sa.String(20), nullable=False, server_default="branch"),
        sa.Column("commit_sha", sa.String(64), nullable=False, server_default=""),
        sa.Column("version", sa.String(50), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("requested_by_user_id", bigint, nullable=False, server_default="0"),
        sa.Column("plugin_id", bigint, nullable=False, server_default="0"),
        sa.Column("release_id", bigint, nullable=False, server_default="0"),
        sa.Column("package_sha256", sa.String(64), nullable=False, server_default=""),
        sa.Column("error_code", sa.String(100), nullable=False, server_default=""),
        sa.Column("error_message", sa.String(1000), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(),
            nullable=False,
            server_default="1970-01-01 00:00:00",
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(),
            nullable=False,
            server_default="1970-01-01 00:00:00",
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "idx_plugin_repo_publications_queue",
        "plugin_repository_publications",
        ["status", "created_at"],
    )
    op.create_index(
        "idx_plugin_repo_publications_repo_slug",
        "plugin_repository_publications",
        ["repository_id", "plugin_slug", "created_at"],
    )
    op.create_index(
        "idx_plugin_repo_publications_requester",
        "plugin_repository_publications",
        ["requested_by_user_id", "created_at"],
    )
    op.add_column(
        "plugins",
        sa.Column("source_repository_id", bigint, nullable=True),
    )
    op.create_index(
        "idx_plugins_source_repository", "plugins", ["source_repository_id"]
    )


def downgrade() -> None:
    op.drop_index("idx_plugins_source_repository", table_name="plugins")
    op.drop_column("plugins", "source_repository_id")
    op.drop_index(
        "idx_plugin_repo_publications_requester",
        table_name="plugin_repository_publications",
    )
    op.drop_index(
        "idx_plugin_repo_publications_repo_slug",
        table_name="plugin_repository_publications",
    )
    op.drop_index(
        "idx_plugin_repo_publications_queue",
        table_name="plugin_repository_publications",
    )
    op.drop_table("plugin_repository_publications")
    op.drop_index("idx_plugin_repositories_enabled", table_name="plugin_repositories")
    op.drop_table("plugin_repositories")
