#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RAW_DATA_ROOT="${CODEX_LAMP_HOME:-${HOME}/Library/Application Support/CodexLamp}"
if [[ "${RAW_DATA_ROOT}" == "~" ]]; then
    DATA_ROOT="${HOME}"
elif [[ "${RAW_DATA_ROOT}" == "~/"* ]]; then
    DATA_ROOT="${HOME}/${RAW_DATA_ROOT#\~/}"
elif [[ "${RAW_DATA_ROOT}" == /* ]]; then
    DATA_ROOT="${RAW_DATA_ROOT}"
else
    DATA_ROOT="$(pwd -P)/${RAW_DATA_ROOT}"
fi
mkdir -p "${DATA_ROOT}"
DATA_ROOT="$(cd "${DATA_ROOT}" && pwd -P)"
export CODEX_LAMP_HOME="${DATA_ROOT}"
export CODEX_PLUGIN_ROOT="${PLUGIN_ROOT}"
VENV_ROOT="${DATA_ROOT}/venv"
COMMAND_DIR="${HOME}/.local/bin"

python3 -m venv "${VENV_ROOT}"
"${VENV_ROOT}/bin/python" -m pip install --upgrade "${PLUGIN_ROOT}"
mkdir -p "${COMMAND_DIR}"
ln -sfn "${VENV_ROOT}/bin/codex-lamp" "${COMMAND_DIR}/codex-lamp"
"${COMMAND_DIR}/codex-lamp" setup
