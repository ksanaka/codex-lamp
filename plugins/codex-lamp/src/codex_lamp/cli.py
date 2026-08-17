"""Command-line setup, diagnostics, and explicit lamp controls."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import platform
import signal
import shutil
import subprocess
import sys
import time
from typing import Any, Callable

from .config import CONFIG_FILENAME, Settings, load_settings, resolve_home, save_settings
from .protocol import commands_for_state
from .state import StateStore


STATES = ("idle", "working", "input", "off")
PLUGIN_ROOT_FILENAME = "plugin_root"
HARDWARE_TEST_DELAY = 1.0
DAEMON_STOP_TIMEOUT_SECONDS = 2.0
DAEMON_STOP_POLL_SECONDS = 0.05
LEDOFF_TIMEOUT_SECONDS = 5.0
CONFIG_CONVERTERS: dict[str, Callable[[str], Any]] = {
    "device_name_prefix": str,
    "device_uuid": lambda value: None if value.casefold() in {"none", "null"} else value,
    "idle_color": lambda value: tuple(int(part) for part in value.split(",")),
    "input_color": lambda value: tuple(int(part) for part in value.split(",")),
    "working_command": str,
    "brightness": int,
    "stale_seconds": int,
    "poll_interval": float,
    "relaunch_cooldown": int,
    "question_markers": lambda value: tuple(part.strip() for part in value.split(",")),
}


def build_parser() -> argparse.ArgumentParser:
    """Build the public ``codex-lamp`` command parser."""
    parser = argparse.ArgumentParser(prog="codex-lamp")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("setup")
    subparsers.add_parser("scan")

    test_parser = subparsers.add_parser("test")
    test_parser.add_argument("--dry-run", action="store_true")

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--json", action="store_true")

    config_parser = subparsers.add_parser("config")
    config_parser.add_argument("key", nargs="?")
    config_parser.add_argument("value", nargs="?")
    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--json", action="store_true")
    logs_parser = subparsers.add_parser("logs")
    logs_parser.add_argument("--lines", type=int, default=50)

    uninstall_parser = subparsers.add_parser("uninstall")
    uninstall_parser.add_argument("--yes", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one CLI command and return its process exit status."""
    arguments = build_parser().parse_args(argv)
    if arguments.command == "setup":
        return _setup()
    if arguments.command == "scan":
        return _scan()
    if arguments.command == "status":
        return _status(arguments.json)
    if arguments.command == "config":
        return _config(arguments.key, arguments.value)
    if arguments.command == "logs":
        return _logs(arguments.lines)
    if arguments.command == "uninstall":
        return _uninstall(arguments.yes)
    if arguments.command == "test":
        settings = load_settings()
        if arguments.dry_run:
            _print_dry_run(settings)
            return 0
        return _run_hardware_test(settings)
    if arguments.command == "doctor":
        return _doctor(arguments.json)
    return 0


def _setup() -> int:
    settings = load_settings()
    config_path = settings.home / CONFIG_FILENAME
    if not config_path.exists():
        save_settings(settings)
    configured_plugin_root = os.environ.get("CODEX_PLUGIN_ROOT")
    if configured_plugin_root:
        plugin_root = Path(configured_plugin_root).expanduser().resolve()
        (settings.home / PLUGIN_ROOT_FILENAME).write_text(
            f"{plugin_root}\n", encoding="utf-8"
        )
    print(f"Codex Lamp data: {settings.home}")
    print(f"Configuration: {config_path}")
    return 0


def _scan() -> int:
    settings = load_settings()
    device = _discover_device(settings)
    if device is None:
        print("No compatible Moonside device found", file=sys.stderr)
        return 1
    name = getattr(device, "name", None) or "Moonside device"
    address = getattr(device, "address", None)
    print(f"{name} ({address})" if address else name)
    return 0


def _status(as_json: bool) -> int:
    settings = load_settings()
    store = StateStore(settings)
    effective_state = store.effective()
    sessions = sum(1 for _ in (settings.home / "sessions").glob("*.json"))
    pid, daemon_running = _daemon_status(settings.home)
    report = {
        "daemon": {"pid": pid, "running": daemon_running},
        "device": {
            "name_prefix": settings.device_name_prefix,
            "uuid": settings.device_uuid,
        },
        "effective_state": effective_state,
        "sessions": sessions,
    }
    if as_json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(f"daemon: {'running' if daemon_running else 'stopped'}")
        print(f"device: {settings.device_uuid or settings.device_name_prefix}")
        print(f"sessions: {sessions}")
        print(f"effective state: {effective_state}")
    return 0


