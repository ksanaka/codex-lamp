# Codex Lamp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a macOS Codex plugin and Marketplace repository that maps Codex lifecycle events to low-latency Moonside Halo lamp states over BLE.

**Architecture:** Plugin-scoped Codex hooks write atomic per-session state through a fast Python hook router. A separately launched daemon aggregates sessions, maintains one BLE connection, and sends Moonside Nordic UART commands; a CLI owns setup, diagnostics, configuration, testing, and cleanup.

**Tech Stack:** Python 3.10+, `bleak`, `pytest`, Bash, Codex plugin manifests, Codex lifecycle hooks, JSON configuration.

## Global Constraints

- Support macOS, Python 3.10 or newer, Codex App or CLI, and Moonside Halo in the first release.
- Keep hook handlers independent of BLE and normally below 100 ms.
- Never block or alter Codex behavior when Bluetooth, dependencies, or hardware fail.
- Store writable data under `~/Library/Application Support/CodexLamp/`, overridable with `CODEX_LAMP_HOME`.
- Use effective-state priority `input > working > idle > off`.
- Expire sessions after 30 minutes without an event by default.
- Target state-to-lamp latency below 500 ms after BLE connection.
- Preserve existing Codex configuration; use only plugin-scoped hooks.
- Preserve upstream MIT attribution and license new code under MIT.
- Use TDD for every behavior-bearing task and commit after each task passes.

---

## File map

| Path | Responsibility |
| --- | --- |
| `plugins/codex-lamp/pyproject.toml` | Python package metadata, dependency, console entry point, pytest settings |
| `plugins/codex-lamp/src/codex_lamp/config.py` | Data paths, defaults, JSON configuration loading and persistence |
| `plugins/codex-lamp/src/codex_lamp/question.py` | Conservative Chinese and English input-request classification |
| `plugins/codex-lamp/src/codex_lamp/state.py` | Atomic per-session records, stale cleanup, priority aggregation |
| `plugins/codex-lamp/src/codex_lamp/protocol.py` | Moonside command generation and validation |
| `plugins/codex-lamp/src/codex_lamp/hook.py` | Codex event mapping, state updates, detached daemon launch |
| `plugins/codex-lamp/src/codex_lamp/daemon.py` | BLE transport, reconnect loop, state watcher, process locking |
| `plugins/codex-lamp/src/codex_lamp/cli.py` | `setup`, `scan`, `test`, `status`, `config`, `doctor`, `logs`, `uninstall` |
| `plugins/codex-lamp/scripts/hook_runner.sh` | Fail-open runtime wrapper used by Codex hooks |
| `plugins/codex-lamp/scripts/setup.sh` | Create isolated venv and install the CLI |
| `plugins/codex-lamp/hooks/hooks.json` | Plugin-scoped Codex lifecycle hook declarations |
| `plugins/codex-lamp/.codex-plugin/plugin.json` | Plugin identity and install-surface metadata |
| `plugins/codex-lamp/skills/codex-lamp/SKILL.md` | Codex setup, testing, customization, and troubleshooting workflow |
| `.agents/plugins/marketplace.json` | Repository Marketplace catalog entry |
| `install.sh` | Local repository bootstrap and Marketplace registration |
| `tests/` | Unit, integration, packaging, and mocked BLE tests |
| `README.md`, `README.zh-CN.md` | English and Chinese user documentation |
| `LICENSE`, `THIRD_PARTY_NOTICES.md` | MIT terms and upstream attribution |

### Task 1: Python package and configuration foundation

**Files:**
- Create: `plugins/codex-lamp/pyproject.toml`
- Create: `plugins/codex-lamp/src/codex_lamp/__init__.py`
- Create: `plugins/codex-lamp/src/codex_lamp/config.py`
- Create: `tests/conftest.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings`, `resolve_home()`, `load_settings()`, `save_settings()`, `ensure_layout()`.
- Consumes: `CODEX_LAMP_HOME` environment variable and optional `config.json`.

- [ ] **Step 1: Write failing configuration tests**

