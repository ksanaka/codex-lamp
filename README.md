# Codex Lamp

[简体中文](README.zh-CN.md)

Codex Lamp turns a Moonside Halo into an ambient status light for Codex. Fast,
fail-open lifecycle hooks write local session state; a separate daemon keeps one
Bluetooth Low Energy connection to the lamp. Multiple Codex sessions are
aggregated with `input > working > idle > off` priority.

Codex Lamp never needs network access at runtime. Hook processing does not wait
for Bluetooth, and a missing lamp, missing permission, or daemon failure does
not interrupt Codex.

## Prerequisites

- macOS (the first release does not support other operating systems)
- Python 3.10 or newer, including the `venv` module
- Codex CLI available as `codex`
- a Codex surface with `/plugins` and `/hooks`
- a powered Moonside Halo and macOS Bluetooth access for discovery and physical
  tests; neither is required for `test --dry-run`

The installer creates an isolated runtime under
`~/Library/Application Support/CodexLamp/`, installs `bleak>=0.22,<2`, and
places a `codex-lamp` symlink in `~/.local/bin`. Add that directory to `PATH` if
your shell does not already include it.

## Install from the local ZIP

The supported current installation is the local ZIP and local Marketplace
flow. Download `codex-lamp-v0.1.0-local.zip`, open Terminal in the directory
that contains it, and extract it:

```bash
unzip codex-lamp-v0.1.0-local.zip
cd codex-lamp
./install.sh
```

The script validates macOS, Python, and Codex; creates or upgrades the isolated
runtime; registers the extracted directory as the `codex-lamp-marketplace`;
and prints the remaining manual steps. It does not edit
`~/.codex/config.toml`, install the plugin for you, or approve a hook.

Finish explicitly in Codex:

1. Open `/plugins`, select the **Codex Lamp** marketplace, and install
   **Codex Lamp**.
2. Open `/hooks` and inspect the six entries that run
   `bash "${PLUGIN_ROOT}/scripts/hook_runner.sh"`.
3. Trust those hooks only if you approve the command and this extracted copy.
4. Trigger a Codex session event, then run `codex-lamp doctor`.

Plugin installation does not imply hook trust. Codex skips untrusted bundled
hooks, and a changed hook definition must be reviewed again. Do not bypass this
review with a dangerous trust flag or a hand-edited approval.

## After GitHub publication

