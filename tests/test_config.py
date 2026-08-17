import json
import math
from pathlib import Path

import pytest

from codex_lamp.config import load_settings, resolve_home, save_settings


def test_resolve_home_prefers_environment(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CODEX_LAMP_HOME", str(tmp_path))
    assert resolve_home() == tmp_path


def test_load_settings_uses_approved_defaults(tmp_path: Path):
    settings = load_settings(tmp_path)
    assert settings.idle_color == (255, 180, 50)
    assert settings.input_color == (200, 0, 255)
    assert settings.working_command == "THEME.BEAT2.255,255,255,0,0,140,"
    assert settings.stale_seconds == 1800
    assert settings.priority == ("input", "working", "idle", "off")


def test_save_settings_preserves_user_values(tmp_path: Path):
    settings = load_settings(tmp_path)
    save_settings(settings.with_updates(brightness=80))
    assert load_settings(tmp_path).brightness == 80


def test_with_updates_keeps_tuple_settings_immutable(tmp_path: Path):
    settings = load_settings(tmp_path).with_updates(idle_color=[1, 2, 3])
    assert settings.idle_color == (1, 2, 3)


def test_load_settings_rejects_a_reordered_configured_priority(tmp_path: Path):
    # Break caught: accepting a configuration that makes idle outrank input.
    (tmp_path / "config.json").write_text(
        json.dumps({"priority": ["idle", "input", "working", "off"]}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="priority"):
        load_settings(tmp_path)


@pytest.mark.parametrize("field", ["stale_seconds", "poll_interval", "relaunch_cooldown"])
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_positive_numeric_settings_reject_non_finite_values(tmp_path, field, value):
    # Break caught: accepting NaN or infinity into daemon timing and retry settings.
    with pytest.raises(ValueError, match="finite and positive"):
        load_settings(tmp_path).with_updates(**{field: value})