def _config(key: str | None, raw_value: str | None) -> int:
    settings = load_settings()
    if key is None:
        print(json.dumps(_settings_dict(settings), indent=2, sort_keys=True))
        return 0
    if key not in CONFIG_CONVERTERS:
        print(f"unsupported configuration key: {key}", file=sys.stderr)
        return 2
    if raw_value is None:
        print(json.dumps(_json_value(getattr(settings, key))))
        return 0

    try:
        value = CONFIG_CONVERTERS[key](raw_value)
        updated = settings.with_updates(**{key: value})
    except (TypeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    save_settings(updated)
    print(f"{key}={json.dumps(_json_value(getattr(updated, key)))}")
    return 0


def _logs(lines: int) -> int:
    if lines <= 0:
        print("--lines must be positive", file=sys.stderr)
        return 2
    log_dir = resolve_home().expanduser() / "logs"
    print(f"Log directory: {log_dir}")
    entries: list[str] = []
    if log_dir.is_dir():
        for log_path in sorted(log_dir.glob("*.log")):
            try:
                entries.extend(log_path.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError as error:
                print(f"unable to read {log_path.name}: {error}", file=sys.stderr)
    for entry in entries[-lines:]:
        print(entry)
    return 0


def _uninstall(confirmed: bool) -> int:
    try:
        home = _validated_uninstall_home()
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    if not confirmed:
        answer = input(f"Remove Codex Lamp data at {home}? [y/N] ")
        if answer.casefold() not in {"y", "yes"}:
            print("Uninstall cancelled")
            return 1
    if not home.exists():
        print(f"Codex Lamp data not found: {home}")
        return 0
    if not (home / CONFIG_FILENAME).is_file():
        print(f"refusing uninitialized Codex Lamp home: {home}", file=sys.stderr)
        return 2

    settings = load_settings(home)
    with (home / "daemon.lock").open("a+") as lock_file:
        daemon_owns_lock = not _try_daemon_lock(lock_file)
        if daemon_owns_lock:
            pid = _confirmed_daemon_pid(home, lock_file)
            if pid is None:
                if not _try_daemon_lock(lock_file):
                    print("refusing to signal an unverified daemon owner", file=sys.stderr)
                    return 2
            else:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError as error:
                    print(f"unable to signal daemon {pid}: {error}", file=sys.stderr)
                    return 2
                if not _wait_for_daemon_stop(lock_file, pid):
                    print(f"daemon {pid} did not stop before the deadline", file=sys.stderr)
                    return 2
        _attempt_ledoff(settings)
        shutil.rmtree(home)
    print(f"Removed Codex Lamp data: {home}")
    return 0


def _validated_uninstall_home() -> Path:
    home = resolve_home().expanduser().resolve()
    broad_targets = {Path("/").resolve(), Path.home().resolve(), Path.cwd().resolve()}
    if home in broad_targets or len(home.parts) < 3:
        raise ValueError(f"refusing unsafe Codex Lamp home: {home}")
    return home


def _try_daemon_lock(lock_file) -> bool:
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _confirmed_daemon_pid(home: Path, lock_file) -> int | None:
    pid = _read_pid(home)
    if pid is None or pid == os.getpid() or not _process_alive(pid):
        return None
    if _read_pid(home) != pid or _try_daemon_lock(lock_file):
        return None
    return pid


def _wait_for_daemon_stop(lock_file, pid: int) -> bool:
    deadline = time.monotonic() + DAEMON_STOP_TIMEOUT_SECONDS
    lock_acquired = False
    while True:
        if not lock_acquired:
            lock_acquired = _try_daemon_lock(lock_file)
        if lock_acquired and not _process_alive(pid):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(DAEMON_STOP_POLL_SECONDS, remaining))


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _attempt_ledoff(settings: Settings) -> None:
    environment = os.environ.copy()
    environment["CODEX_LAMP_HOME"] = str(settings.home)
    try:
        result = subprocess.run(
            _ledoff_worker_command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            timeout=LEDOFF_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"warning: unable to turn lamp off: {error}", file=sys.stderr)
        return
    if result.returncode != 0:
        print("warning: unable to turn lamp off", file=sys.stderr)


def _ledoff_worker_command() -> list[str]:
    return [
        sys.executable,
        "-c",
        "from codex_lamp.cli import _ledoff_worker; raise SystemExit(_ledoff_worker())",
    ]


def _ledoff_worker() -> int:
    async def turn_off() -> None:
        from .daemon import BleakLampTransport

        settings = load_settings()
        transport = BleakLampTransport()
        try:
            device = await transport.discover(settings)
            if device is None:
                return
            await transport.connect(device)
            await transport.send("LEDOFF")
        finally:
            await transport.close()

    try:
        asyncio.run(turn_off())
    except Exception:
        return 1
    return 0


def _print_dry_run(settings: Settings) -> None:
    for state in STATES:
        for command in commands_for_state(state, settings):
            print(f"{state}: {command}")


def _run_hardware_test(settings: Settings) -> int:
    async def exercise() -> None:
        transport = _new_transport()
        try:
            device = await transport.discover(settings)
            if device is None:
                raise RuntimeError("No compatible Moonside device found")
            await transport.connect(device)
            for state in STATES:
                print(state)
                for command in commands_for_state(state, settings):
                    await transport.send(command)
                if HARDWARE_TEST_DELAY:
                    await asyncio.sleep(HARDWARE_TEST_DELAY)
        finally:
            await transport.close()

    try:
        asyncio.run(exercise())
    except Exception as error:
        print(f"hardware test failed: {error}", file=sys.stderr)
        return 1
    return 0


def _new_transport():
    from .daemon import BleakLampTransport

    return BleakLampTransport()


def _doctor(as_json: bool) -> int:
    try:
        report = _doctor_report(load_settings())
    except Exception as error:
        report = {
            "python": sys.version_info >= (3, 10),
            "bleak": False,
            "platform": False,
            "bluetooth": False,
            "device": False,
            "daemon": False,
            "hooks": False,
            "ok": False,
        }
        if not as_json:
            print(f"configuration: failed ({error})", file=sys.stderr)
    if as_json:
        print(json.dumps(report, sort_keys=True))
    else:
        for name, passed in report.items():
            print(f"{name}: {'ok' if passed else 'failed'}")
    return 0 if report["ok"] else 1


def _doctor_report(settings: Settings) -> dict[str, bool]:
    python_ok = sys.version_info >= (3, 10)
    bleak_ok = _bleak_available()
    platform_ok = _platform_is_supported()
    bluetooth_ok = platform_ok and bleak_ok and _bluetooth_available()
    device_ok = bluetooth_ok and _discover_device(settings) is not None
    checks = {
        "python": python_ok,
        "bleak": bleak_ok,
        "platform": platform_ok,
        "bluetooth": bluetooth_ok,
        "device": device_ok,
        "daemon": _daemon_running(settings.home),
        "hooks": _hooks_installed(settings.home),
    }
    return {**checks, "ok": all(checks.values())}


def _bleak_available() -> bool:
    return importlib.util.find_spec("bleak") is not None


def _platform_is_supported() -> bool:
    return platform.system() == "Darwin"


def _bluetooth_available() -> bool:
    try:
        result = subprocess.run(
            ["system_profiler", "SPBluetoothDataType"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _discover_device(settings: Settings):
    async def discover():
        from .daemon import BleakLampTransport

        transport = BleakLampTransport()
        try:
            return await transport.discover(settings)
        finally:
            await transport.close()

    try:
        return asyncio.run(discover())
    except Exception:
        return None


def _daemon_running(home: Path) -> bool:
    return _daemon_status(home)[1]


def _daemon_status(home: Path) -> tuple[int | None, bool]:
    pid = _read_pid(home)
    if pid is None:
        return None, False
    try:
        os.kill(pid, 0)
    except OSError:
        return pid, False
    return pid, True


def _read_pid(home: Path) -> int | None:
    try:
        pid = int((home / "daemon.pid").read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 0 else None


def _hooks_installed(home: Path) -> bool:
    plugin_roots: list[Path] = []
    configured_root = os.environ.get("CODEX_PLUGIN_ROOT")
    if configured_root:
        plugin_roots.append(Path(configured_root).expanduser().resolve())
    try:
        persisted_root = (home / PLUGIN_ROOT_FILENAME).read_text(encoding="utf-8").strip()
    except OSError:
        persisted_root = ""
    if persisted_root:
        plugin_roots.append(Path(persisted_root).expanduser().resolve())
    plugin_roots.append(Path(__file__).parents[2])
    return any((root / "hooks" / "hooks.json").is_file() for root in plugin_roots)


def _settings_dict(settings: Settings) -> dict[str, Any]:
    return {
        key: _json_value(getattr(settings, key))
        for key in CONFIG_CONVERTERS
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    return value
