# SPDX-FileCopyrightText: 2026 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""API tests for DingTalk team-file endpoints."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.api.endpoints.dingtalk_team_files import router
from app.core import security
from app.models.dingtalk_doc import DingTalkNodeSource, DingtalkSyncedNode
from app.models.user import User


def _client(test_db: Session, test_user: User) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/dingtalk-team-files")

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[security.get_current_user] = lambda: test_user
    return TestClient(app)


class TestDingTalkTeamFilesApi:
    """Tests for team-file tree and sync endpoints."""

    def test_get_returns_team_file_tree(
        self,
        test_db: Session,
        test_user: User,
    ) -> None:
        now = datetime.now()
        root = DingtalkSyncedNode(
            user_id=test_user.id,
            dingtalk_node_id="root-1",
            name="Engineering",
            doc_url="https://alidocs.dingtalk.com/i/nodes/root-1",
            parent_node_id="",
            node_type="folder",
            workspace_id="1001",
            source=DingTalkNodeSource.TEAM_FILES.value,
            content_updated_at=now,
            last_synced_at=now,
        )
        child = DingtalkSyncedNode(
            user_id=test_user.id,
            dingtalk_node_id="file-1",
            name="Roadmap.pdf",
            doc_url="https://alidocs.dingtalk.com/i/nodes/file-1",
            parent_node_id="root-1",
            node_type="file",
            workspace_id="1001",
            source=DingTalkNodeSource.TEAM_FILES.value,
            content_type="pdf",
            content_updated_at=now,
            last_synced_at=now,
        )
        test_db.add_all([root, child])
        test_db.commit()

        response = _client(test_db, test_user).get("/dingtalk-team-files")

        assert response.status_code == 200
        payload = response.json()
        assert payload["total_count"] == 2
        assert payload["nodes"][0]["name"] == "Engineering"
        assert payload["nodes"][0]["children"][0]["name"] == "Roadmap.pdf"
        assert payload["nodes"][0]["source"] == "team_files"

    def test_sync_returns_400_when_authorization_expired(
        self,
        test_db: Session,
        test_user: User,
    ) -> None:
        with patch(
            "app.api.endpoints.dingtalk_team_files."
            "DingTalkTeamFilesService.sync_team_files",
            new=AsyncMock(side_effect=ValueError("DingTalk is not authorized")),
        ):
            response = _client(test_db, test_user).post("/dingtalk-team-files/sync")

        assert response.status_code == 400
        assert "not authorized" in response.json()["detail"]
