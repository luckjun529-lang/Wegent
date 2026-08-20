# SPDX-FileCopyrightText: 2026 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""Periodic tasks for selectively mirrored plugin releases."""

import logging

from app.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.plugin_marketplace_tasks.sync_plugin_upstreams")
def sync_plugin_upstreams() -> dict[str, int]:
    """Synchronize only explicitly enabled upstream plugin records."""
    from app.core.distributed_lock import distributed_lock
    from app.db.session import get_db_session
    from app.services.plugin_marketplace_service import plugin_marketplace_service

    with distributed_lock.acquire_context(
        "sync_plugin_upstreams", expire_seconds=60 * 60
    ) as acquired:
        if not acquired:
            return {"synced": 0, "skipped": 1}
        with get_db_session() as db:
            items = plugin_marketplace_service.sync_enabled_upstreams(db)
            logger.info("Synchronized %s plugin upstreams", len(items))
            return {"synced": len(items), "skipped": 0}


@celery_app.task(
    bind=True,
    name="app.tasks.plugin_marketplace_tasks.publish_plugin_repository_release",
    max_retries=360,
)
def publish_plugin_repository_release(self, publication_id: int) -> dict[str, int]:
    """Publish one plugin from a commit-pinned managed repository."""
    from app.core.distributed_lock import distributed_lock
    from app.db.session import get_db_session
    from app.models.plugin_marketplace import PluginRepositoryPublication
    from app.services.plugin_repository_service import plugin_repository_service

    with get_db_session() as db:
        publication = db.get(PluginRepositoryPublication, publication_id)
        if not publication:
            return {"published": 0, "missing": 1}
        lock_name = (
            f"plugin_repository_publish:{publication.repository_id}:"
            f"{publication.plugin_slug}"
        )
    with distributed_lock.acquire_context(
        lock_name, expire_seconds=60 * 30
    ) as acquired:
        if not acquired:
            raise self.retry(
                exc=RuntimeError("Plugin publication is already running"), countdown=5
            )
        with get_db_session() as db:
            plugin_repository_service.process_publication(db, publication_id)
            updated = db.get(PluginRepositoryPublication, publication_id)
            published = int(bool(updated and updated.status == "published"))
    return {"published": published, "missing": 0}