```python
from pathlib import Path
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
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python3 -m pytest tests/test_config.py -v`

Expected: FAIL because `codex_lamp.config` does not exist.

- [ ] **Step 3: Add package metadata and minimal configuration implementation**

Define an immutable `Settings` dataclass with `home: Path`, device selection, approved colors/theme, `brightness: int = 120`, `stale_seconds: int = 1800`, `poll_interval: float = 0.2`, `relaunch_cooldown: int = 30`, question markers, and priority. Implement `with_updates()` using `dataclasses.replace`, validate RGB values in `0..255`, brightness in `0..120`, and positive timeout values. Serialize tuples as JSON arrays and write configuration with a temporary file followed by `os.replace`.

Set package metadata to require Python `>=3.10`, depend on `bleak>=0.22,<2`, expose `codex-lamp = codex_lamp.cli:main`, and configure pytest to add `plugins/codex-lamp/src` to `pythonpath`.

- [ ] **Step 4: Run focused tests**

Run: `python3 -m pytest tests/test_config.py -v`

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add plugins/codex-lamp/pyproject.toml plugins/codex-lamp/src/codex_lamp tests/conftest.py tests/test_config.py
git commit -m "feat: add Codex Lamp configuration foundation"
```

### Task 2: Question classification

**Files:**
- Create: `plugins/codex-lamp/src/codex_lamp/question.py`
- Create: `tests/test_question.py`

**Interfaces:**
- Consumes: `message: str | None` and `markers: tuple[str, ...]`.
- Produces: `requests_input(message, markers) -> bool`.

- [ ] **Step 1: Write failing classifier tests**

```python
from codex_lamp.config import load_settings
from codex_lamp.question import requests_input


def test_detects_terminal_questions_and_confirmations(tmp_path):
    markers = load_settings(tmp_path).question_markers
    assert requests_input("你确认这个方案吗？", markers)
    assert requests_input("Please confirm the migration plan.", markers)
    assert requests_input("Would you like me to continue?", markers)


def test_normal_completion_is_not_input(tmp_path):
    markers = load_settings(tmp_path).question_markers
    assert not requests_input("Implementation is complete and all tests pass.", markers)
    assert not requests_input(None, markers)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python3 -m pytest tests/test_question.py -v`

Expected: FAIL because `codex_lamp.question` does not exist.

- [ ] **Step 3: Implement conservative matching**

Normalize surrounding whitespace and case. Return `True` for a final `?` or `？`, or when one configured phrase appears as a case-insensitive substring. Return `False` for missing or blank text. Keep the marker list in configuration so users can customize it.

- [ ] **Step 4: Run focused tests**

Run: `python3 -m pytest tests/test_question.py -v`

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add plugins/codex-lamp/src/codex_lamp/question.py tests/test_question.py
git commit -m "feat: classify Codex replies that need input"
```

### Task 3: Multi-session state store

**Files:**
- Create: `plugins/codex-lamp/src/codex_lamp/state.py`
- Create: `tests/test_state.py`

**Interfaces:**
- Consumes: `Settings`, session IDs, states, nanosecond event timestamps, and wall-clock seconds.
- Produces: `SessionRecord`, `StateStore.update()`, `StateStore.remove()`, `StateStore.prune()`, `StateStore.effective()`.

- [ ] **Step 1: Write failing aggregation tests**

```python
from codex_lamp.config import load_settings
from codex_lamp.state import StateStore


def test_priority_across_sessions(tmp_path):
    store = StateStore(load_settings(tmp_path))
    store.update("a", "working", 10, now=100.0)
    store.update("b", "input", 11, now=100.0)
    assert store.effective(now=100.0) == "input"


def test_older_event_cannot_overwrite_newer_state(tmp_path):
    store = StateStore(load_settings(tmp_path))
    store.update("a", "idle", 20, now=100.0)
    store.update("a", "working", 19, now=101.0)
    assert store.effective(now=101.0) == "idle"


def test_stale_sessions_are_pruned(tmp_path):
    store = StateStore(load_settings(tmp_path))
    store.update("a", "working", 1, now=0.0)
    assert store.effective(now=1801.0) == "off"
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python3 -m pytest tests/test_state.py -v`

