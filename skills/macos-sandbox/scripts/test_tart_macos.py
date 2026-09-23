#!/usr/bin/env -S uv run --script

# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = [
#     "pytest>=8.0",
#     "python-decouple>=3.8",
# ]
# [tool.uv]
# exclude-newer = "2026-10-01T00:00:00Z"
# ///

# pyright: reportMissingImports=false

"""
Usage:
    ./test_tart_macos.py [pytest args...]

Note:
    PEP 723 self-contained test suite for tart_macos.py -- run directly,
    `uv run` resolves pytest + python-decouple into an isolated environment.
"""

import importlib.util
import pytest
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

SCRIPT_DIR = Path(__file__).resolve().parent
TART_MACOS_PATH = SCRIPT_DIR / "tart_macos.py"

spec = importlib.util.spec_from_file_location("tart_macos", TART_MACOS_PATH)
tart_macos = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tart_macos)


def fake_completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# --- config precedence ---


def test_config_default_when_nothing_set(monkeypatch):
    monkeypatch.delenv("TART_MACOS_CPU", raising=False)
    config = tart_macos.load_config(None)
    assert config("TART_MACOS_CPU", default=2, cast=int) == 2


def test_config_process_env_overrides_default(monkeypatch):
    monkeypatch.setenv("TART_MACOS_CPU", "4")
    config = tart_macos.load_config(None)
    assert config("TART_MACOS_CPU", default=2, cast=int) == 4


