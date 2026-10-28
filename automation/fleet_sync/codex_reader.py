"""Replaceable Codex CLI adapter: ChatGPT login, isolated read-only `codex exec`.

Returns the model's raw JSON; ReadingService chooses retries and validation.
This adapter never modifies fleet data.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any

from .reading_contracts import READER_RESPONSE_SCHEMA, TITLE_TRIAGE_SCHEMA, ReadingInput, validate_reading_input
from .reading_service import (
    CORRECTION_PROMPT, DUPLICATE_CHECK_PROMPT, MODEL, REASONING_EFFORT, SERVICE_TIER, TRIAGE_PROMPT,
    ReaderConfigurationError, ReaderOutputError, ReaderUsageLimited, prompt_for_reading, target_vessel_ids,
)

CLI_CONFIG = (
    'model_provider="openai"',
    f'model_reasoning_effort="{REASONING_EFFORT}"',
    f'service_tier="{SERVICE_TIER}"',
    'approval_policy="never"',
    'features.shell_tool=false',
    'features.apps=false',
    'features.plugins=false',
    'features.memories=false',
    'features.multi_agent=false',
    'features.js_repl=false',
    'skills.bundled.enabled=false',
    'project_doc_max_bytes=0',
    'web_search="disabled"',
)
_BLOCKED_ENV = {"OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"}


class CodexReader:
    def __init__(self, *, timeout: int = 600):
        self.timeout = timeout

    # --- ReadingService adapter API ---------------------------------------

    def read(self, request: ReadingInput) -> Any:
        return self._read(request, correction=None)

    def read_corrected(self, request: ReadingInput, *, previous_response: Any, error: str) -> Any:
        return self._read(request, correction={"error": error, "rejectedOutput": previous_response})

    def read_rechecked(self, request: ReadingInput, *, recheck: dict, correction: dict | None = None) -> Any:
        return self._read(request, correction=correction, recheck=recheck)

    def triage(self, items: list[dict], *, correction: dict | None = None) -> Any:
        schema = deepcopy(TITLE_TRIAGE_SCHEMA)
        ids = [item["id"] for item in items]
        if ids:
            schema["properties"]["selected"]["items"]["enum"] = list(dict.fromkeys(ids))
            schema["properties"]["categories"]["items"]["properties"]["id"]["enum"] = list(dict.fromkeys(ids))
        prompt = TRIAGE_PROMPT + _correction_text(correction)
        prompt += "\nINPUT_JSON:\n" + json.dumps({"items": items}, ensure_ascii=False, allow_nan=False)
        return self._exec(prompt, schema, images=[])

    # --- internals ---------------------------------------------------------

    def _read(self, request: ReadingInput, *, correction: dict | None, recheck: dict | None = None) -> Any:
        validate_reading_input(request)
        schema = _request_schema(request)
        supplied, images, manifest = _strip_page_images(request)
        prompt = prompt_for_reading(request)
        if recheck is not None:
            prompt += DUPLICATE_CHECK_PROMPT + json.dumps(recheck, ensure_ascii=False, default=str) + "\n"
        prompt += _correction_text(correction)
        if manifest:
            prompt += "\nPAGE_IMAGE_ATTACHMENTS_JSON:\n" + json.dumps(manifest, ensure_ascii=False, allow_nan=False)
        prompt += "\nINPUT_JSON:\n" + json.dumps(supplied, ensure_ascii=False, allow_nan=False)
        return self._exec(prompt, schema, images=images)

    def _exec(self, prompt: str, schema: dict, *, images: list[bytes]) -> Any:
        executable = shutil.which("codex")
        if not executable:
            raise RuntimeError("Install Codex CLI and run codex login with ChatGPT")
        env = {key: value for key, value in os.environ.items() if key not in _BLOCKED_ENV}
        _require_chatgpt_login(executable, env)
        with tempfile.TemporaryDirectory(prefix="drillship-codex-") as temp:
            path = Path(temp)
            (path / "schema.json").write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
            command = [executable, "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
                       "--skip-git-repo-check", "--cd", temp, "--model", MODEL]
            for config in CLI_CONFIG:
                command.extend(["-c", config])
            for index, data in enumerate(images, 1):
                image_path = path / f"page-{index:06d}.png"
                image_path.write_bytes(data)
                command.extend(["--image", str(image_path)])
            output = path / "result.json"
            command.extend(["--sandbox", "read-only", "--output-schema", str(path / "schema.json"),
                            "--output-last-message", str(output), "-"])
            result = subprocess.run(command, input=prompt, text=True, capture_output=True, env=env,
                                    timeout=self.timeout)
            if result.returncode:
                limited = usage_limit(result.stderr)
                if limited is not None:
                    raise limited
                error_type = ReaderConfigurationError if _invalid_schema_error(result.stderr) else RuntimeError
                raise error_type(f"Codex reading failed (exit {result.returncode}): {result.stderr[-1200:]}")
            if not output.exists():
                raise ReaderOutputError("Codex returned no output file", raw_output="")
            raw = output.read_text(encoding="utf-8")
            try:
                return json.loads(raw, parse_constant=_reject_constant)
            except (ValueError, RecursionError) as exc:
                raise ReaderOutputError(f"Codex returned invalid JSON: {exc}", raw_output=raw) from exc


def _require_chatgpt_login(executable: str, env: dict) -> None:
    auth = subprocess.run([executable, "login", "status"], env=env, capture_output=True, text=True, timeout=20)
    chatgpt = any(line.strip() == "Logged in using ChatGPT" for line in (auth.stdout + "\n" + auth.stderr).splitlines())
    if auth.returncode or not chatgpt:
        raise RuntimeError("Document reader requires Codex ChatGPT login; API-key auth is not accepted")


def _correction_text(correction: dict | None) -> str:
    if correction is None:
        return ""
    return CORRECTION_PROMPT + json.dumps(correction, ensure_ascii=False, default=str) + "\n"


def _request_schema(request: ReadingInput) -> dict:
    """Wire schema with vesselId limited to this company's registered vessels."""
    schema = deepcopy(READER_RESPONSE_SCHEMA)
    ids = target_vessel_ids(request)
    if ids:
        for collection in ("decisions", "vesselUpdates"):
            schema["properties"][collection]["items"]["properties"]["vesselId"]["enum"] = ids
    return schema


