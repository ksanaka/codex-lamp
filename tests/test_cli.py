import argparse
import asyncio
import json
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from codex_lamp.config import load_settings, save_settings
from codex_lamp.cli import build_parser, main
from codex_lamp.state import StateStore


def _start_daemon_lock_holder(home: Path, sigterm_behavior: str) -> subprocess.Popen:
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            """
import fcntl
import os
from pathlib import Path
import signal
import sys
import time

home = Path(sys.argv[1])
lock_file = (home / "daemon.lock").open("a+")
fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
(home / "daemon.pid").write_text(f"{os.getpid()}\\n", encoding="ascii")
behavior = sys.argv[2]
if behavior == "delay":
    def delayed_exit(signum, frame):
        time.sleep(0.15)
        (home / "daemon-exited").write_text("yes", encoding="ascii")
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, delayed_exit)
elif behavior == "ignore":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
print("ready", flush=True)
while True:
    time.sleep(1)
""",
            str(home),
            sigterm_behavior,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "ready"
    return process


def test_parser_exposes_exactly_the_eight_approved_subcommands():
    # Break caught: adding, removing, or renaming a user-visible command.
    parser = build_parser()
    subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )

    assert set(subparsers.choices) == {
        "setup",
        "scan",
        "test",
        "status",
        "config",
        "doctor",
        "logs",
        "uninstall",
    }