def test_config_env_file_value_used(tmp_path, monkeypatch):
    monkeypatch.delenv("TART_MACOS_CPU", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("TART_MACOS_CPU=8\n")
    config = tart_macos.load_config(env_file)
    assert config("TART_MACOS_CPU", default=2, cast=int) == 8


def test_config_process_env_overrides_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("TART_MACOS_CPU=8\n")
    monkeypatch.setenv("TART_MACOS_CPU", "16")
    config = tart_macos.load_config(env_file)
    assert config("TART_MACOS_CPU", default=2, cast=int) == 16


def test_cli_flag_beats_env_default(monkeypatch):
    monkeypatch.setenv("TART_MACOS_SOFTNET", "true")
    args = tart_macos.parse_args(["up", "myvm", "--softnet"])
    assert args.softnet is True


# --- argv builders ---


def test_build_clone_argv():
    assert tart_macos.build_clone_argv("ghcr.io/x/y:latest", "dest") == ["tart", "clone", "ghcr.io/x/y:latest", "dest"]


def test_build_set_argv():
    assert tart_macos.build_set_argv("vm", 2, 4096, "1280x800") == [
        "tart",
        "set",
        "vm",
        "--cpu",
        "2",
        "--memory",
        "4096",
        "--display",
        "1280x800",
    ]


def test_build_run_argv_headless_by_default():
    argv = tart_macos.build_run_argv("vm")
    assert argv == ["tart", "run", "vm", "--no-graphics", "--no-audio", "--no-clipboard"]


def test_build_run_argv_softnet():
    argv = tart_macos.build_run_argv("vm", softnet=True)
    assert argv[-1] == "--net-softnet"


def test_build_ssh_argv_uses_sshpass_and_no_host_key_checking():
    argv = tart_macos.build_ssh_argv("10.0.0.5", "admin", "admin", "true")
    assert argv[0] == "sshpass"
    assert "-p" in argv and "admin" in argv
    assert "StrictHostKeyChecking=no" in " ".join(argv)
    assert "admin@10.0.0.5" in argv


def test_build_mcp_command():
    cmd = tart_macos.build_mcp_command(
        "osascript-vm", "10.0.0.5", "admin", "admin", "git+https://github.com/pythoninthegrass/osascript-mcp"
    )
    assert cmd[:4] == ["claude", "mcp", "add", "osascript-vm"]
    assert "sshpass" in cmd
    assert "admin@10.0.0.5" in cmd
    joined = " ".join(cmd)
    assert "uvx --from git+https://github.com/pythoninthegrass/osascript-mcp osascript-mcp" in joined


# --- doctor ---


def test_doctor_reports_missing_tart(monkeypatch):
    monkeypatch.setattr(tart_macos.shutil, "which", lambda name: None if name == "tart" else "/usr/bin/" + name)
    problems = tart_macos.doctor()
    assert any("tart is not on PATH" in p for p in problems)


def test_doctor_reports_missing_sshpass(monkeypatch):
    monkeypatch.setattr(tart_macos.shutil, "which", lambda name: None if name == "sshpass" else "/usr/bin/" + name)
    monkeypatch.setattr(tart_macos, "run", lambda argv, **k: fake_completed(returncode=0))
    problems = tart_macos.doctor()
    assert any("sshpass is not on PATH" in p for p in problems)


def test_doctor_reports_tart_dyld_break(monkeypatch):
    monkeypatch.setattr(tart_macos.shutil, "which", lambda name: "/usr/bin/" + name)

    def fake_run(argv, **kwargs):
        if argv == ["tart", "--version"]:
            return fake_completed(returncode=1, stderr="dyld[1]: Library not loaded: @rpath/libswiftCompatibilitySpan.dylib")
        return fake_completed(returncode=0)

    monkeypatch.setattr(tart_macos, "run", fake_run)
    problems = tart_macos.doctor()
    assert any("2.34.0" in p for p in problems)


def test_doctor_reports_tart_generic_failure(monkeypatch):
    monkeypatch.setattr(tart_macos.shutil, "which", lambda name: "/usr/bin/" + name)

    def fake_run(argv, **kwargs):
        if argv == ["tart", "--version"]:
            return fake_completed(returncode=1, stderr="permission denied")
        return fake_completed(returncode=0)

    monkeypatch.setattr(tart_macos, "run", fake_run)
    problems = tart_macos.doctor()
    assert any("permission denied" in p for p in problems)


# --- dhcp_lease_warning ---


def test_dhcp_lease_warning_when_plist_key_missing(monkeypatch):
    monkeypatch.setattr(tart_macos, "run", lambda argv, **k: fake_completed(returncode=1, stderr="does not exist"))
    assert tart_macos.dhcp_lease_warning() is not None


def test_dhcp_lease_warning_when_still_default(monkeypatch):
    monkeypatch.setattr(tart_macos, "run", lambda argv, **k: fake_completed(returncode=0, stdout="DHCPLeaseTimeSecs = 86400;\n"))
    assert tart_macos.dhcp_lease_warning() is not None


def test_dhcp_lease_no_warning_when_already_shortened(monkeypatch):
    monkeypatch.setattr(tart_macos, "run", lambda argv, **k: fake_completed(returncode=0, stdout="DHCPLeaseTimeSecs = 600;\n"))
    assert tart_macos.dhcp_lease_warning() is None


def test_cmd_doctor_warns_but_does_not_fail_on_dhcp_lease(monkeypatch, capsys):
    monkeypatch.setattr(tart_macos, "doctor", lambda: [])
    monkeypatch.setattr(tart_macos, "dhcp_lease_warning", lambda: "lease tweak not set")
    rc = tart_macos.cmd_doctor()
    assert rc == tart_macos.EXIT_OK
    assert "WARN: lease tweak not set" in capsys.readouterr().err


def test_doctor_ok_when_everything_present(monkeypatch, tmp_path):
    monkeypatch.setattr(tart_macos.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(tart_macos, "run", lambda argv, **k: fake_completed(returncode=0))
    monkeypatch.setattr(tart_macos, "MIN_FREE_GB_DEFAULT", 0)
    import platform

    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    problems = tart_macos.doctor()
    assert problems == []


# --- down: refuses to delete the golden VM ---


def test_down_refuses_golden_without_flag():
    rc, message = tart_macos.down("gg-golden", "gg-golden", allow_golden=False)
    assert rc == tart_macos.EXIT_USAGE
    assert "golden" in message.lower()


def test_down_allows_golden_with_flag(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return fake_completed(returncode=0)

    monkeypatch.setattr(tart_macos, "run", fake_run)
    rc, message = tart_macos.down("gg-golden", "gg-golden", allow_golden=True)
    assert rc == tart_macos.EXIT_OK
    assert calls == [
        tart_macos.build_stop_argv("gg-golden"),
        tart_macos.build_delete_argv("gg-golden"),
    ]


def test_down_deletes_ephemeral_vm(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return fake_completed(returncode=0)

    monkeypatch.setattr(tart_macos, "run", fake_run)
    rc, message = tart_macos.down("gg-sbx-123", "gg-golden", allow_golden=False)
    assert rc == tart_macos.EXIT_OK
    assert calls == [
        tart_macos.build_stop_argv("gg-sbx-123"),
        tart_macos.build_delete_argv("gg-sbx-123"),
    ]


def test_down_reports_stop_failure(monkeypatch):
    def fake_run(argv, **kwargs):
        if argv[1] == "stop":
            return fake_completed(returncode=1, stderr="boom")
        return fake_completed(returncode=0)

    monkeypatch.setattr(tart_macos, "run", fake_run)
    rc, message = tart_macos.down("gg-sbx-123", "gg-golden", allow_golden=False)
    assert rc == tart_macos.EXIT_FAIL
    assert "boom" in message


def test_down_tolerates_already_stopped(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "stop":
            return fake_completed(returncode=1, stderr="VM is not running")
        return fake_completed(returncode=0)

    monkeypatch.setattr(tart_macos, "run", fake_run)
    rc, _ = tart_macos.down("gg-sbx-123", "gg-golden", allow_golden=False)
    assert rc == tart_macos.EXIT_OK
    assert calls == [
        tart_macos.build_stop_argv("gg-sbx-123"),
        tart_macos.build_delete_argv("gg-sbx-123"),
    ]


# --- up: exercises the full clone -> run -> wait flow with a fake run() ---


def test_up_builds_clone_from_golden_and_waits_for_ip_and_ssh(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["tart", "ip"]:
            return fake_completed(returncode=0, stdout="10.0.0.9\n")
        return fake_completed(returncode=0)

    monkeypatch.setattr(tart_macos, "run", fake_run)
    monkeypatch.setattr(tart_macos, "run_vm", lambda name, softnet=False: MagicMock())
    monkeypatch.setattr(tart_macos, "wait_for_ssh", lambda *a, **k: True)

    args = tart_macos.parse_args(["up", "gg-sbx-test"])
    rc = tart_macos.cmd_up(args)

    assert rc == tart_macos.EXIT_OK
    assert calls[0] == tart_macos.build_clone_argv(tart_macos.GOLDEN_DEFAULT, "gg-sbx-test")


def test_up_fails_when_ip_never_appears(monkeypatch):
    def fake_run(argv, **kwargs):
        if argv[:2] == ["tart", "ip"]:
            return fake_completed(returncode=1, stderr="no ip")
        return fake_completed(returncode=0)

    monkeypatch.setattr(tart_macos, "run", fake_run)
    monkeypatch.setattr(tart_macos, "run_vm", lambda name, softnet=False: MagicMock())
    monkeypatch.setattr(tart_macos, "BOOT_TIMEOUT_DEFAULT", 0)

    args = tart_macos.parse_args(["up", "gg-sbx-test"])
    rc = tart_macos.cmd_up(args)
    assert rc == tart_macos.EXIT_FAIL


# --- mcp ---


def test_cmd_mcp_requires_a_name():
    args = tart_macos.parse_args(["mcp"])
    rc = tart_macos.cmd_mcp(args)
    assert rc == tart_macos.EXIT_USAGE


def test_cmd_mcp_prints_command(monkeypatch, capsys):
    monkeypatch.setattr(tart_macos, "get_ip", lambda name: "10.0.0.9")
    args = tart_macos.parse_args(["mcp", "gg-sbx-test"])
    rc = tart_macos.cmd_mcp(args)
    assert rc == tart_macos.EXIT_OK
    out = capsys.readouterr().out
    assert "claude mcp add osascript-vm --" in out
    assert "10.0.0.9" in out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
