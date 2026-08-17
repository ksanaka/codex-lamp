import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import sysconfig

import pytest

ROOT = Path(__file__).parents[1]
PLUGIN_ROOT = ROOT / "plugins/codex-lamp"


def _plugin_validator_path():
    configured_home = os.environ.get("CODEX_HOME")
    codex_home = (
        Path(configured_home).expanduser()
        if configured_home
        else Path.home() / ".codex"
    )
    return codex_home / "skills/.system/plugin-creator/scripts/validate_plugin.py"


def _run_plugin_validator():
    validator = _plugin_validator_path()
    if not validator.is_file():
        return None

    env = os.environ.copy()
    base_site_packages = sysconfig.get_path(
        "purelib",
        vars={"base": sys.base_prefix, "platbase": sys.base_prefix},
    )
    if Path(base_site_packages).is_dir():
        python_paths = [base_site_packages]
        if env.get("PYTHONPATH"):
            python_paths.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(python_paths)
    return subprocess.run(
        [sys.executable, validator, PLUGIN_ROOT],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _write_recording_validator(codex_home, record):
    validator = codex_home / "skills/.system/plugin-creator/scripts/validate_plugin.py"
    validator.parent.mkdir(parents=True)
    validator.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "import yaml as validator_dependency\n"
        "Path(os.environ['VALIDATOR_INVOCATION']).write_text(json.dumps({\n"
        "    'dependency': validator_dependency.__file__,\n"
        "    'executable': sys.executable,\n"
        "    'plugin_root': sys.argv[1],\n"
        "    'pythonpath': os.environ.get('PYTHONPATH', ''),\n"
        "    'sys_path': sys.path,\n"
        "}))\n"
    )
    return validator


def _validator_invocation(record):
    return json.loads(record.read_text())


def test_plugin_declares_skill_default_hooks_and_install_metadata():
    manifest = json.loads((PLUGIN_ROOT / ".codex-plugin/plugin.json").read_text())

    assert manifest["name"] == "codex-lamp"
    assert manifest["version"] == "0.1.0"
    assert manifest["license"] == "MIT"
    assert manifest["skills"] == "./skills/"
    assert "hooks" not in manifest
    assert (PLUGIN_ROOT / "hooks/hooks.json").is_file()
    assert manifest["author"] == {"name": "Codex Lamp Contributors"}
    assert manifest["interface"]["displayName"] == "Codex Lamp"
    assert manifest["interface"]["category"] == "Developer Tools"
    assert manifest["interface"]["composerIcon"] == "./assets/icon.svg"
    assert manifest["interface"]["longDescription"]
    assert manifest["interface"]["developerName"] == "Codex Lamp Contributors"
    assert manifest["interface"]["defaultPrompt"] == [
        "Diagnose my Codex Lamp setup and recommend the next safe step."
    ]
    assert manifest["interface"]["capabilities"] == [
        "Status Lighting",
        "Diagnostics",
    ]


def test_plugin_passes_current_ingestion_validator():
    result = _run_plugin_validator()
    if result is None:
        pytest.skip("system plugin validator is unavailable")

    assert result.returncode == 0, result.stdout + result.stderr


def test_plugin_validator_uses_codex_home_and_current_runtime(tmp_path, monkeypatch):
    codex_home = tmp_path / "portable-codex-home"
    record = tmp_path / "validator-invocation"
    inherited_paths = [tmp_path / "existing-one", tmp_path / "existing-two"]
    _write_recording_validator(codex_home, record)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("VALIDATOR_INVOCATION", str(record))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(map(str, inherited_paths)))

    result = _run_plugin_validator()

    assert result is not None
    assert result.returncode == 0, result.stdout + result.stderr
    invocation = _validator_invocation(record)
    base_site_packages = sysconfig.get_path(
        "purelib",
        vars={"base": sys.base_prefix, "platbase": sys.base_prefix},
    )
    assert invocation["executable"] == sys.executable
    assert invocation["plugin_root"] == str(PLUGIN_ROOT)
    assert Path(invocation["dependency"]).is_relative_to(base_site_packages)
    assert invocation["pythonpath"].split(os.pathsep) == [
        base_site_packages,
        *map(str, inherited_paths),
    ]
    assert base_site_packages in invocation["sys_path"]
    assert all(str(path) in invocation["sys_path"] for path in inherited_paths)


@pytest.mark.parametrize(
    "configured_home",
    [pytest.param(None, id="unset"), pytest.param("", id="empty")],
)
def test_plugin_validator_falls_back_to_current_user_codex_home(
    tmp_path, monkeypatch, configured_home
):
    user_home = tmp_path / "portable-user-home"
    codex_home = user_home / ".codex"
    record = tmp_path / "validator-invocation"
    _write_recording_validator(codex_home, record)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_home))
    if configured_home is None:
        monkeypatch.delenv("CODEX_HOME", raising=False)
    else:
        monkeypatch.setenv("CODEX_HOME", configured_home)
    monkeypatch.setenv("VALIDATOR_INVOCATION", str(record))

    result = _run_plugin_validator()

    assert result is not None
    assert result.returncode == 0, result.stdout + result.stderr
    invocation = _validator_invocation(record)
    assert invocation["executable"] == sys.executable
    assert invocation["plugin_root"] == str(PLUGIN_ROOT)


