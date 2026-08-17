#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SETUP_SCRIPT="${REPO_ROOT}/plugins/codex-lamp/scripts/setup.sh"
MARKETPLACE="${REPO_ROOT}/.agents/plugins/marketplace.json"

if [[ "$(uname -s)" != "Darwin" ]]; then
    printf 'Codex Lamp requires macOS.\n' >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    printf 'Codex Lamp requires Python 3.10 or newer.\n' >&2
    exit 1
fi
if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    printf 'Codex Lamp requires Python 3.10 or newer.\n' >&2
    exit 1
fi

if ! command -v codex >/dev/null 2>&1; then
    printf 'Codex Lamp requires the Codex CLI.\n' >&2
    exit 1
fi

if [[ ! -x "${SETUP_SCRIPT}" || ! -f "${MARKETPLACE}" ]]; then
    printf 'Run this installer from a complete Codex Lamp checkout.\n' >&2
    exit 1
fi

"${SETUP_SCRIPT}"
codex plugin marketplace add "${REPO_ROOT}"

printf '\nRuntime setup is complete. Finish installation explicitly in Codex:\n'
printf '1. Open /plugins, select Codex Lamp, and install the plugin.\n'
printf '2. Open /hooks, review bash "${PLUGIN_ROOT}/scripts/hook_runner.sh", and trust it only if you approve it.\n'
printf 'The installer does not bypass or pre-approve Codex hook trust.\n'
