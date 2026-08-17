"""Persistent configuration for Codex Lamp."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


DEFAULT_HOME = Path.home() / "Library" / "Application Support" / "CodexLamp"
CONFIG_FILENAME = "config.json"
CANONICAL_PRIORITY = ("input", "working", "idle", "off")


@dataclass(frozen=True)
class Settings:
    """The complete, validated Codex Lamp configuration."""

    home: Path
    device_name_prefix: str = "MOONSIDE"
    device_uuid: str | None = None
    idle_color: tuple[int, int, int] = (255, 180, 50)
    input_color: tuple[int, int, int] = (200, 0, 255)
    working_command: str = "THEME.BEAT2.255,255,255,0,0,140,"
    brightness: int = 120
    stale_seconds: int = 1800
    poll_interval: float = 0.2
    relaunch_cooldown: int = 30
    question_markers: tuple[str, ...] = (
        "请确认",
        "请选择",
        "please confirm",
        "would you like",
        "let me know",
    )
    priority: tuple[str, ...] = CANONICAL_PRIORITY

    def __post_init__(self) -> None:
        object.__setattr__(self, "home", Path(self.home))
        object.__setattr__(self, "idle_color", tuple(self.idle_color))
        object.__setattr__(self, "input_color", tuple(self.input_color))
        object.__setattr__(self, "question_markers", tuple(self.question_markers))
        object.__setattr__(self, "priority", tuple(self.priority))
        _validate_rgb("idle_color", self.idle_color)
        _validate_rgb("input_color", self.input_color)
        _validate_int_range("brightness", self.brightness, minimum=0, maximum=120)
        _validate_positive("stale_seconds", self.stale_seconds)
        _validate_positive("poll_interval", self.poll_interval)
        _validate_positive("relaunch_cooldown", self.relaunch_cooldown)
        _validate_priority(self.priority)

    def with_updates(self, **updates: Any) -> Settings:
        """Return a validated copy with the supplied configuration updates."""
        return replace(self, **updates)


def resolve_home() -> Path:
    """Return the writable root, honoring the explicit environment override."""
    configured_home = os.environ.get("CODEX_LAMP_HOME")
    return Path(configured_home).expanduser() if configured_home else DEFAULT_HOME


def ensure_layout(home: Path | None = None) -> Path:
    """Create and return the local-data directory layout."""
    root = Path(home) if home is not None else resolve_home()
    root.mkdir(parents=True, exist_ok=True)
    (root / "sessions").mkdir(exist_ok=True)
    (root / "logs").mkdir(exist_ok=True)
    return root


def load_settings(home: Path | None = None) -> Settings:
    """Load user configuration, applying defaults for absent values."""
    root = ensure_layout(home)
    config_path = root / CONFIG_FILENAME
    if not config_path.exists():
        return Settings(home=root)

    with config_path.open(encoding="utf-8") as config_file:
        values = json.load(config_file)
    if not isinstance(values, dict):
        raise ValueError("config.json must contain a JSON object")

    values.pop("home", None)
    for name in ("idle_color", "input_color", "question_markers", "priority"):
        if name in values:
            values[name] = tuple(values[name])
    return Settings(home=root, **values)


def save_settings(settings: Settings) -> None:
    """Atomically persist settings while retaining the caller's local root."""
    root = ensure_layout(settings.home)
    config_path = root / CONFIG_FILENAME
    values = asdict(settings)
    values.pop("home")

    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=root, prefix=".config-", suffix=".tmp", delete=False
    ) as temporary_file:
        json.dump(values, temporary_file, indent=2, sort_keys=True)
        temporary_file.write("\n")
        temporary_path = Path(temporary_file.name)
    os.replace(temporary_path, config_path)


def _validate_rgb(name: str, color: tuple[int, int, int]) -> None:
    if len(color) != 3 or any(not isinstance(value, int) or not 0 <= value <= 255 for value in color):
        raise ValueError(f"{name} must contain three RGB values in 0..255")


def _validate_int_range(name: str, value: int, *, minimum: int, maximum: int) -> None:
    if not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")


def _validate_positive(name: str, value: float | int) -> None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{name} must be finite and positive")


def _validate_priority(priority: tuple[str, ...]) -> None:
    if priority != CANONICAL_PRIORITY:
        raise ValueError(f"priority must be {CANONICAL_PRIORITY!r}")