def test_plugin_validator_resolves_relative_codex_home_from_cwd(tmp_path, monkeypatch):
    relative_home = Path("relative-codex-home")
    record = tmp_path / "validator-invocation"
    _write_recording_validator(tmp_path / relative_home, record)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(relative_home))
    monkeypatch.setenv("VALIDATOR_INVOCATION", str(record))

    result = _run_plugin_validator()

    assert result is not None
    assert result.returncode == 0, result.stdout + result.stderr
    invocation = _validator_invocation(record)
    assert invocation["executable"] == sys.executable
    assert invocation["plugin_root"] == str(PLUGIN_ROOT)


def test_hooks_cover_approved_lifecycle_events():
    hooks = json.loads((PLUGIN_ROOT / "hooks/hooks.json").read_text())["hooks"]

    assert set(hooks) == {
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PermissionRequest",
        "Stop",
        "SessionEnd",
    }
    expected_handler = {
        "type": "command",
        "command": 'bash "${PLUGIN_ROOT}/scripts/hook_runner.sh"',
        "timeout": 3,
    }
    assert all(groups == [{"hooks": [expected_handler]}] for groups in hooks.values())


def test_marketplace_uses_existing_repo_relative_plugin_path():
    marketplace = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text())

    assert marketplace["name"] == "codex-lamp-marketplace"
    assert marketplace["interface"]["displayName"] == "Codex Lamp"
    assert len(marketplace["plugins"]) == 1
    entry = marketplace["plugins"][0]
    assert entry["name"] == "codex-lamp"
    assert entry["category"] == "Developer Tools"
    assert entry["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }
    assert entry["source"]["source"] == "local"

    source = entry["source"]["path"]
    parts = PurePosixPath(source).parts
    assert source.startswith("./")
    assert ".." not in parts
    assert (ROOT / source).resolve() == PLUGIN_ROOT.resolve()
    assert (ROOT / source / ".codex-plugin/plugin.json").is_file()


def test_skill_frontmatter_has_only_name_and_description():
    skill_text = (PLUGIN_ROOT / "skills/codex-lamp/SKILL.md").read_text()
    _, frontmatter, _ = skill_text.split("---", 2)
    metadata = {
        key.strip(): value.strip()
        for key, value in (line.split(":", 1) for line in frontmatter.splitlines() if line)
    }

    assert set(metadata) == {"name", "description"}
    assert metadata["name"] == "codex-lamp"


def test_skill_interface_has_required_fields():
    lines = (
        PLUGIN_ROOT / "skills/codex-lamp/agents/openai.yaml"
    ).read_text().splitlines()
    assert lines[0] == "interface:"
    interface = {
        key.strip(): json.loads(value.strip())
        for key, value in (line.split(":", 1) for line in lines[1:] if line.strip())
    }

    assert interface == {
        "display_name": "Codex Lamp",
        "short_description": "Control a Moonside Halo from Codex state",
        "default_prompt": (
            "Use $codex-lamp to diagnose my Codex Lamp setup and recommend the next safe step."
        ),
    }


def test_shell_scripts_are_executable_and_have_valid_syntax():
    scripts = [
        ROOT / "install.sh",
        PLUGIN_ROOT / "scripts/hook_runner.sh",
        PLUGIN_ROOT / "scripts/setup.sh",
    ]

    for script in scripts:
        assert script.stat().st_mode & stat.S_IXUSR
    subprocess.run(["bash", "-n", *scripts], check=True)


def test_installer_runs_setup_registers_repo_and_leaves_trust_manual(tmp_path):
    checkout = tmp_path / "checkout"
    setup_script = checkout / "plugins/codex-lamp/scripts/setup.sh"
    marketplace = checkout / ".agents/plugins/marketplace.json"
    bin_dir = tmp_path / "bin"
    calls = tmp_path / "calls"
    home = tmp_path / "home"
    setup_script.parent.mkdir(parents=True)
    marketplace.parent.mkdir(parents=True)
    bin_dir.mkdir()
    home.mkdir()
    shutil.copy2(ROOT / "install.sh", checkout / "install.sh")
    setup_script.write_text(
        '#!/usr/bin/env bash\nprintf "setup\\n" >> "$CODEX_LAMP_TEST_CALLS"\n'
    )
    setup_script.chmod(0o755)
    marketplace.write_text("{}\n")
    (bin_dir / "uname").write_text('#!/usr/bin/env bash\nprintf "Darwin\\n"\n')
    (bin_dir / "python3").write_text(
        '#!/usr/bin/env bash\n'
        'if [[ "$1" == "-c" ]]; then exit 0; fi\n'
        'printf "Python 3.12.0\\n"\n'
    )
    (bin_dir / "codex").write_text(
        '#!/usr/bin/env bash\nprintf "codex %s\\n" "$*" >> "$CODEX_LAMP_TEST_CALLS"\n'
    )
    for command in bin_dir.iterdir():
        command.chmod(0o755)

    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(home),
        "CODEX_LAMP_TEST_CALLS": str(calls),
    }
    result = subprocess.run(
        [checkout / "install.sh"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert calls.read_text().splitlines() == [
        "setup",
        f"codex plugin marketplace add {checkout}",
    ]
    assert "/plugins" in result.stdout
    assert "/hooks" in result.stdout
    assert "review" in result.stdout.lower()
    assert "trust" in result.stdout.lower()
    assert not (home / ".codex/config.toml").exists()