Expected: FAIL because `codex_lamp.state` does not exist.

- [ ] **Step 3: Implement atomic per-session storage**

Create session records under `sessions/`, using the SHA-256 digest of each session ID as the JSON filename. Include `session_id`, `state`, `event_time_ns`, and `updated_at`. Reject invalid states. Lock `state.lock` with `fcntl.flock`, write through `NamedTemporaryFile` plus `os.replace`, remove expired records, and atomically write `effective_state.json` as `{"state": "working", "updated_at": 100.0}`.

- [ ] **Step 4: Run focused tests**

Run: `python3 -m pytest tests/test_state.py -v`

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add plugins/codex-lamp/src/codex_lamp/state.py tests/test_state.py
git commit -m "feat: aggregate multi-session lamp state"
```

### Task 4: Moonside command protocol

**Files:**
- Create: `plugins/codex-lamp/src/codex_lamp/protocol.py`
- Create: `tests/test_protocol.py`

**Interfaces:**
- Produces: `NUS_TX_UUID`, `color_command()`, `brightness_command()`, `theme_command()`, `commands_for_state()`.
- Consumes: validated state names and `Settings`.

- [ ] **Step 1: Write failing protocol tests**

```python
import pytest
from codex_lamp.config import load_settings
from codex_lamp.protocol import brightness_command, color_command, commands_for_state


def test_formats_protocol_commands():
    assert color_command(0, 255, 7) == "COLOR000255007"
    assert brightness_command(60) == "BRIGH060"


def test_rejects_out_of_range_values():
    with pytest.raises(ValueError):
        color_command(256, 0, 0)
    with pytest.raises(ValueError):
        brightness_command(121)


def test_maps_states_to_approved_commands(tmp_path):
    settings = load_settings(tmp_path)
    assert commands_for_state("input", settings)[-1] == "COLOR200000255"
    assert commands_for_state("off", settings) == ["LEDOFF"]
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python3 -m pytest tests/test_protocol.py -v`

Expected: FAIL because `codex_lamp.protocol` does not exist.

- [ ] **Step 3: Implement exact ASCII command generation**

Use NUS TX UUID `6e400002-b5a3-f393-e0a9-e50e24dcca9e`. Format numeric fields with three digits. Map `idle` and `input` to `LEDON`, optional brightness, then their configured color; map `working` to the configured theme command; map `off` to `LEDOFF`.

- [ ] **Step 4: Run focused tests**

Run: `python3 -m pytest tests/test_protocol.py -v`

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add plugins/codex-lamp/src/codex_lamp/protocol.py tests/test_protocol.py
git commit -m "feat: implement Moonside BLE command protocol"
```

### Task 5: Codex hook router and detached daemon launch

**Files:**
- Create: `plugins/codex-lamp/src/codex_lamp/hook.py`
- Create: `plugins/codex-lamp/scripts/hook_runner.sh`
- Create: `tests/test_hook.py`

**Interfaces:**
- Consumes: Codex hook payloads with `hook_event_name`, `session_id`, and optional `last_assistant_message`.
- Produces: `HookAction`, `action_for_payload(payload: dict, settings: Settings) -> HookAction`, `process_payload(payload: dict, settings: Settings | None = None, launch: bool = True, now: float | None = None, event_time_ns: int | None = None) -> None`, `ensure_daemon(settings: Settings) -> None`, fail-open `main() -> int`.
- Uses: `requests_input()` and `StateStore` from Tasks 2 and 3.

- [ ] **Step 1: Write failing event-mapping tests**