def _strip_page_images(request: ReadingInput) -> tuple[dict, list[bytes], list[dict]]:
    """Request without base64 image data, PNG bytes in page order, and a manifest.

    The manifest maps the CLI attachment order to the page; file names depend
    only on attachment index, never on untrusted locators.
    """
    pages, images, manifest = [], [], []
    for page_index, page in enumerate(request["pages"], 1):
        supplied = dict(page)
        if "image" in page:
            data = base64.b64decode(page["image"]["dataBase64"], validate=True)
            images.append(data)
            sha256 = hashlib.sha256(data).hexdigest()
            manifest.append({"attachmentIndex": len(images), "pageIndex": page_index, "locator": page["locator"]})
            supplied["image"] = {"mimeType": page["image"]["mimeType"], "sha256": sha256,
                                 "attachmentIndex": len(images)}
        pages.append(supplied)
    return {**request, "pages": pages}, images, manifest


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


_ERROR_PREFIX = re.compile(r"^(?:\[[^\]]*\]\s*)?ERROR:\s*")
_USAGE_LINE = re.compile(r"^(?:\W\s*)?you[\u2019']ve hit your usage limit", re.IGNORECASE)


def _cli_error_lines(stderr: str) -> list[str | dict]:
    """CLI error lines/events only, not echoed prompt or document text."""
    messages: list[str | dict] = []
    for line in stderr.splitlines():
        candidate = line.strip()
        prefix = _ERROR_PREFIX.match(candidate)
        if prefix:
            candidate = candidate[prefix.end():]
        if candidate.startswith("{"):
            try:
                event = json.loads(candidate)
            except (ValueError, RecursionError):
                event = None
            if isinstance(event, dict) and event.get("type") == "error":
                messages.append(event)
                continue
        if prefix or _USAGE_LINE.match(candidate):
            messages.append(candidate)
    return messages


def _invalid_schema_error(stderr: str) -> bool:
    """A structured `invalid_json_schema` error event (shared config failure)."""
    return any(isinstance(event, dict) and isinstance(event.get("error"), dict)
               and event["error"].get("code") == "invalid_json_schema"
               for event in _cli_error_lines(stderr))


_USAGE_LIMIT = re.compile(r"usage limit", re.IGNORECASE)
_RETRY_TIME = re.compile(
    r"try again at\s+(?:(?P<month>[A-Za-z]{3,9})\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+(?:(?P<year>\d{4}),?\s+)?)?"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>[AaPp][Mm])", re.IGNORECASE)


def usage_limit(stderr: str, *, now: datetime | None = None) -> ReaderUsageLimited | None:
    """ReaderUsageLimited when Codex stderr reports a usage limit, else None.

    Codex prints e.g. "ERROR: You've hit your usage limit. ... try again at
    8:34 PM." The time is local; the next such moment becomes ``retry_at``.
    """
    for entry in _cli_error_lines(stderr):
        message = entry if isinstance(entry, str) else json.dumps(entry, ensure_ascii=False)
        if _USAGE_LIMIT.search(message):
            return ReaderUsageLimited(f"Codex usage limit reached: {message[:300]}",
                                      retry_at=parse_retry_at(message, now=now))
    return None


def parse_retry_at(message: str, *, now: datetime | None = None) -> datetime | None:
    """Parse "try again at [Mon D[, YYYY]] H:MM AM/PM" as an aware local datetime."""
    match = _RETRY_TIME.search(message)
    if match is None:
        return None
    now = (now or datetime.now()).astimezone()
    hour, minute = int(match["hour"]), int(match["minute"])
    if not (1 <= hour <= 12 and minute < 60):
        return None
    hour = hour % 12 + (12 if match["ampm"].lower() == "pm" else 0)
    if match["month"]:
        try:
            month = datetime.strptime(match["month"][:3].title(), "%b").month
            year = int(match["year"]) if match["year"] else now.year
            moment = now.replace(year=year, month=month, day=int(match["day"]), hour=hour, minute=minute,
                                 second=0, microsecond=0)
        except ValueError:
            return None
        if not match["year"] and moment < now - timedelta(days=1):
            moment = moment.replace(year=year + 1)
        return moment
    moment = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return moment if moment > now else moment + timedelta(days=1)
