import hashlib
import json

import pytest

from codex_lamp.config import load_settings
from codex_lamp.state import StateStore


def test_priority_across_sessions(tmp_path):
    # Break caught: selecting the first session rather than the approved highest-priority state.
    store = StateStore(load_settings(tmp_path))
    store.update("a", "working", 10, now=100.0)
    store.update("b", "input", 11, now=100.0)

    assert store.effective(now=100.0) == "input"


def test_older_event_cannot_overwrite_newer_state(tmp_path):
    # Break caught: accepting a delayed event whose timestamp is older than the stored event.
    store = StateStore(load_settings(tmp_path))
    store.update("a", "idle", 20, now=100.0)
    store.update("a", "working", 19, now=101.0)

    assert store.effective(now=101.0) == "idle"


def test_stale_sessions_are_pruned(tmp_path):
    # Break caught: retaining a session after the configured 30-minute freshness period.
    store = StateStore(load_settings(tmp_path))
    store.update("a", "working", 1, now=0.0)

    assert store.effective(now=1801.0) == "off"
    assert not list((tmp_path / "sessions").iterdir())


def test_session_expires_at_the_configured_stale_cutoff(tmp_path):
    # Break caught: treating a session exactly 30 minutes old as still active.
    store = StateStore(load_settings(tmp_path))
    store.update("a", "working", 1, now=0.0)

    assert store.effective(now=1800.0) == "off"


def test_update_persists_a_hashed_session_record_and_effective_state(tmp_path):
    # Break caught: exposing session IDs in filenames or failing to persist the aggregate for the daemon.
    store = StateStore(load_settings(tmp_path))
    store.update("private-session-id", "working", 123, now=100.0)

    filename = hashlib.sha256(b"private-session-id").hexdigest() + ".json"
    with (tmp_path / "sessions" / filename).open(encoding="utf-8") as record_file:
        assert json.load(record_file) == {
            "event_time_ns": 123,
            "session_id": "private-session-id",
            "state": "working",
            "updated_at": 100.0,
        }
    with (tmp_path / "effective_state.json").open(encoding="utf-8") as state_file:
        assert json.load(state_file) == {"state": "working", "updated_at": 100.0}


def test_remove_recomputes_the_effective_state(tmp_path):
    # Break caught: deleting a session without updating the persistent aggregate used by the daemon.
    store = StateStore(load_settings(tmp_path))
    store.update("a", "working", 1, now=100.0)
    store.remove("a", now=101.0)

    assert store.effective(now=101.0) == "off"
    with (tmp_path / "effective_state.json").open(encoding="utf-8") as state_file:
        assert json.load(state_file) == {"state": "off", "updated_at": 101.0}


def test_update_rejects_a_state_outside_the_approved_priority(tmp_path):
    # Break caught: persisting a state the device layer cannot interpret.
    store = StateStore(load_settings(tmp_path))

    with pytest.raises(ValueError, match="invalid state"):
        store.update("a", "celebrating", 1, now=100.0)
