"""Moonside Nordic UART Service command generation."""

from __future__ import annotations

from .config import Settings


NUS_TX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"


def color_command(red: int, green: int, blue: int) -> str:
    """Return a fixed-width RGB command for the lamp."""
    values = (red, green, blue)
    if any(not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 255 for value in values):
        raise ValueError("RGB values must be integers in 0..255")
    return f"COLOR{red:03d}{green:03d}{blue:03d}"


def brightness_command(brightness: int) -> str:
    """Return a fixed-width brightness command for the lamp."""
    if not isinstance(brightness, int) or isinstance(brightness, bool) or not 0 <= brightness <= 120:
        raise ValueError("brightness must be an integer in 0..120")
    return f"BRIGH{brightness:03d}"


def theme_command(theme: str) -> str:
    """Return a configured Moonside theme command unchanged."""
    return theme


def commands_for_state(state: str, settings: Settings) -> list[str]:
    """Return the ordered commands that display an approved lamp state."""
    if state == "idle":
        return ["LEDON", brightness_command(settings.brightness), color_command(*settings.idle_color)]
    if state == "input":
        return ["LEDON", brightness_command(settings.brightness), color_command(*settings.input_color)]
    if state == "working":
        return [theme_command(settings.working_command)]
    if state == "off":
        return ["LEDOFF"]
    raise ValueError(f"unsupported state: {state!r}")