```python
from codex_lamp.config import load_settings
from codex_lamp.hook import action_for_payload


def test_maps_codex_events(tmp_path):
    settings = load_settings(tmp_path)
    assert action_for_payload({"hook_event_name": "SessionStart"}, settings).state == "idle"
    assert action_for_payload({"hook_event_name": "UserPromptSubmit"}, settings).state == "working"
    assert action_for_payload({"hook_event_name": "PreToolUse"}, settings).state == "working"
    assert action_for_payload({"hook_event_name": "PermissionRequest"}, settings).state == "input"
    assert action_for_payload({"hook_event_name": "SessionEnd"}, settings).kind == "remove"


def test_stop_uses_last_assistant_message(tmp_path):
    settings = load_settings(tmp_path)
    payload = {"hook_event_name": "Stop", "last_assistant_message": "继续吗？"}
    assert action_for_payload(payload, settings).state == "input"
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python3 -m pytest tests/test_hook.py -v`

Expected: FAIL because `codex_lamp.hook` does not exist.

- [ ] **Step 3: Implement mapping, persistence, and fail-open execution**

Define `HookAction(kind: Literal["update", "remove", "ignore"], state: str | None)`. Use `time.time_ns()` for event ordering. Read one JSON object from stdin, default a missing session ID to `main`, update or remove the record, then spawn `python -m codex_lamp.daemon` with `start_new_session=True`, closed standard streams, and an environment containing `CODEX_LAMP_HOME`. Catch all exceptions in `main()`, append one concise line to `hook.log`, and return `0`.

Make `hook_runner.sh` resolve the approved data directory, use its venv Python when present, execute `python -m codex_lamp.hook`, append dependency errors to `hook.log`, and always `exit 0`.

- [ ] **Step 4: Verify behavior and shell syntax**

Run: `python3 -m pytest tests/test_hook.py -v && bash -n plugins/codex-lamp/scripts/hook_runner.sh`

Expected: tests pass and `bash -n` exits 0.

- [ ] **Step 5: Commit**

```bash
git add plugins/codex-lamp/src/codex_lamp/hook.py plugins/codex-lamp/scripts/hook_runner.sh tests/test_hook.py
git commit -m "feat: route Codex hooks to lamp state"
```

### Task 6: Persistent BLE daemon

**Files:**
- Create: `plugins/codex-lamp/src/codex_lamp/daemon.py`
- Create: `tests/test_daemon.py`

**Interfaces:**
- Consumes: `Settings`, `StateStore`, `commands_for_state()`, and an optional `LampTransport`.
- Produces: `LampTransport` protocol, `BleakLampTransport`, `apply_state(transport: LampTransport, state: str, settings: Settings) -> None`, `run_daemon()`, `main()`.

- [ ] **Step 1: Write failing mocked BLE tests**

```python
import asyncio
from codex_lamp.config import load_settings
from codex_lamp.daemon import apply_state


class FakeTransport:
    def __init__(self):
        self.commands = []

    async def send(self, command: str):
        self.commands.append(command)


def test_apply_state_sends_commands_in_order(tmp_path):
    transport = FakeTransport()
    asyncio.run(apply_state(transport, "input", load_settings(tmp_path)))
    assert transport.commands[-1] == "COLOR200000255"
```

Add tests that a duplicate state sends nothing, a simulated disconnect triggers reconnect and state replay, device UUID selection wins over name-prefix selection, and a second daemon exits when the lock is held.

- [ ] **Step 2: Run tests and verify failure**

Run: `python3 -m pytest tests/test_daemon.py -v`

Expected: FAIL because `codex_lamp.daemon` does not exist.

- [ ] **Step 3: Implement transport and daemon loop**

Define an async transport interface with `discover(settings)`, `connect(device)`, `send(command)`, `is_connected`, and `close()`. Implement it with `BleakScanner.discover`, `BleakClient`, and `write_gatt_char(..., response=True)`. Use delays `[1, 2, 4, 8, 16]`, then rescan. Watch the effective-state file at the configured 200 ms interval, send only on change, replay after reconnect, and turn off during graceful shutdown. Use `fcntl.flock` on `daemon.lock`; write and clean `daemon.pid`; rotate logs with `RotatingFileHandler`.

- [ ] **Step 4: Run focused tests**

