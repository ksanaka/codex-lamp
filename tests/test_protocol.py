import pytest

from codex_lamp.config import load_settings
from codex_lamp.protocol import (
    NUS_TX_UUID,
    brightness_command,
    color_command,
    commands_for_state,
    theme_command,
)


def test_exposes_the_moonside_nus_tx_uuid():
    # Break caught: writing command bytes to the NUS RX characteristic instead of TX.
    assert NUS_TX_UUID == "6e400002-b5a3-f393-e0a9-e50e24dcca9e"


def test_formats_protocol_commands():
    # Break caught: omitting fixed-width numeric padding from device commands.
    assert color_command(0, 255, 7) == "COLOR000255007"
    assert brightness_command(60) == "BRIGH060"
    assert theme_command("THEME.BEAT2.255,255,255,0,0,140,") == "THEME.BEAT2.255,255,255,0,0,140,"


@pytest.mark.parametrize(
    ("function", "args"),
    [
        (color_command, (-1, 0, 0)),
        (color_command, (256, 0, 0)),
        (color_command, (False, 0, 0)),
        (brightness_command, (-1,)),
        (brightness_command, (121,)),
        (brightness_command, (True,)),
    ],
)
def test_rejects_values_outside_protocol_ranges(function, args):
    # Break caught: emitting malformed commands for invalid numeric protocol fields.
    with pytest.raises(ValueError):
        function(*args)


def test_maps_states_to_approved_commands_in_device_order(tmp_path):
    # Break caught: switching colors before enabling the lamp or skipping configured brightness.
    settings = load_settings(tmp_path).with_updates(brightness=60)

    assert commands_for_state("idle", settings) == ["LEDON", "BRIGH060", "COLOR255180050"]
    assert commands_for_state("input", settings) == ["LEDON", "BRIGH060", "COLOR200000255"]
    assert commands_for_state("working", settings) == ["THEME.BEAT2.255,255,255,0,0,140,"]
    assert commands_for_state("off", settings) == ["LEDOFF"]


def test_rejects_unapproved_states(tmp_path):
    # Break caught: treating unknown state names as a device command or an approved state.
    with pytest.raises(ValueError, match="state"):
        commands_for_state("paused", load_settings(tmp_path))
