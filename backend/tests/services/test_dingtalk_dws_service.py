# SPDX-FileCopyrightText: 2026 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for DingTalk DWS CLI service helpers."""

from __future__ import annotations

import asyncio
import stat
from unittest.mock import AsyncMock, patch

import pytest

from app.services.dingtalk_dws_service import (
    DingTalkDwsService,
    DwsCommandError,
    DwsCommandResult,
)


class TestDingTalkDwsService:
    """Tests for DWS runner and auth helpers."""

    def test_with_json_format_appends_format_json(self) -> None:
        """All DWS commands are forced into JSON output unless already specified."""
        assert DingTalkDwsService._with_json_format(["auth", "status"]) == [
            "auth",
            "status",
            "--format",
            "json",
        ]
        assert DingTalkDwsService._with_json_format(
            ["auth", "status", "--format", "json"]
        ) == ["auth", "status", "--format", "json"]

    def test_build_env_uses_per_user_config_dir(self, tmp_path, monkeypatch) -> None:
        """DWS_CONFIG_DIR is isolated per Wegent user with 0700 permissions."""
        monkeypatch.setattr(
            "app.services.dingtalk_dws_service.settings.DWS_CONFIG_ROOT",
            str(tmp_path),
        )

        env = DingTalkDwsService._build_env(user_id=42)

        config_dir = tmp_path / "users" / "42"
        assert env["DWS_CONFIG_DIR"] == str(config_dir)
        assert stat.S_IMODE((tmp_path / "users").stat().st_mode) == 0o700
        assert stat.S_IMODE(config_dir.stat().st_mode) == 0o700

    @pytest.mark.asyncio
    async def test_run_terminates_process_when_caller_is_cancelled(self) -> None:
        """A disconnected request must not leave its DWS subprocess running."""

        class BlockingProcess:
            def __init__(self) -> None:
                self.returncode = None
                self.communicate_started = asyncio.Event()
                self.terminated = False

            async def communicate(self):
                self.communicate_started.set()
                await asyncio.Future()

            def terminate(self) -> None:
                self.terminated = True

            def kill(self) -> None:
                self.terminated = True

            async def wait(self) -> int:
                self.returncode = -15
                return self.returncode

        process = BlockingProcess()
        with (
            patch(
                "app.services.dingtalk_dws_service.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=process),
            ),
            patch.object(DingTalkDwsService, "_build_env", return_value={}),
        ):
            task = asyncio.create_task(DingTalkDwsService.run(42, ["auth", "status"]))
            await process.communicate_started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert process.terminated is True
        assert process.returncode == -15

    def test_parse_json_stdout_after_progress_lines(self) -> None:
        """Pretty-printed JSON remains parseable after CLI pagination progress."""
        output = """[page 1] fetching...
[pagination] done
{
  "data": {
    "records": [{"recordId": "record-1"}],
    "hasMore": false
  }
}
"""

        assert DingTalkDwsService._parse_json_stdout(output) == {
            "data": {
                "records": [{"recordId": "record-1"}],
                "hasMore": False,
            }
        }

    def test_parse_json_stdout_keeps_prefixed_array_root(self) -> None:
        """Nested objects do not replace a complete JSON array root."""
        output = '[page 1] fetching...\n[\n  {"id": "one"},\n  {"id": "two"}\n]'

        assert DingTalkDwsService._parse_json_stdout(output) == [
            {"id": "one"},
            {"id": "two"},
        ]

    @pytest.mark.asyncio
    async def test_run_extracts_structured_dws_error(self) -> None:
        """Pretty-printed DWS errors expose their message and server code."""

        class FailedProcess:
            returncode = 1

            async def communicate(self):
                return (
                    b"",
                    """{
  "error": {
    "message": "Access denied to DingTalk drive space",
    "server_error_code": "forbidden.accessDenied"
  }
}
""".encode(),
                )

        with (
            patch(
                "app.services.dingtalk_dws_service.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=FailedProcess()),
            ),
            patch.object(DingTalkDwsService, "_build_env", return_value={}),
        ):
            with pytest.raises(DwsCommandError) as error:
                await DingTalkDwsService.run(42, ["drive", "list"])

        assert str(error.value) == "Access denied to DingTalk drive space"
        assert error.value.server_error_code == "forbidden.accessDenied"
        assert DingTalkDwsService.is_access_denied_error(error.value) is True

    @pytest.mark.asyncio
    async def test_auth_status_authenticated(self) -> None:
        """Authenticated DWS payloads are normalized for API callers."""
        with patch.object(
            DingTalkDwsService,
            "run",
            new=AsyncMock(
                return_value=DwsCommandResult(
                    data={"authenticated": True},
                    stdout='{"authenticated":true}',
                    stderr="",
                    returncode=0,
                )
            ),
        ) as mock_run:
            status = await DingTalkDwsService.auth_status(user_id=42)

        assert status == {
            "is_authenticated": True,
            "auth_status": "authenticated",
        }
        mock_run.assert_awaited_once_with(42, ["auth", "status"], timeout=20)

    @pytest.mark.asyncio
    async def test_auth_status_unauthenticated_from_error_output(self) -> None:
        """DWS login errors with 未登录 are treated as unauthenticated."""
        with patch.object(
            DingTalkDwsService,
            "run",
            new=AsyncMock(
                side_effect=DwsCommandError(
                    "not logged in",
                    stdout='{"message":"未登录"}',
                )
            ),
        ):
            status = await DingTalkDwsService.auth_status(user_id=42)

        assert status == {
            "is_authenticated": False,
            "auth_status": "unauthenticated",
        }

    @pytest.mark.asyncio
    async def test_auth_status_task_is_cleaned_after_only_waiter_cancels(self) -> None:
        """Shared auth work is removed when it finishes after a caller disconnects."""
        user_id = 42001
        started = asyncio.Event()
        release = asyncio.Event()

        async def read_status(_: int) -> dict[str, object]:
            started.set()
            await release.wait()
            return {"is_authenticated": True, "auth_status": "authenticated"}

        with patch.object(
            DingTalkDwsService,
            "_read_auth_status",
            side_effect=read_status,
        ):
            task = asyncio.create_task(DingTalkDwsService.auth_status(user_id))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            release.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        assert user_id not in DingTalkDwsService._auth_status_tasks

    @pytest.mark.asyncio
    async def test_device_login_start_is_not_blocked_by_another_user(self) -> None:
        """Waiting for one device URL does not serialize every user's login flow."""

        class PendingProcess:
            def __init__(self, name: str) -> None:
                self.name = name
                self.returncode = None
                self.stdout = None

            def terminate(self) -> None:
                self.returncode = -15

            def kill(self) -> None:
                self.returncode = -9

            async def wait(self) -> int:
                return self.returncode or 0

        first_process = PendingProcess("first")
        second_process = PendingProcess("second")
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def read_payload(process: PendingProcess):
            if process is first_process:
                first_started.set()
                await release_first.wait()
            return (
                "",
                {
                    "verification_url": (
                        "https://login.dingtalk.com/oauth2/device/"
                        f"verify.htm?user_code={process.name.upper()}-CODE"
                    ),
                    "user_code": f"{process.name.upper()}-CODE",
                },
            )

        async def monitor(_session) -> None:
            return None

        user_ids = (42002, 42003)
        try:
            with (
                patch.object(
                    DingTalkDwsService,
                    "auth_status",
                    new=AsyncMock(
                        return_value={
                            "is_authenticated": False,
                            "auth_status": "unauthenticated",
                        }
                    ),
                ),
                patch.object(DingTalkDwsService, "_build_env", return_value={}),
                patch(
                    "app.services.dingtalk_dws_service.asyncio.create_subprocess_exec",
                    new=AsyncMock(side_effect=[first_process, second_process]),
                ),
                patch.object(
                    DingTalkDwsService,
                    "_read_device_login_payload",
                    side_effect=read_payload,
                ),
                patch.object(
                    DingTalkDwsService,
                    "_monitor_device_login",
                    side_effect=monitor,
                ),
            ):
                first = asyncio.create_task(
                    DingTalkDwsService.start_device_login(user_ids[0])
                )
                await first_started.wait()
                second = await asyncio.wait_for(
                    DingTalkDwsService.start_device_login(user_ids[1]),
                    timeout=0.5,
                )
                assert second["user_code"] == "SECOND-CODE"
                release_first.set()
                first_result = await first
                assert first_result["user_code"] == "FIRST-CODE"
        finally:
            for user_id in user_ids:
                DingTalkDwsService._device_sessions.pop(user_id, None)
                DingTalkDwsService._device_locks.pop(user_id, None)

    def test_extract_device_login_payload_from_json(self) -> None:
        """Device-login payloads can be parsed from JSON output."""
        payload = DingTalkDwsService._extract_device_login_payload(
            '{"verification_url":"https://login.dingtalk.com/oauth2/device/'
            'verify.htm?user_code=GLLM-GXKH","user_code":"GLLM-GXKH"}'
        )

        assert payload == {
            "verification_url": (
                "https://login.dingtalk.com/oauth2/device/"
                "verify.htm?user_code=GLLM-GXKH"
            ),
            "user_code": "GLLM-GXKH",
        }

    def test_extract_device_login_payload_from_human_output(self) -> None:
        """Device-login payloads can be parsed from human-readable DWS output."""
        payload = DingTalkDwsService._extract_device_login_payload(
            "Open https://login.dingtalk.com/oauth2/device/"
            "verify.htm?user_code=ABCD-EFGH to continue"
        )

        assert payload == {
            "verification_url": (
                "https://login.dingtalk.com/oauth2/device/"
                "verify.htm?user_code=ABCD-EFGH"
            ),
            "user_code": "ABCD-EFGH",
        }

    def test_rejects_nonstandard_device_verification_url(self) -> None:
        """Device login links cannot redirect through credentials or custom ports."""
        assert (
            DingTalkDwsService._validated_device_verification_url(
                "https://user@login.dingtalk.com/oauth2/device/verify.htm"
            )
            is None
        )
        assert (
            DingTalkDwsService._validated_device_verification_url(
                "https://login.dingtalk.com:8443/oauth2/device/verify.htm"
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_sheet_list_uses_dws_sheet_command(self) -> None:
        """Worksheet discovery uses the online spreadsheet command surface."""
        result = DwsCommandResult(
            data={"data": {"sheets": [{"sheetId": "sheet-1"}]}},
            stdout="",
            stderr="",
            returncode=0,
        )
        with patch.object(
            DingTalkDwsService,
            "run",
            new=AsyncMock(return_value=result),
        ) as mock_run:
            sheets = await DingTalkDwsService.sheet_list(42, "node-1")

        assert sheets == [{"sheetId": "sheet-1"}]
        mock_run.assert_awaited_once_with(
            42,
            ["sheet", "list", "--node", "node-1"],
        )

    @pytest.mark.asyncio
    async def test_sheet_csv_get_uses_sheet_and_size_limit(self) -> None:
        """Worksheet reads use csv-get with an explicit output-size limit."""
        result = DwsCommandResult(
            data={"result": {"csv": "[row=1]Name", "hasMore": False}},
            stdout="",
            stderr="",
            returncode=0,
        )
        with patch.object(
            DingTalkDwsService,
            "run",
            new=AsyncMock(return_value=result),
        ) as mock_run:
            payload = await DingTalkDwsService.sheet_csv_get(
                42,
                "node-1",
                "sheet-1",
                max_chars=12345,
            )

        assert payload == {"csv": "[row=1]Name", "hasMore": False}
        mock_run.assert_awaited_once_with(
            42,
            [
                "sheet",
                "csv-get",
                "--node",
                "node-1",
                "--sheet-id",
                "sheet-1",
                "--max-chars",
                "12345",
            ],
        )

    @pytest.mark.asyncio
    async def test_doc_download_uses_output_path_and_extended_timeout(self) -> None:
        """Binary file downloads use an explicit backend-local output path."""
        result = DwsCommandResult(
            data={"success": True}, stdout="", stderr="", returncode=0
        )
        with (
            patch.object(
                DingTalkDwsService,
                "run",
                new=AsyncMock(return_value=result),
            ) as mock_run,
            patch(
                "app.services.dingtalk_dws_service.settings.DWS_COMMAND_TIMEOUT_SECONDS",
                60,
            ),
        ):
            payload = await DingTalkDwsService.doc_download(
                42,
                "node-1",
                "/tmp/report.pdf",
            )

        assert payload == {"success": True}
        mock_run.assert_awaited_once_with(
            42,
            [
                "doc",
                "download",
                "--node",
                "node-1",
                "--output",
                "/tmp/report.pdf",
            ],
            timeout=300,
        )

    @pytest.mark.asyncio
    async def test_list_drive_nodes_uses_space_folder_and_cursor(self) -> None:
        """Team-drive traversal preserves space, folder, and pagination IDs."""
        result = DwsCommandResult(
            data={
                "result": {"dentryList": [{"fileId": "file-1"}], "nextToken": "next"}
            },
            stdout="",
            stderr="",
            returncode=0,
        )
        with patch.object(
            DingTalkDwsService,
            "run",
            new=AsyncMock(return_value=result),
        ) as mock_run:
            items, cursor = await DingTalkDwsService.list_drive_nodes(
                42,
                space_id="1001",
                folder_id="folder-1",
                cursor="page-1",
            )

        assert items == [{"fileId": "file-1"}]
        assert cursor == "next"
        mock_run.assert_awaited_once_with(
            42,
            [
                "drive",
                "list",
                "--space-id",
                "1001",
                "--limit",
                "50",
                "--folder",
                "folder-1",
                "--cursor",
                "page-1",
            ],
        )

    @pytest.mark.asyncio
    async def test_drive_info_and_download_include_space_id(self) -> None:
        """Drive metadata and downloads can target the owning team space."""
        results = [
            DwsCommandResult(
                data={"result": {"extension": "pdf"}},
                stdout="",
                stderr="",
                returncode=0,
            ),
            DwsCommandResult(
                data={"success": True},
                stdout="",
                stderr="",
                returncode=0,
            ),
        ]
        with (
            patch.object(
                DingTalkDwsService,
                "run",
                new=AsyncMock(side_effect=results),
            ) as mock_run,
            patch(
                "app.services.dingtalk_dws_service.settings.DWS_COMMAND_TIMEOUT_SECONDS",
                60,
            ),
        ):
            info = await DingTalkDwsService.drive_info(
                42,
                "file-1",
                space_id="1001",
            )
            downloaded = await DingTalkDwsService.drive_download(
                42,
                "file-1",
                "/tmp/report.pdf",
                space_id="1001",
            )

        assert info == {"extension": "pdf"}
        assert downloaded == {"success": True}
        assert mock_run.await_args_list[0].args == (
            42,
            ["drive", "info", "--node", "file-1", "--space-id", "1001"],
        )
        assert mock_run.await_args_list[1].args == (
            42,
            [
                "drive",
                "download",
                "--node",
                "file-1",
                "--output",
                "/tmp/report.pdf",
                "--space-id",
                "1001",
            ],
        )
        assert mock_run.await_args_list[1].kwargs == {"timeout": 300}

    @pytest.mark.asyncio
    async def test_aitable_helpers_use_base_table_and_record_commands(self) -> None:
        """AI table helpers preserve IDs and bounded auto-pagination flags."""
        results = [
            DwsCommandResult(
                data={"result": {"tables": [{"tableId": "table-1"}]}},
                stdout="",
                stderr="",
                returncode=0,
            ),
            DwsCommandResult(
                data={"data": {"tables": [{"tableId": "table-1"}]}},
                stdout="",
                stderr="",
                returncode=0,
            ),
            DwsCommandResult(
                data={"data": {"records": [], "hasMore": False}},
                stdout="",
                stderr="",
                returncode=0,
            ),
        ]
        with (
            patch.object(
                DingTalkDwsService,
                "run",
                new=AsyncMock(side_effect=results),
            ) as mock_run,
            patch(
                "app.services.dingtalk_dws_service.settings.DWS_COMMAND_TIMEOUT_SECONDS",
                60,
            ),
        ):
            base = await DingTalkDwsService.aitable_base_get(42, "base-1")
            tables = await DingTalkDwsService.aitable_table_get(
                42,
                "base-1",
                ["table-1"],
            )
            records = await DingTalkDwsService.aitable_record_query(
                42,
                "base-1",
                "table-1",
                field_ids=["field-1", "field-2"],
                page_limit=25,
            )

        assert base == {"tables": [{"tableId": "table-1"}]}
        assert tables == [{"tableId": "table-1"}]
        assert records == {"records": [], "hasMore": False}
        assert mock_run.await_args_list[0].args == (
            42,
            ["aitable", "base", "get", "--base-id", "base-1"],
        )
        assert mock_run.await_args_list[1].args == (
            42,
            [
                "aitable",
                "table",
                "get",
                "--base-id",
                "base-1",
                "--table-ids",
                "table-1",
            ],
        )
        assert mock_run.await_args_list[2].args == (
            42,
            [
                "aitable",
                "record",
                "query",
                "--base-id",
                "base-1",
                "--table-id",
                "table-1",
                "--limit",
                "100",
                "--all",
                "--page-limit",
                "25",
                "--field-ids",
                "field-1,field-2",
            ],
        )
        assert mock_run.await_args_list[2].kwargs == {"timeout": 300}

    @pytest.mark.asyncio
    async def test_aitable_query_extends_timeout_for_large_page_limit(self) -> None:
        """A 100,000-record query has enough time to fetch 1,000 pages."""
        result = DwsCommandResult(
            data={"data": {"records": [], "hasMore": False}},
            stdout="",
            stderr="",
            returncode=0,
        )
        with (
            patch.object(
                DingTalkDwsService,
                "run",
                new=AsyncMock(return_value=result),
            ) as mock_run,
            patch(
                "app.services.dingtalk_dws_service.settings.DWS_COMMAND_TIMEOUT_SECONDS",
                60,
            ),
        ):
            await DingTalkDwsService.aitable_record_query(
                42,
                "base-1",
                "table-1",
                page_limit=1000,
            )

        assert mock_run.await_args.kwargs == {"timeout": 2000}

    @pytest.mark.asyncio
    async def test_aitable_query_passes_cursor_to_dws(self) -> None:
        """Chunked AI-table reads resume from the cursor returned by DWS."""
        result = DwsCommandResult(
            data={"data": {"records": [], "hasMore": False}},
            stdout="",
            stderr="",
            returncode=0,
        )
        with patch.object(
            DingTalkDwsService,
            "run",
            new=AsyncMock(return_value=result),
        ) as mock_run:
            await DingTalkDwsService.aitable_record_query(
                42,
                "base-1",
                "table-1",
                cursor="next-page",
            )

        assert mock_run.await_args.args == (
            42,
            [
                "aitable",
                "record",
                "query",
                "--base-id",
                "base-1",
                "--table-id",
                "table-1",
                "--limit",
                "100",
                "--all",
                "--page-limit",
                "50",
                "--cursor",
                "next-page",
            ],
        )
