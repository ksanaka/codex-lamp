"""Fast, fail-open routing for Codex lifecycle hook payloads."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Literal

from .config import Settings, load_settings, resolve_home
from .question import requests_input
from .state import StateStore


@dataclass(frozen=True)
class HookAction:
    """The state operation selected for a Codex hook payload."""

    kind: Literal["update", "remove", "ignore"]
    state: str | None = None


def action_for_payload(payload: dict, settings: Settings) -> HookAction:
    """Map a Codex lifecycle event to its local lamp-state operation."""
    event_name = payload.get("hook_event_name")
    if event_name == "SessionStart":
        return HookAction("update", "idle")
    if event_name in {"UserPromptSubmit", "PreToolUse"}:
        return HookAction("update", "working")
    if event_name == "PermissionRequest":
        return HookAction("update", "input")
    if event_name == "Stop":
        state = "input" if requests_input(payload.get("last_assistant_message"), settings.question_markers) else "idle"
        return HookAction("update", state)
    if event_name == "SessionEnd":
        return HookAction("remove")
    return HookAction("ignore")


def process_payload(
    payload: dict,
    settings: Settings | None = None,
    launch: bool = True,
    now: float | None = None,
    event_time_ns: int | None = None,
) -> None:
    """Persist the selected state transition, then launch the daemon if needed."""
    active_settings = settings or load_settings()
    action = action_for_payload(payload, active_settings)
    if action.kind == "ignore":
        return

    session_id = payload.get("session_id") or "main"
    store = StateStore(active_settings)
    if action.kind == "update":
        store.update(
            session_id,
            action.state or "off",
            time.time_ns() if event_time_ns is None else event_time_ns,
            now=now,
        )
    else:
        store.remove(session_id, now=now)

    if launch:
        ensure_daemon(active_settings)


def ensure_daemon(settings: Settings) -> None:
    """Start a detached daemon that uses the same persistent data root."""
    environment = os.environ.copy()
    environment["CODEX_LAMP_HOME"] = str(settings.home)
    subprocess.Popen(
        [sys.executable, "-m", "codex_lamp.daemon"],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
    )


def main() -> int:
    """Process one hook payload without allowing errors to affect Codex."""
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook payload must be a JSON object")
        process_payload(payload)
    except Exception as error:
        _append_error(error)
    return 0


def _append_error(error: Exception) -> None:
    """Best-effort hook error logging that cannot interfere with Codex."""
    try:
        log_path = Path(resolve_home()) / "logs" / "hook.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"hook error: {type(error).__name__}\n")
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
