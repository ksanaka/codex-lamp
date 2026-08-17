#!/usr/bin/env bash

# Codex invokes this script on its critical path, so every branch fails open.
data_home="${CODEX_LAMP_HOME:-${HOME}/Library/Application Support/CodexLamp}"
case "$data_home" in
    "~/"*) data_home="$HOME/${data_home#\~/}" ;;
esac
export CODEX_LAMP_HOME="$data_home"
log_path="$data_home/logs/hook.log"

mkdir -p "$(dirname "$log_path")" 2>/dev/null || exit 0

python_path="$data_home/venv/bin/python"
if [[ ! -x "$python_path" ]]; then
    python_path="$(command -v python3 2>/dev/null || true)"
fi

if [[ -z "$python_path" ]]; then
    printf 'hook runner error: Python unavailable\n' >> "$log_path" 2>/dev/null
    exit 0
fi

"$python_path" -m codex_lamp.hook 2>> "$log_path"
if [[ $? -ne 0 ]]; then
    printf 'hook runner error: hook process failed\n' >> "$log_path" 2>/dev/null
fi

exit 0
