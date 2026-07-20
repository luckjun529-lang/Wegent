# SPDX-FileCopyrightText: 2026 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""DingTalk DWS CLI integration.

The backend owns DingTalk authentication and document reads. Credentials are
isolated per Wegent user through DWS_CONFIG_DIR and are never passed to
executors or sandboxes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.core.config import settings

logger = logging.getLogger(__name__)

DEVICE_VERIFY_URL_RE = re.compile(
    r"https://login\.dingtalk\.com/oauth2/device/verify\.htm\?[^\s\"'<>]+"
)
USER_CODE_RE = re.compile(r"\b([A-Z0-9]{4,}-[A-Z0-9]{4,})\b")


class DwsCommandError(RuntimeError):
    """Raised when a DWS command cannot be executed or parsed."""

    def __init__(
        self,
        message: str,
        *,
        returncode: int | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@dataclass
class DwsCommandResult:
    """Parsed DWS command result."""

    data: Any
    stdout: str
    stderr: str
    returncode: int


@dataclass
class DeviceLoginSession:
    """In-memory state for a single DWS device authorization flow."""

    user_id: int
    session_id: str
    process: asyncio.subprocess.Process
    verification_url: str
    user_code: str
    created_at: datetime
    expires_at: datetime
    status: str = "pending"
    error: str | None = None
    output: str = ""
    monitor_task: asyncio.Task[None] | None = field(default=None, repr=False)


class DingTalkDwsService:
    """Run DWS commands and manage per-user device-login sessions."""

    _device_sessions: dict[int, DeviceLoginSession] = {}
    _device_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Command runner
    # ------------------------------------------------------------------

    @classmethod
    async def run(
        cls,
        user_id: int,
        args: list[str],
        *,
        timeout: int | None = None,
    ) -> DwsCommandResult:
        """Run a DWS command for a user and parse its JSON output."""
        argv = [settings.DWS_BIN, *cls._with_json_format(args)]
        env = cls._build_env(user_id)
        command_timeout = timeout or settings.DWS_COMMAND_TIMEOUT_SECONDS

        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            raise DwsCommandError(f"DWS binary not found: {settings.DWS_BIN}") from exc

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=command_timeout,
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise DwsCommandError(
                "DWS command timed out",
                returncode=process.returncode,
            ) from exc

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if process.returncode != 0:
            raise DwsCommandError(
                cls._safe_error_message(stderr or stdout or "DWS command failed"),
                returncode=process.returncode,
                stdout=stdout,
                stderr=stderr,
            )

        return DwsCommandResult(
            data=cls._parse_json_stdout(stdout),
            stdout=stdout,
            stderr=stderr,
            returncode=process.returncode,
        )

    @classmethod
    def _build_env(cls, user_id: int) -> dict[str, str]:
        env = os.environ.copy()
        config_dir = cls._user_config_dir(user_id)
        env["DWS_CONFIG_DIR"] = str(config_dir)
        env.setdefault("NO_COLOR", "1")
        return env

    @staticmethod
    def _user_config_dir(user_id: int) -> Path:
        root = Path(settings.DWS_CONFIG_ROOT)
        user_dir = root / "users" / str(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(user_dir, 0o700)
        return user_dir

    @staticmethod
    def _with_json_format(args: list[str]) -> list[str]:
        if "--format" in args or "-f" in args:
            return args
        return [*args, "--format", "json"]

    @staticmethod
    def _safe_error_message(raw: str) -> str:
        message = (raw or "").strip().splitlines()
        if not message:
            return "DWS command failed"
        return message[-1][:500]

    @staticmethod
    def _parse_json_stdout(stdout: str) -> Any:
        raw = stdout.strip()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # DWS should emit pure JSON with --format json. This fallback handles
        # occasional human-readable preface lines without treating them as API.
        for line in reversed(raw.splitlines()):
            candidate = line.strip()
            if not candidate:
                continue
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

        raise DwsCommandError("DWS command returned non-JSON output", stdout=stdout)

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    @classmethod
    async def auth_status(cls, user_id: int) -> dict[str, Any]:
        """Return normalized DingTalk auth status for a user."""
        try:
            result = await cls.run(user_id, ["auth", "status"], timeout=20)
        except DwsCommandError as exc:
            raw = f"{exc.stdout}\n{exc.stderr}".lower()
            if any(marker in raw for marker in ("未登录", "not login", "not logged")):
                return {
                    "is_authenticated": False,
                    "auth_status": "unauthenticated",
                }
            return {
                "is_authenticated": False,
                "auth_status": "error",
                "error": str(exc),
            }

        authenticated = cls._is_authenticated_payload(result.data)
        return {
            "is_authenticated": authenticated,
            "auth_status": "authenticated" if authenticated else "unauthenticated",
        }

    @classmethod
    async def start_device_login(cls, user_id: int) -> dict[str, Any]:
        """Start a DWS device-login flow for a user."""
        status = await cls.auth_status(user_id)
        if status["is_authenticated"]:
            return status

        async with cls._device_lock:
            existing = cls._device_sessions.get(user_id)
            if existing and existing.status == "pending":
                await cls._cancel_session(existing, "replaced")

            env = cls._build_env(user_id)
            argv = [
                settings.DWS_BIN,
                *cls._with_json_format(["auth", "login", "--device"]),
            ]
            try:
                process = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=env,
                )
            except FileNotFoundError as exc:
                raise DwsCommandError(
                    f"DWS binary not found: {settings.DWS_BIN}"
                ) from exc

            try:
                output, payload = await cls._read_device_login_payload(process)
            except Exception:
                await cls._terminate_process(process)
                raise
            session_id = uuid.uuid4().hex
            session = DeviceLoginSession(
                user_id=user_id,
                session_id=session_id,
                process=process,
                verification_url=payload["verification_url"],
                user_code=payload["user_code"],
                created_at=datetime.now(),
                expires_at=datetime.now()
                + timedelta(seconds=settings.DWS_DEVICE_LOGIN_TIMEOUT_SECONDS),
                output=output,
            )
            session.monitor_task = asyncio.create_task(
                cls._monitor_device_login(session)
            )
            cls._device_sessions[user_id] = session

        return cls._device_session_response(session)

    @classmethod
    async def get_device_login_status(
        cls,
        user_id: int,
        session_id: str,
    ) -> dict[str, Any]:
        """Return status for an active or recently completed device-login flow."""
        async with cls._device_lock:
            session = cls._device_sessions.get(user_id)
            if not session or session.session_id != session_id:
                raise ValueError("Device login session not found")

            if session.status == "pending" and datetime.now() > session.expires_at:
                await cls._cancel_session(session, "timeout")

            return cls._device_session_response(session)

    @classmethod
    async def _read_device_login_payload(
        cls,
        process: asyncio.subprocess.Process,
    ) -> tuple[str, dict[str, str]]:
        if process.stdout is None:
            raise DwsCommandError("DWS device login did not expose stdout")

        output = ""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 30

        while loop.time() < deadline:
            remaining = max(0.1, deadline - loop.time())
            try:
                chunk = await asyncio.wait_for(process.stdout.read(4096), remaining)
            except asyncio.TimeoutError as exc:
                raise DwsCommandError(
                    "Timed out waiting for DingTalk device authorization URL",
                    stdout=output,
                ) from exc

            if not chunk:
                if process.returncode is None:
                    await process.wait()
                raise DwsCommandError(
                    "DWS device login exited before returning an authorization URL",
                    returncode=process.returncode,
                    stdout=output,
                )

            output += chunk.decode("utf-8", errors="replace")
            payload = cls._extract_device_login_payload(output)
            if payload:
                return output, payload

        raise DwsCommandError(
            "Timed out waiting for DingTalk device authorization URL",
            stdout=output,
        )

    @classmethod
    async def _monitor_device_login(cls, session: DeviceLoginSession) -> None:
        try:
            try:
                remaining_output = ""
                if session.process.stdout is not None:
                    rest = await asyncio.wait_for(
                        session.process.stdout.read(),
                        timeout=settings.DWS_DEVICE_LOGIN_TIMEOUT_SECONDS,
                    )
                    remaining_output = rest.decode("utf-8", errors="replace")
                await asyncio.wait_for(
                    session.process.wait(),
                    timeout=settings.DWS_DEVICE_LOGIN_TIMEOUT_SECONDS,
                )
                session.output += remaining_output
            except asyncio.TimeoutError:
                await cls._cancel_session(session, "timeout")
                return

            if session.process.returncode == 0:
                status = await cls.auth_status(session.user_id)
                if status.get("is_authenticated"):
                    session.status = "authenticated"
                    session.error = None
                else:
                    session.status = "error"
                    session.error = "DingTalk authorization did not complete"
            else:
                session.status = "error"
                session.error = cls._safe_error_message(session.output)
        except asyncio.CancelledError:
            await cls._terminate_process(session.process)
            raise
        except Exception as exc:
            logger.warning("DWS device login monitor failed: %s", exc)
            session.status = "error"
            session.error = str(exc)

    @classmethod
    async def _cancel_session(
        cls,
        session: DeviceLoginSession,
        reason: str,
    ) -> None:
        current_task = asyncio.current_task()
        if (
            session.monitor_task
            and not session.monitor_task.done()
            and session.monitor_task is not current_task
        ):
            session.monitor_task.cancel()
        await cls._terminate_process(session.process)
        session.status = "timeout" if reason == "timeout" else "cancelled"
        session.error = (
            "DingTalk authorization timed out" if reason == "timeout" else None
        )

    @staticmethod
    async def _terminate_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    @staticmethod
    def _device_session_response(session: DeviceLoginSession) -> dict[str, Any]:
        return {
            "is_authenticated": session.status == "authenticated",
            "auth_status": session.status,
            "verification_url": session.verification_url,
            "user_code": session.user_code,
            "session_id": session.session_id,
            "error": session.error,
        }

    @classmethod
    def _extract_device_login_payload(cls, output: str) -> dict[str, str] | None:
        for candidate in cls._json_candidates(output):
            if not isinstance(candidate, dict):
                continue
            url = (
                candidate.get("verification_url")
                or candidate.get("verification_uri")
                or candidate.get("verificationUrl")
                or candidate.get("url")
            )
            user_code = (
                candidate.get("user_code")
                or candidate.get("userCode")
                or candidate.get("code")
            )
            nested = candidate.get("result") or candidate.get("data")
            if (not url or not user_code) and isinstance(nested, dict):
                nested_payload = cls._extract_device_login_payload(json.dumps(nested))
                if nested_payload:
                    return nested_payload
            if isinstance(url, str):
                code_from_url = cls._user_code_from_url(url)
                if not user_code and code_from_url:
                    user_code = code_from_url
                if isinstance(user_code, str) and user_code:
                    return {"verification_url": url, "user_code": user_code}

        url_match = DEVICE_VERIFY_URL_RE.search(output)
        if not url_match:
            return None
        url = url_match.group(0)
        user_code = cls._user_code_from_url(url)
        if not user_code:
            code_match = USER_CODE_RE.search(output)
            user_code = code_match.group(1) if code_match else ""
        if not user_code:
            return None
        return {"verification_url": url, "user_code": user_code}

    @staticmethod
    def _json_candidates(output: str) -> list[Any]:
        candidates: list[Any] = []
        raw = output.strip()
        if not raw:
            return candidates
        chunks = [raw, *raw.splitlines()]
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk.startswith("{"):
                continue
            try:
                candidates.append(json.loads(chunk))
            except json.JSONDecodeError:
                continue
        return candidates

    @staticmethod
    def _user_code_from_url(url: str) -> str | None:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        values = query.get("user_code") or query.get("userCode")
        if values and values[0]:
            return values[0]
        return None

    @staticmethod
    def _is_authenticated_payload(data: Any) -> bool:
        if isinstance(data, dict):
            authenticated = data.get("authenticated") or data.get("is_authenticated")
            if isinstance(authenticated, bool):
                return authenticated
            status = str(data.get("status") or data.get("auth_status") or "").lower()
            if status in {"authenticated", "logged_in", "login", "success"}:
                return True
            if status in {"unauthenticated", "not_logged_in", "error"}:
                return False
            if data.get("accessToken") or data.get("access_token") or data.get("token"):
                return True
            profiles = data.get("profiles") or data.get("items")
            if isinstance(profiles, list) and profiles:
                return True
            nested = data.get("result") or data.get("data")
            if nested is not None and nested is not data:
                return DingTalkDwsService._is_authenticated_payload(nested)
            message = str(data.get("message") or "").lower()
            if "未登录" in message or "not logged" in message:
                return False
        return False

    # ------------------------------------------------------------------
    # Product helpers
    # ------------------------------------------------------------------

    @classmethod
    async def list_spaces(cls, user_id: int, space_type: str) -> list[dict[str, Any]]:
        """List all spaces for a DWS wiki space type."""
        spaces: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            args = [
                "wiki",
                "space",
                "list",
                "--type",
                space_type,
                "--limit",
                "50",
            ]
            if cursor:
                args.extend(["--cursor", cursor])
            result = await cls.run(user_id, args)
            spaces.extend(cls.extract_items(result.data))
            cursor = cls.extract_next_cursor(result.data)
            if not cursor:
                break
        return spaces

    @classmethod
    async def list_nodes(
        cls,
        user_id: int,
        *,
        workspace_id: str,
        folder_id: str | None = None,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List one page of nodes under a workspace or folder."""
        args = [
            "wiki",
            "node",
            "list",
            "--workspace",
            workspace_id,
            "--limit",
            "50",
        ]
        if folder_id:
            args.extend(["--folder", folder_id])
        if cursor:
            args.extend(["--cursor", cursor])
        result = await cls.run(user_id, args)
        return cls.extract_items(result.data), cls.extract_next_cursor(result.data)

    @classmethod
    async def doc_info(cls, user_id: int, node: str) -> dict[str, Any]:
        result = await cls.run(user_id, ["doc", "info", "--node", node])
        info = cls.unwrap_payload(result.data)
        return info if isinstance(info, dict) else {}

    @classmethod
    async def doc_read(cls, user_id: int, node: str) -> Any:
        result = await cls.run(user_id, ["doc", "read", "--node", node])
        return cls.unwrap_payload(result.data)

    @classmethod
    def extract_items(cls, data: Any) -> list[dict[str, Any]]:
        payload = cls.unwrap_payload(data)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []

        for key in (
            "items",
            "nodes",
            "wikiSpaces",
            "spaces",
            "spaceList",
            "records",
            "list",
        ):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]

        for key in ("result", "data", "body"):
            value = payload.get(key)
            if value is not None and value is not payload:
                items = cls.extract_items(value)
                if items:
                    return items
        return []

    @classmethod
    def extract_next_cursor(cls, data: Any) -> str | None:
        payload = cls.unwrap_payload(data)
        if not isinstance(payload, dict):
            return None
        for key in (
            "nextPageToken",
            "next_page_token",
            "nextCursor",
            "next_cursor",
            "nextToken",
            "pageToken",
            "cursor",
        ):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        for key in ("result", "data", "body"):
            value = payload.get(key)
            if value is not None and value is not payload:
                cursor = cls.extract_next_cursor(value)
                if cursor:
                    return cursor
        return None

    @classmethod
    def unwrap_payload(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key in ("result", "data", "body"):
                value = data.get(key)
                if value is not None:
                    return cls.unwrap_payload(value)
        return data


dingtalk_dws_service = DingTalkDwsService