Run: `python3 -m pytest tests/test_daemon.py -v`

Expected: all mocked BLE and lifecycle tests pass without Bluetooth hardware.

- [ ] **Step 5: Commit**

```bash
git add plugins/codex-lamp/src/codex_lamp/daemon.py tests/test_daemon.py
git commit -m "feat: maintain persistent Halo BLE connection"
```

### Task 7: CLI, setup runtime, and diagnostics

**Files:**
- Create: `plugins/codex-lamp/src/codex_lamp/cli.py`
- Create: `plugins/codex-lamp/scripts/setup.sh`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: configuration, state store, daemon status files, and Moonside transport.
- Produces: `build_parser()`, `main(argv: list[str] | None = None) -> int`, and the eight approved subcommands.

- [ ] **Step 1: Write failing CLI tests**

```python
from codex_lamp.cli import main


def test_dry_run_prints_all_states(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_LAMP_HOME", str(tmp_path))
    assert main(["test", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "idle" in output
    assert "working" in output
    assert "input" in output
    assert "LEDOFF" in output


def test_doctor_reports_missing_bleak_without_crashing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_LAMP_HOME", str(tmp_path))
    assert main(["doctor", "--json"]) in (0, 1)
    assert '"python"' in capsys.readouterr().out
```

Add tests for `status`, configuration updates with validation, recent log output, and safe uninstall against a temporary home.

- [ ] **Step 2: Run tests and verify failure**

Run: `python3 -m pytest tests/test_cli.py -v`

Expected: FAIL because `codex_lamp.cli` does not exist.

- [ ] **Step 3: Implement argparse commands**

Implement `setup`, `scan`, `test`, `status`, `config`, `doctor`, `logs`, and `uninstall`. Make `doctor --json` emit stable keys `python`, `bleak`, `platform`, `bluetooth`, `device`, `daemon`, `hooks`, and `ok`. Make hardware tests require explicit invocation. Validate that uninstall targets the resolved Codex Lamp home, signal the recorded daemon PID, attempt `LEDOFF`, and remove only that directory.

Make `setup.sh` create `~/Library/Application Support/CodexLamp/venv` unless `CODEX_LAMP_HOME` overrides the data root, install the plugin package with `python -m pip install --upgrade`, create `~/.local/bin/codex-lamp` as a symlink to the venv entry point, and run `codex-lamp setup`.

- [ ] **Step 4: Run focused tests and shell syntax**

Run: `python3 -m pytest tests/test_cli.py -v && bash -n plugins/codex-lamp/scripts/setup.sh`

Expected: tests pass and `bash -n` exits 0.

- [ ] **Step 5: Commit**

```bash
git add plugins/codex-lamp/src/codex_lamp/cli.py plugins/codex-lamp/scripts/setup.sh tests/test_cli.py
git commit -m "feat: add setup diagnostics and test CLI"
```

### Task 8: Codex plugin, skill, Marketplace, and local installer

**Files:**
- Create: `plugins/codex-lamp/.codex-plugin/plugin.json`
- Create: `plugins/codex-lamp/hooks/hooks.json`
- Create: `plugins/codex-lamp/skills/codex-lamp/SKILL.md`
- Create: `plugins/codex-lamp/skills/codex-lamp/agents/openai.yaml`
- Create: `plugins/codex-lamp/assets/icon.svg`
- Create: `.agents/plugins/marketplace.json`
- Create: `install.sh`
- Create: `tests/test_packaging.py`

**Interfaces:**
- Consumes: `hook_runner.sh`, `setup.sh`, CLI commands, and Codex plugin/Marketplace schemas.
- Produces: installable plugin `codex-lamp` and repository Marketplace `codex-lamp-marketplace`.

- [ ] **Step 1: Write failing packaging tests**