No GitHub repository exists for Codex Lamp yet. Do not use an invented
repository URL or Marketplace source; use the local ZIP flow above. After a
real repository is published, this section will provide its exact clone and
`codex plugin marketplace add` commands. Marketplace source syntax is
documented in [Package your plugin](https://developers.openai.com/plugins/build/plugins#add-a-marketplace-from-the-cli).

## Lamp states

| Effective state | Codex trigger | Default Halo output |
| --- | --- | --- |
| `input` | `PermissionRequest`, or a completed response that clearly asks for user input | Solid purple, RGB `200,0,255` |
| `working` | `UserPromptSubmit` or `PreToolUse` | `BEAT2` white/navy theme |
| `idle` | `SessionStart`, or ordinary completion | Solid sunset mango, RGB `255,180,50` |
| `off` | No active sessions remain, or all records are stale | LEDs off |

With concurrent sessions, the highest state wins:
`input > working > idle > off`. A session record expires after 1,800 seconds by
default. The `Stop` classifier recognizes terminal `?`/`？` and configured
Chinese or English phrases; uncertain completions become `idle`.

## Commands

```bash
codex-lamp setup
codex-lamp scan
codex-lamp status
codex-lamp status --json
codex-lamp config
codex-lamp doctor
codex-lamp doctor --json
codex-lamp test --dry-run
codex-lamp test
codex-lamp logs --lines 100
codex-lamp uninstall
```

- `setup` creates the default configuration if it is absent; it does not
  overwrite existing values.
- `scan` performs real Bluetooth discovery and prints the selected device.
- `status` reports daemon PID/running state, device selector, active session
  count, and effective state.
- `doctor` checks Python, `bleak`, macOS, Bluetooth, device discovery, daemon,
  and bundled-hook file discovery. It cannot determine whether you approved the
  hook in `/hooks`, and it exits nonzero if any reported check fails.
- `test --dry-run` prints every state command without accessing Bluetooth.
- `test` connects to real hardware and displays `idle`, `working`, `input`, and
  `off` for one second each.
- `logs` prints the log directory and the requested number of recent lines.

Immediately after setup, `doctor` may report `daemon: failed`: the daemon is
launched by the first trusted lifecycle hook, not by `setup`.

## Configuration

List all supported values, read one, or update one:

```bash
codex-lamp config
codex-lamp config brightness
codex-lamp config idle_color 32,64,128
codex-lamp config device_uuid null
codex-lamp config question_markers 'please confirm,would you like,请确认,请选择'
```

| Key | Default | Format or constraint |
| --- | --- | --- |
| `device_name_prefix` | `MOONSIDE` | Device-name prefix used when no UUID is set |
| `device_uuid` | `null` | macOS CoreBluetooth UUID; `none` or `null` clears it |
| `idle_color` | `255,180,50` | Three comma-separated integers in `0..255` |
| `input_color` | `200,0,255` | Three comma-separated integers in `0..255` |
| `working_command` | `THEME.BEAT2.255,255,255,0,0,140,` | Raw Moonside theme command; quote it in the shell |
| `brightness` | `120` | Integer in `0..120` |
| `stale_seconds` | `1800` | Positive integer number of seconds |
| `poll_interval` | `0.2` | Positive daemon polling interval in seconds |
| `relaunch_cooldown` | `30` | Positive integer retained for compatibility; the current hook path does not consult it |
| `question_markers` | Chinese/English defaults | Comma-separated phrases; empty items are ignored |

Configuration lives at
`~/Library/Application Support/CodexLamp/config.json`. Advanced users can set
`CODEX_LAMP_HOME` before setup to use another data root; use the same resolved
value for Codex and later CLI commands so they do not create separate runtimes.
The state priority is fixed and cannot be reordered.

After changing configuration, run:

```bash
codex-lamp doctor --json
codex-lamp status --json
codex-lamp test --dry-run
```

Only run `codex-lamp test` when physical lamp control is intended.

## Upgrade

For the current local deployment, download the newer local ZIP release and
extract it to a new `codex-lamp` directory. From that new directory, refresh
the isolated runtime:

```bash
./plugins/codex-lamp/scripts/setup.sh
```

Run `./install.sh` instead if the new extracted directory also needs to become
the registered local Marketplace source. Open `/plugins` to update or reinstall
Codex Lamp, then restart the ChatGPT desktop app when required. Open `/hooks`
and review the current definitions again before trusting any changed hook.
Runtime setup uses `pip install --upgrade` but preserves an existing
`config.json`. Git-backed Marketplace refresh instructions will be added after
a real GitHub repository is published.

## Uninstall

First remove only the local runtime data:

```bash
codex-lamp uninstall
```

The command shows the exact data directory for confirmation, verifies that it
is an initialized and safe target, stops a verified daemon when possible,
attempts `LEDOFF` with a bounded timeout, and removes that one directory. Use
`codex-lamp uninstall --yes` only after independently confirming the target.

Runtime uninstall does not remove or disable the plugin, unregister the
Marketplace, delete the extracted directory, or remove the
`~/.local/bin/codex-lamp` symlink. Finish separately:

1. Open `/plugins` and disable or remove **Codex Lamp**.
2. Optionally run
   `codex plugin marketplace remove codex-lamp-marketplace`.
3. Remove the now-unused `~/.local/bin/codex-lamp` symlink and extracted
   directory if they still exist and you no longer need them.

## Architecture

```text
Codex lifecycle event (JSON on stdin)
        |
        v
fail-open hook runner -> hook router -> locked per-session JSON records
                                             |
                                             v
                              effective state aggregation
                                             |
                                             v
                             single persistent BLE daemon
                                             |
                                             v
                               Moonside Nordic UART Service
```

The hook router performs local atomic file updates and detached daemon launch;
it never performs BLE discovery. Session filenames are SHA-256 digests, writes
use temporary-file replacement, and a file lock protects aggregation. The
daemon owns a separate process lock, suppresses duplicate state writes,
reconnects with bounded backoff, reapplies state after reconnect, and attempts
to turn the lamp off on shutdown. Its daemon log rotates under the data root.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| `codex-lamp: command not found` | Add `~/.local/bin` to `PATH`, or from the extracted `codex-lamp` directory rerun `./plugins/codex-lamp/scripts/setup.sh` (or `./install.sh`). |
| `doctor` reports `platform: failed` | Use macOS; other platforms are outside this release. |
| `bleak` is missing | Rerun `./plugins/codex-lamp/scripts/setup.sh`. |
| `bluetooth` fails | Turn on Bluetooth and grant the host app/terminal Bluetooth permission in macOS System Settings. |
| `device` fails | Power the Halo, move it nearby, run `codex-lamp scan`, and configure its UUID if name discovery is ambiguous. |
| `daemon` fails | Install and trust the hook, trigger a session event, then inspect `codex-lamp logs --lines 100`. |
| `hooks` fails | Confirm the plugin is installed and rerun its setup so the hook file is discoverable. Separately review trust in `/hooks`; `doctor` cannot verify approval. |
| The state appears stuck | Check `status --json`, concurrent sessions, and `stale_seconds`; ended or stale sessions are removed automatically. |
| The lamp disconnects | Leave the daemon running; it rescans with bounded backoff and reapplies the effective state after reconnect. |
| Codex continues while the lamp fails | This is intentional fail-open behavior; inspect the logs for the hardware/runtime error. |

## Safety

`codex-lamp test`, `scan`, normal trusted hooks, and uninstall can access real
Bluetooth hardware. The working theme is animated and may pulse. Warn anyone
sensitive to flashing light, keep the lamp out of safety-critical signaling,
and use `test --dry-run` before an intentional physical test. Review every hook
command in `/hooks`; never bypass Codex's trust decision. Uninstall only after
checking the exact displayed data path.

## Contributing

Keep hook behavior fail-open and free of synchronous BLE work. Add a failing,
behavioral test before changing runtime behavior, and use mocked transports for
automated BLE coverage. A typical development setup is:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ./plugins/codex-lamp pytest PyYAML
.venv/bin/python -m pytest -v
bash -n install.sh plugins/codex-lamp/scripts/*.sh
git diff --check
```

Changes to the plugin manifest, Marketplace, skill, or hooks should also pass
the current Codex plugin and skill validators. Physical hardware tests require
explicit approval and belong in a separate macOS acceptance pass.

## License and attribution

New project code is available under the [MIT License](LICENSE). The Moonside
protocol and daemon approach were informed by
[bobek-balinek/claude-lamp](https://github.com/bobek-balinek/claude-lamp); its
MIT notice is preserved in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
