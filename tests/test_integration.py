import time

from codex_lamp.config import load_settings
from codex_lamp.hook import process_payload
from codex_lamp.state import StateStore


def test_codex_turn_lifecycle_without_hardware(tmp_path, monkeypatch):
    # Break caught: ending the only Codex session leaves the aggregate lamp state active.
    monkeypatch.setenv("CODEX_LAMP_HOME", str(tmp_path))
    settings = load_settings(tmp_path)
    process_payload({"session_id": "a", "hook_event_name": "SessionStart"}, settings, launch=False)
    process_payload({"session_id": "a", "hook_event_name": "UserPromptSubmit"}, settings, launch=False)
    assert StateStore(settings).effective() == "working"
    process_payload(
        {"session_id": "a", "hook_event_name": "Stop", "last_assistant_message": "完成。"},
        settings,
        launch=False,
    )
    assert StateStore(settings).effective() == "idle"
    process_payload({"session_id": "a", "hook_event_name": "SessionEnd"}, settings, launch=False)
    assert StateStore(settings).effective() == "off"


def test_two_sessions_preserve_input_working_idle_priority(tmp_path, monkeypatch):
    # Break caught: one session's lower state hides another session's higher-priority state.
    monkeypatch.setenv("CODEX_LAMP_HOME", str(tmp_path))
    settings = load_settings(tmp_path)
    store = StateStore(settings)

    process_payload({"session_id": "working", "hook_event_name": "SessionStart"}, settings, launch=False)
    assert store.effective() == "idle"
    process_payload(
        {"session_id": "working", "hook_event_name": "UserPromptSubmit"},
        settings,
        launch=False,
    )
    assert store.effective() == "working"
    process_payload({"session_id": "input", "hook_event_name": "SessionStart"}, settings, launch=False)
    assert store.effective() == "working"
    process_payload(
        {
            "session_id": "input",
            "hook_event_name": "Stop",
            "last_assistant_message": "请选择下一步。",
        },
        settings,
        launch=False,
    )
    assert store.effective() == "input"
    process_payload({"session_id": "input", "hook_event_name": "SessionEnd"}, settings, launch=False)
    assert store.effective() == "working"
    process_payload(
        {
            "session_id": "working",
            "hook_event_name": "Stop",
            "last_assistant_message": "Done.",
        },
        settings,
        launch=False,
    )
    assert store.effective() == "idle"


def test_hook_path_stays_under_100ms(tmp_path, monkeypatch):
    # Break caught: the no-launch hook path performs slow I/O that blocks Codex for 100 ms or more.
    monkeypatch.setenv("CODEX_LAMP_HOME", str(tmp_path))
    settings = load_settings(tmp_path)
    started = time.perf_counter()
    process_payload(
        {"session_id": "timing", "hook_event_name": "UserPromptSubmit"},
        settings,
        launch=False,
    )
    assert time.perf_counter() - started < 0.1