```python
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_plugin_declares_skill_and_hooks():
    manifest = json.loads((ROOT / "plugins/codex-lamp/.codex-plugin/plugin.json").read_text())
    assert manifest["name"] == "codex-lamp"
    assert manifest["skills"] == "./skills/"
    assert manifest["hooks"] == "./hooks/hooks.json"


def test_hooks_cover_approved_lifecycle_events():
    hooks = json.loads((ROOT / "plugins/codex-lamp/hooks/hooks.json").read_text())["hooks"]
    assert set(hooks) == {"SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest", "Stop", "SessionEnd"}
    assert all("${PLUGIN_ROOT}/scripts/hook_runner.sh" in group[0]["hooks"][0]["command"] for group in hooks.values())
```

Add tests for valid Marketplace relative paths, `SKILL.md` frontmatter containing only `name` and `description`, executable shell scripts, and `bash -n install.sh`.

- [ ] **Step 2: Run tests and verify failure**

Run: `python3 -m pytest tests/test_packaging.py -v`

Expected: FAIL because plugin packaging files do not exist.

- [ ] **Step 3: Create the plugin and Marketplace files**

Set plugin version `0.1.0`, license `MIT`, category `Developer Tools`, display name `Codex Lamp`, and a concise description of Moonside Halo status lighting. Point `skills` and `hooks` to the approved relative paths. Configure all six events to run `bash "${PLUGIN_ROOT}/scripts/hook_runner.sh"` with a 3-second timeout; the router reads the event payload from stdin.

Write a focused skill that tells Codex to run setup, doctor, scan, dry-run tests, hardware tests, configuration changes, log inspection, and uninstall without bypassing hook trust. Generate `agents/openai.yaml` with display name `Codex Lamp`, short description `Control a Moonside Halo from Codex state`, and a default diagnostic prompt.

Make `install.sh` verify macOS/Python/Codex, run the plugin setup script, call `codex plugin marketplace add` with the current repository root, and print the exact remaining `/plugins` and `/hooks` steps. It must not edit `~/.codex/config.toml` or trust hooks automatically.

- [ ] **Step 4: Validate packaging and skill**

Run:

```bash
python3 -m pytest tests/test_packaging.py -v
python3 /root/.codex/skills/oai/skill-creator/scripts/quick_validate.py plugins/codex-lamp/skills/codex-lamp
bash -n install.sh plugins/codex-lamp/scripts/hook_runner.sh plugins/codex-lamp/scripts/setup.sh
```

Expected: packaging tests pass, skill validation reports valid, and shell syntax exits 0.

- [ ] **Step 5: Commit**

```bash
git add .agents plugins/codex-lamp/.codex-plugin plugins/codex-lamp/hooks plugins/codex-lamp/skills plugins/codex-lamp/assets install.sh tests/test_packaging.py
git commit -m "feat: package Codex Lamp plugin and marketplace"
```

### Task 9: Documentation, licensing, and end-to-end simulation

**Files:**
- Create: `README.md`
- Create: `README.zh-CN.md`
- Create: `LICENSE`
- Create: `THIRD_PARTY_NOTICES.md`
- Create: `tests/test_integration.py`

**Interfaces:**
- Consumes: the complete plugin, CLI, mocked transport, and local installer.
- Produces: open-source documentation and a no-hardware end-to-end acceptance test.

- [ ] **Step 1: Write the failing end-to-end simulation**

```python
import json
from codex_lamp.config import load_settings
from codex_lamp.hook import process_payload
from codex_lamp.state import StateStore


def test_codex_turn_lifecycle_without_hardware(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_LAMP_HOME", str(tmp_path))
    settings = load_settings(tmp_path)
    process_payload({"session_id": "a", "hook_event_name": "SessionStart"}, settings, launch=False)
    process_payload({"session_id": "a", "hook_event_name": "UserPromptSubmit"}, settings, launch=False)
    assert StateStore(settings).effective() == "working"
    process_payload({"session_id": "a", "hook_event_name": "Stop", "last_assistant_message": "完成。"}, settings, launch=False)
    assert StateStore(settings).effective() == "idle"
    process_payload({"session_id": "a", "hook_event_name": "SessionEnd"}, settings, launch=False)
    assert StateStore(settings).effective() == "off"
```

