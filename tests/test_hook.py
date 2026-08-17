import io
import os
from pathlib import Path
import subprocess
import sys

from codex_lamp.config import load_settings
from codex_lamp.hook import action_for_payload, ensure_daemon, main, process_payload
from codex_lamp.state import StateStore


HOOK_RUNNER = Path(__file__).resolve().parents[1] / "plugins" / "codex-lamp" / "scripts" / "hook_runner.sh"


def test_maps_codex_events(tmp_path):
    # Break caught: routing a lifecycle event to the wrong session state or failing to remove ended sessions.
    settings = load_settings(tmp_path)

    assert action_for_payload({"hook_event_name": "SessionStart"}, settings).state == "idle"
    assert action_for_payload({"hook_event_name": "UserPromptSubmit"}, settings).state == "working"
    assert action_for_payload({"hook_event_name": "PreToolUse"}, settings).state == "working"
    assert action_for_payload({"hook_event_name": "PermissionRequest"}, settings).state == "input"
    assert action_for_payload({"hook_event_name": "SessionEnd"}, settings).kind == "remove"


def test_stop_uses_last_assistant_message(tmp_path):
    # Break caught: treating a reply that asks the user a question as an idle completion.
    settings = load_settings(tmp_path)
    payload = {"hook_event_name": "Stop", "last_assistant_message": "继续吗？"}

    assert action_for_payload(payload, settings).state == "input"


def test_process_payload_preserves_newer_session_event(tmp_path):
    # Break caught: allowing a delayed hook to overwrite the state from a newer event.
    settings = load_settings(tmp_path)
    process_payload(
        {"session_id": "session-a", "hook_event_name": "UserPromptSubmit"},
        settings,
        launch=False,
        now=100.0,
        event_time_ns=20,
    )
    process_payload(
        {"session_id": "session-a", "hook_event_name": "SessionStart"},
        settings,
        launch=False,
        now=101.0,
        event_time_ns=19,
    )

    assert StateStore(settings).effective(now=101.0) == "working"


def test_process_payload_defaults_missing_session_to_main(tmp_path):
    # Break caught: dropping a valid hook event when Codex omits the optional session identifier.
    settings = load_settings(tmp_path)
    process_payload(
        {"hook_event_name": "SessionStart"}, settings, launch=False, now=100.0, event_time_ns=1
    )

    assert StateStore(settings)._read_record(StateStore(settings)._record_path("main")).state == "idle"


def test_ensure_daemon_starts_a_detached_child_with_the_data_home(tmp_path, monkeypatch):
    # Break caught: launching the daemon in the hook process group or without its configured data root.
    settings = load_settings(tmp_path)
    observed: dict[str, object] = {}

    def capture_process(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs

    monkeypatch.setattr("codex_lamp.hook.subprocess.Popen", capture_process)
    ensure_daemon(settings)

    assert observed["args"] == ([sys.executable, "-m", "codex_lamp.daemon"],)
    assert observed["kwargs"]["start_new_session"] is True
    assert observed["kwargs"]["stdin"] is subprocess.DEVNULL
    assert observed["kwargs"]["stdout"] is subprocess.DEVNULL
    assert observed["kwargs"]["stderr"] is subprocess.DEVNULL
    assert observed["kwargs"]["env"]["CODEX_LAMP_HOME"] == str(tmp_path)


def test_main_logs_invalid_json_and_returns_success(tmp_path, monkeypatch):
    # Break caught: a malformed hook payload causing Codex's hook execution to fail.
    monkeypatch.setenv("CODEX_LAMP_HOME", str(tmp_path))
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))

    assert main() == 0
    assert (Path(tmp_path) / "logs" / "hook.log").read_text(encoding="utf-8").startswith("hook error:")


def test_hook_runner_logs_dependency_failure_and_exits_successfully(tmp_path):
    # Break caught: a missing runtime dependency making Codex treat a hook as failed.
    completed = subprocess.run(
        ["bash", str(HOOK_RUNNER)],
        input="{}",
        text=True,
        env={**os.environ, "CODEX_LAMP_HOME": str(tmp_path)},
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    log_text = (tmp_path / "logs" / "hook.log").read_text(encoding="utf-8")
    assert "codex_lamp.hook" in log_text
    assert "hook runner error: hook process failed" in log_text


def test_hook_runner_prefers_the_data_home_venv(tmp_path):
    # Break caught: bypassing the project venv and running a global Python with different dependencies.
    python_path = tmp_path / "venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text(
        "#!/usr/bin/env bash\nprintf 'venv selected\\n' > \"$CODEX_LAMP_HOME/selected\"\n",
        encoding="utf-8",
    )
    python_path.chmod(0o755)

    completed = subprocess.run(
        ["bash", str(HOOK_RUNNER)],
        input="{}",
        text=True,
        env={**os.environ, "CODEX_LAMP_HOME": str(tmp_path)},
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert (tmp_path / "selected").read_text(encoding="utf-8") == "venv selected\n"


def test_hook_runner_expands_a_tilde_prefixed_data_home(tmp_path):
    # Break caught: treating an explicit ~/ data-root override literally instead of using the Python-resolved home.
    expected_home = tmp_path / "CodexLamp"
    python_path = expected_home / "venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$CODEX_LAMP_HOME\" > \"$CODEX_LAMP_HOME/selected\"\n",
        encoding="utf-8",
    )
    python_path.chmod(0o755)

    completed = subprocess.run(
        ["bash", str(HOOK_RUNNER)],
        input="{}",
        text=True,
        cwd=tmp_path,
        env={**os.environ, "HOME": str(tmp_path), "CODEX_LAMP_HOME": "~/CodexLamp"},
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert (expected_home / "selected").read_text(encoding="utf-8") == f"{expected_home}\n"