def test_dry_run_prints_all_states_without_touching_hardware(
    tmp_path, monkeypatch, capsys
):
    # Break caught: omitting a state/command or constructing a BLE transport in dry-run mode.
    monkeypatch.setenv("CODEX_LAMP_HOME", str(tmp_path))
    monkeypatch.setattr(
        "codex_lamp.cli._run_hardware_test",
        lambda settings: pytest.fail("dry-run accessed Bluetooth"),
    )

    assert main(["test", "--dry-run"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "idle: LEDON",
        "idle: BRIGH120",
        "idle: COLOR255180050",
        "working: THEME.BEAT2.255,255,255,0,0,140,",
        "input: LEDON",
        "input: BRIGH120",
        "input: COLOR200000255",
        "off: LEDOFF",
    ]


def test_doctor_json_reports_missing_bleak_without_discovery(
    tmp_path, monkeypatch, capsys
):
    # Break caught: importing the daemon eagerly or touching Bluetooth after a prerequisite fails.
    monkeypatch.setenv("CODEX_LAMP_HOME", str(tmp_path))
    monkeypatch.setattr("codex_lamp.cli._bleak_available", lambda: False)
    monkeypatch.setattr("codex_lamp.cli._platform_is_supported", lambda: False)
    monkeypatch.setattr(
        "codex_lamp.cli._discover_device",
        lambda settings: pytest.fail("doctor attempted discovery on an unsupported platform"),
    )

    assert main(["doctor", "--json"]) == 1

    report = json.loads(capsys.readouterr().out)
    assert set(report) == {
        "python",
        "bleak",
        "platform",
        "bluetooth",
        "device",
        "daemon",
        "hooks",
        "ok",
    }
    assert report["python"] is True
    assert report["bleak"] is False
    assert report["platform"] is False
    assert report["device"] is False
    assert report["ok"] is False


def test_doctor_json_keeps_stable_schema_for_malformed_configuration(
    tmp_path, monkeypatch, capsys
):
    # Break caught: letting config parsing escape before the doctor JSON boundary.
    monkeypatch.setenv("CODEX_LAMP_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text("{not-json\n", encoding="utf-8")

    assert main(["doctor", "--json"]) == 1

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert set(report) == {
        "python",
        "bleak",
        "platform",
        "bluetooth",
        "device",
        "daemon",
        "hooks",
        "ok",
    }
    assert report["ok"] is False
    assert "Traceback" not in captured.err


def test_status_json_reports_daemon_device_sessions_and_effective_state(
    tmp_path, monkeypatch, capsys
):
    # Break caught: reporting only PID-file presence or failing to aggregate current sessions.
    monkeypatch.setenv("CODEX_LAMP_HOME", str(tmp_path))
    settings = load_settings()
    StateStore(settings).update("session-a", "input", 1)
    (tmp_path / "daemon.pid").write_text(f"{os.getpid()}\n", encoding="ascii")

    assert main(["status", "--json"]) == 0

    status = json.loads(capsys.readouterr().out)
    assert status == {
        "daemon": {"pid": os.getpid(), "running": True},
        "device": {"name_prefix": "MOONSIDE", "uuid": None},
        "effective_state": "input",
        "sessions": 1,
    }


def test_config_updates_validated_values_and_rejects_invalid_values(
    tmp_path, monkeypatch, capsys
):
    # Break caught: storing unvalidated strings or persisting an out-of-range brightness.
    monkeypatch.setenv("CODEX_LAMP_HOME", str(tmp_path))

    assert main(["config", "brightness", "80"]) == 0
    assert load_settings().brightness == 80

    assert main(["config", "brightness", "121"]) == 2
    assert load_settings().brightness == 80
    assert "brightness must be an integer in 0..120" in capsys.readouterr().err


@pytest.mark.parametrize("invalid_value", ["nan", "inf"])
def test_config_rejects_non_finite_poll_interval_without_overwriting(
    tmp_path, monkeypatch, invalid_value
):
    # Break caught: persisting a non-finite poll interval that can busy-loop the daemon.
    monkeypatch.setenv("CODEX_LAMP_HOME", str(tmp_path))
    assert main(["config", "poll_interval", "0.5"]) == 0

    assert main(["config", "poll_interval", invalid_value]) == 2
    assert load_settings().poll_interval == 0.5


def test_logs_prints_only_the_requested_recent_lines(tmp_path, monkeypatch, capsys):
    # Break caught: dumping an unbounded daemon log or selecting the oldest entries.
    monkeypatch.setenv("CODEX_LAMP_HOME", str(tmp_path))
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "daemon.log").write_text("oldest\nmiddle\nnewest\n", encoding="utf-8")

    assert main(["logs", "--lines", "2"]) == 0

    output = capsys.readouterr().out
    assert str(log_dir) in output
    assert "oldest" not in output
    assert output.endswith("middle\nnewest\n")


def test_uninstall_does_not_signal_stale_pid_when_daemon_lock_is_free(
    tmp_path, monkeypatch
):
    # Break caught: signaling an unrelated PID-reused process from a stale PID file.
    home = tmp_path / "lamp-home"
    outside = tmp_path / "keep-me"
    outside.write_text("safe", encoding="utf-8")
    monkeypatch.setenv("CODEX_LAMP_HOME", str(home))
    save_settings(load_settings())
    (home / "daemon.pid").write_text("4242\n", encoding="ascii")
    events: list[tuple] = []

    monkeypatch.setattr(
        "codex_lamp.cli.os.kill", lambda pid, sig: events.append(("signal", pid, sig))
    )
    monkeypatch.setattr(
        "codex_lamp.cli._attempt_ledoff",
        lambda settings: events.append(("off", settings.home.resolve())),
    )

    assert main(["uninstall", "--yes"]) == 0

    assert events == [("off", home.resolve())]
    assert not home.exists()
    assert outside.read_text(encoding="utf-8") == "safe"


def test_uninstall_waits_for_the_signaled_daemon_to_release_its_lock(
    tmp_path, monkeypatch
):
    # Break caught: opening BLE or deleting the home while daemon shutdown still owns its lock.
    home = tmp_path / "lamp-home"
    monkeypatch.setenv("CODEX_LAMP_HOME", str(home))
    save_settings(load_settings())
    process = _start_daemon_lock_holder(home, "delay")
    events: list[str] = []
    monkeypatch.setattr(
        "codex_lamp.cli._attempt_ledoff", lambda settings: events.append("off")
    )
    reaper = threading.Thread(target=process.wait)
    reaper.start()

    try:
        assert main(["uninstall", "--yes"]) == 0
        reaper.join(timeout=1)
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

    assert events == ["off"]
    assert not home.exists()


def test_uninstall_aborts_when_the_daemon_does_not_exit(tmp_path, monkeypatch):
    # Break caught: deleting live daemon files after a shutdown deadline expires.
    home = tmp_path / "lamp-home"
    monkeypatch.setenv("CODEX_LAMP_HOME", str(home))
    save_settings(load_settings())
    process = _start_daemon_lock_holder(home, "ignore")
    monkeypatch.setattr(
        "codex_lamp.cli.DAEMON_STOP_TIMEOUT_SECONDS", 0.05, raising=False
    )
    monkeypatch.setattr(
        "codex_lamp.cli._attempt_ledoff",
        lambda settings: pytest.fail("LEDOFF started while daemon still owned lock"),
    )

    started = time.monotonic()
    try:
        assert main(["uninstall", "--yes"]) == 2
        assert time.monotonic() - started < 1
        assert process.poll() is None
        assert (home / "config.json").is_file()
    finally:
        process.kill()
        process.wait()


def test_uninstall_hard_bounds_a_cancellation_resistant_ledoff_attempt(
    tmp_path, monkeypatch
):
    # Break caught: asyncio timeout waiting forever for a BLE coroutine to accept cancellation.
    home = tmp_path / "lamp-home"
    monkeypatch.setenv("CODEX_LAMP_HOME", str(home))
    save_settings(load_settings())

    class CancellationResistantTransport:
        async def discover(self, settings):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.Event().wait()

        async def close(self):
            return None

    monkeypatch.setattr(
        "codex_lamp.daemon.BleakLampTransport", CancellationResistantTransport
    )
    monkeypatch.setattr(
        "codex_lamp.cli.LEDOFF_TIMEOUT_SECONDS", 0.05, raising=False
    )
    monkeypatch.setattr(
        "codex_lamp.cli._ledoff_worker_command",
        lambda: [sys.executable, "-c", "import time; time.sleep(60)"],
        raising=False,
    )
    context = multiprocessing.get_context("fork")
    process = context.Process(target=main, args=(["uninstall", "--yes"],))
    process.start()
    process.join(timeout=0.5)
    try:
        assert not process.is_alive()
        assert process.exitcode == 0
        assert not home.exists()
    finally:
        if process.is_alive():
            process.kill()
            process.join()


def test_uninstall_refuses_a_user_home_even_with_confirmation(monkeypatch):
    # Break caught: treating a broad CODEX_LAMP_HOME override as safe recursive-delete input.
    monkeypatch.setenv("CODEX_LAMP_HOME", str(Path.home()))
    monkeypatch.setattr(
        "codex_lamp.cli.shutil.rmtree",
        lambda path: pytest.fail(f"attempted broad deletion: {path}"),
    )

    assert main(["uninstall", "--yes"]) == 2


def test_uninstall_refuses_an_uninitialized_override(tmp_path, monkeypatch):
    # Break caught: recursively deleting an arbitrary directory selected by an override.
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    (unrelated / "personal.txt").write_text("keep", encoding="utf-8")
    deleted: list[Path] = []
    monkeypatch.setenv("CODEX_LAMP_HOME", str(unrelated))
    monkeypatch.setattr("codex_lamp.cli._attempt_ledoff", lambda settings: None)
    monkeypatch.setattr("codex_lamp.cli.shutil.rmtree", deleted.append)

    assert main(["uninstall", "--yes"]) == 2
    assert deleted == []


def test_setup_creates_config_without_overwriting_existing_values(
    tmp_path, monkeypatch
):
    # Break caught: setup only creating directories or resetting user configuration on refresh.
    monkeypatch.setenv("CODEX_LAMP_HOME", str(tmp_path))

    assert main(["setup"]) == 0
    assert (tmp_path / "config.json").is_file()
    save_settings(load_settings().with_updates(brightness=80))

    assert main(["setup"]) == 0
    assert load_settings().brightness == 80


def test_setup_persists_plugin_root_for_later_doctor_hook_detection(
    tmp_path, monkeypatch, capsys
):
    # Break caught: resolving hooks relative to the installed site-packages module.
    home = tmp_path / "lamp-home"
    plugin_root = tmp_path / "installed-plugin"
    hooks_dir = plugin_root / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "hooks.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_LAMP_HOME", str(home))
    monkeypatch.setenv("CODEX_PLUGIN_ROOT", str(plugin_root))

    assert main(["setup"]) == 0
    monkeypatch.delenv("CODEX_PLUGIN_ROOT")
    monkeypatch.setattr("codex_lamp.cli._bleak_available", lambda: False)
    monkeypatch.setattr("codex_lamp.cli._platform_is_supported", lambda: False)

    assert main(["doctor", "--json"]) == 1
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["hooks"] is True


def test_scan_reports_the_discovered_device(tmp_path, monkeypatch, capsys):
    # Break caught: scan succeeding without surfacing the matched device identity.
    monkeypatch.setenv("CODEX_LAMP_HOME", str(tmp_path))
    monkeypatch.setattr(
        "codex_lamp.cli._discover_device",
        lambda settings: SimpleNamespace(name="MOONSIDE-Halo", address="lamp-uuid"),
    )

    assert main(["scan"]) == 0
    assert capsys.readouterr().out.strip() == "MOONSIDE-Halo (lamp-uuid)"


def test_explicit_hardware_test_applies_all_states_and_closes_transport(
    tmp_path, monkeypatch
):
    # Break caught: hardware test omitting LEDOFF or leaking its BLE connection.
    monkeypatch.setenv("CODEX_LAMP_HOME", str(tmp_path))

    class RecordingTransport:
        def __init__(self):
            self.commands: list[str] = []
            self.closed = False

        async def discover(self, settings):
            return "lamp"

        async def connect(self, device):
            return None

        async def send(self, command):
            self.commands.append(command)

        async def close(self):
            self.closed = True

    transport = RecordingTransport()
    monkeypatch.setattr("codex_lamp.cli._new_transport", lambda: transport)
    monkeypatch.setattr("codex_lamp.cli.HARDWARE_TEST_DELAY", 0)

    assert main(["test"]) == 0
    assert transport.commands == [
        "LEDON",
        "BRIGH120",
        "COLOR255180050",
        "THEME.BEAT2.255,255,255,0,0,140,",
        "LEDON",
        "BRIGH120",
        "COLOR200000255",
        "LEDOFF",
    ]
    assert transport.closed is True


@pytest.mark.parametrize(
    ("override_kind", "relative_data_root"),
    [
        ("unset", Path("Library/Application Support/CodexLamp")),
        ("absolute", Path("custom lamp data")),
        ("tilde", Path("custom lamp")),
        ("relative", Path("relative lamp")),
    ],
)
def test_setup_script_builds_runtime_links_entry_point_and_runs_setup(
    tmp_path, override_kind, relative_data_root
):
    # Break caught: installing globally, mishandling spaces/overrides, or not invoking CLI setup.
    root = Path(__file__).parents[1]
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    python_log = tmp_path / "python.log"
    setup_log = tmp_path / "setup.log"
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        """#!/usr/bin/env bash
set -eu
printf '<%s>' "$@" >> "$FAKE_PYTHON_LOG"
printf '\n' >> "$FAKE_PYTHON_LOG"
if [[ "${1:-}" == "-m" && "${2:-}" == "venv" ]]; then
    mkdir -p "$3/bin"
    cp "$0" "$3/bin/python"
elif [[ "${1:-}" == "-m" && "${2:-}" == "pip" ]]; then
    entry="$(dirname "$0")/codex-lamp"
    printf '#!/usr/bin/env bash\nprintf "%%s|%%s|%%s\\n" "$*" "$CODEX_LAMP_HOME" "$CODEX_PLUGIN_ROOT" > "$FAKE_SETUP_LOG"\n' > "$entry"
    chmod +x "$entry"
fi
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(tmp_path),
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "FAKE_PYTHON_LOG": str(python_log),
            "FAKE_SETUP_LOG": str(setup_log),
        }
    )
    data_root = tmp_path / relative_data_root
    if override_kind == "absolute":
        environment["CODEX_LAMP_HOME"] = str(data_root)
    elif override_kind == "tilde":
        environment["CODEX_LAMP_HOME"] = "~/custom lamp"
    elif override_kind == "relative":
        environment["CODEX_LAMP_HOME"] = "relative lamp"
    else:
        environment.pop("CODEX_LAMP_HOME", None)

    result = subprocess.run(
        ["bash", str(root / "plugins/codex-lamp/scripts/setup.sh")],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (data_root / "venv/bin/python").is_file()
    command_link = tmp_path / ".local/bin/codex-lamp"
    assert command_link.is_symlink()
    assert command_link.resolve() == (data_root / "venv/bin/codex-lamp").resolve()
    assert setup_log.read_text(encoding="utf-8").strip() == (
        f"setup|{data_root.resolve()}|{root / 'plugins/codex-lamp'}"
    )
    calls = python_log.read_text(encoding="utf-8")
    assert f"<{data_root / 'venv'}>" in calls
    assert "<-m><pip><install><--upgrade>" in calls