Add a second integration test with two sessions proving `input > working > idle`, plus this timing assertion with daemon launch disabled:

```python
import time


def test_hook_path_stays_under_100ms(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_LAMP_HOME", str(tmp_path))
    settings = load_settings(tmp_path)
    started = time.perf_counter()
    process_payload(
        {"session_id": "timing", "hook_event_name": "UserPromptSubmit"},
        settings,
        launch=False,
    )
    assert time.perf_counter() - started < 0.1
```

- [ ] **Step 2: Run the integration tests and verify the new assertions expose any gaps**

Run: `python3 -m pytest tests/test_integration.py -v`

Expected: tests fail until all public interfaces and timing behavior are integrated consistently.

- [ ] **Step 3: Complete documentation and licensing**

Write English and Chinese READMEs with prerequisites, local installation, GitHub Marketplace installation, manual plugin and hook-trust steps, state table, customization, `doctor`, dry-run and hardware tests, upgrade, uninstall, architecture, troubleshooting, safety warning, and contribution guidance.

Use the MIT license text for the new project. In `THIRD_PARTY_NOTICES.md`, name `bobek-balinek/claude-lamp`, link its repository, state that its Moonside protocol and daemon approach informed this project, and preserve the exact upstream notice `Copyright (c) 2026 Bobby Bobak` followed by the complete MIT permission and warranty text.

- [ ] **Step 4: Run the complete verification suite**

Run:

```bash
python3 -m pytest -v
python3 -m pytest tests/test_integration.py -v
python3 -m pytest tests/test_integration.py::test_hook_path_stays_under_100ms -v
python3 /root/.codex/skills/oai/skill-creator/scripts/quick_validate.py plugins/codex-lamp/skills/codex-lamp
bash -n install.sh plugins/codex-lamp/scripts/*.sh
git diff --check
```

Expected: all tests pass, the skill validates, all shell scripts parse, and `git diff --check` prints nothing.

- [ ] **Step 5: Commit**

```bash
git add README.md README.zh-CN.md LICENSE THIRD_PARTY_NOTICES.md tests/test_integration.py
git commit -m "docs: prepare Codex Lamp for open source release"
```

### Task 10: Local Marketplace and hardware handoff

**Files:**
- Modify only if verification exposes an issue: files already listed in Tasks 1–9.
- Record no hardware-specific identifiers in the repository.

**Interfaces:**
- Consumes: completed repository and the owner's macOS Codex App/CLI with Moonside Halo.
- Produces: verified local Marketplace installation and a concise hardware acceptance report.

- [ ] **Step 1: Install from the local checkout**

Run: `./install.sh`

Expected: runtime setup succeeds, the local Marketplace is registered, and the script prints manual `/plugins` and `/hooks` instructions without modifying trust settings.

- [ ] **Step 2: Run safe diagnostics before hardware commands**

Run: `codex-lamp doctor --json && codex-lamp test --dry-run`

Expected: JSON reports Python, `bleak`, platform, daemon, and hook readiness; dry-run prints commands for `idle`, `working`, `input`, and `off`.

- [ ] **Step 3: Complete manual Codex trust steps**

In Codex App or CLI, open `/plugins`, install Codex Lamp from the local Marketplace, then open `/hooks`, inspect the exact hook command, and trust it.

Expected: the plugin is enabled and all six lifecycle hooks are trusted.

- [ ] **Step 4: Run Halo acceptance**

Run: `codex-lamp scan && codex-lamp test`

Expected: Halo is discovered and displays `idle → working → input → off`; two concurrent Codex sessions preserve `input > working > idle`; power-cycling the lamp triggers reconnect and state restore.

- [ ] **Step 5: Commit any verified fixes and tag the release candidate**

If hardware testing required fixes, rerun the complete Task 9 suite and commit only those fixes. Then run:

```bash
git status --short
git tag -a v0.1.0-rc1 -m "Codex Lamp v0.1.0 release candidate"
```

Expected: working tree is clean and the annotated local release-candidate tag exists.
