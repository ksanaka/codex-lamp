---
name: codex-lamp
description: Use when setting up, testing, configuring, diagnosing, or uninstalling Codex Lamp or a Moonside Halo used for Codex status lighting.
---

# Codex Lamp

Use the stable `codex-lamp` CLI. Do not substitute generic USB/HID diagnostics for its Bluetooth checks.

## Setup and hook trust

Run:

```bash
codex-lamp setup
codex-lamp doctor --json
```

If the plugin is not installed, open `/plugins` and install **Codex Lamp** from the registered Marketplace. Then open `/hooks`, review `bash "${PLUGIN_ROOT}/scripts/hook_runner.sh"`, and let the user decide whether to trust it.

Never pass `--dangerously-bypass-hook-trust`, edit `~/.codex/config.toml` to approve hooks, or claim plugin installation trusted hooks. Changed hooks require review again.

## Diagnose safely

Start without changing the lamp:

```bash
codex-lamp doctor --json
codex-lamp status --json
codex-lamp test --dry-run
codex-lamp logs --lines 100
```

Use `codex-lamp scan` for supported Bluetooth discovery. Run the four-state hardware sequence only when the user explicitly asks for or approves a physical test:

```bash
codex-lamp test
```

Dry-run prints the approved BLE commands and does not access Bluetooth; `test` controls real hardware.

## Configure

List configuration with `codex-lamp config`, read one value with `codex-lamp config KEY`, and update one with `codex-lamp config KEY VALUE`. RGB values are comma-separated integers:

```bash
codex-lamp config idle_color 32,64,128
codex-lamp config brightness 100
```

Re-run `codex-lamp doctor --json` and `codex-lamp status --json` after changes.

## Uninstall

Run `codex-lamp uninstall` so the CLI confirms the exact local-data directory before stopping the daemon, attempting `LEDOFF`, and removing only that directory. Use `codex-lamp uninstall --yes` only after the user explicitly confirms unattended removal. The command does not remove the plugin; disable or remove **Codex Lamp** through `/plugins` afterward.
