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
    ./test_run.py [pytest args...]

Note:
    PEP 723 self-contained test suite for run.py -- covers the pure
    plumbing (state file, inventory shape, playbook resolution) without
    invoking ansible-playbook or tart_macos.py's own subprocess calls,
    which belong to their own test suites.
"""

import importlib.util
import json
import pytest
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

SCRIPT_DIR = Path(__file__).resolve().parent
RUN_PATH = SCRIPT_DIR / "run.py"

spec = importlib.util.spec_from_file_location("run", RUN_PATH)
run_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_mod)


def fake_completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_state_round_trip(tmp_path):
    assert run_mod.read_state(tmp_path) is None
    run_mod.write_state(tmp_path, "gg-sbx-1", "10.0.0.9")
    state = run_mod.read_state(tmp_path)
    assert state["name"] == "gg-sbx-1"
    assert state["ip"] == "10.0.0.9"
    assert "started_at" in state


def test_build_inventory_shape():
    inv = run_mod.build_inventory("gg-sbx-1", "10.0.0.9", "admin")
    assert inv["sandbox"]["hosts"] == {"gg-sbx-1": {"ansible_host": "10.0.0.9"}}
    assert inv["sandbox"]["vars"]["ansible_user"] == "admin"
    assert "ansible_ssh_pass" not in inv["sandbox"]["vars"]
    assert "ansible_ssh_private_key_file" not in inv["sandbox"]["vars"]


def test_resolve_playbook_explicit_path(tmp_path):
    playbook = tmp_path / "custom.yml"
    playbook.write_text("---\n")
    resolved = run_mod.resolve_playbook(str(playbook), tmp_path)
    assert resolved == playbook.resolve()


def test_resolve_playbook_default_in_repo(tmp_path):
    (tmp_path / "playbook.yml").write_text("---\n")
    resolved = run_mod.resolve_playbook(None, tmp_path)
    assert resolved == (tmp_path / "playbook.yml").resolve()


def test_resolve_playbook_missing_returns_none(tmp_path):
    assert run_mod.resolve_playbook(None, tmp_path) is None


def test_cmd_up_skips_provisioning_without_playbook(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        run_mod,
        "run_tart_macos",
        lambda argv, **k: fake_completed(returncode=0, stdout=json.dumps({"name": "gg-sbx-1", "ip": "10.0.0.9"})),
    )
    run_playbook_mock = MagicMock()
    monkeypatch.setattr(run_mod, "run_playbook", run_playbook_mock)

    args = run_mod.parse_args(["up", "--repo", str(tmp_path)])
    rc = run_mod.cmd_up(args)

    assert rc == run_mod.tart_macos.EXIT_OK
    run_playbook_mock.assert_not_called()
    assert "no playbook found" in capsys.readouterr().err
    state = run_mod.read_state(tmp_path)
    assert state["name"] == "gg-sbx-1"
    assert state["ip"] == "10.0.0.9"


def test_cmd_up_runs_playbook_when_present(monkeypatch, tmp_path):
    (tmp_path / "playbook.yml").write_text("---\n")
    monkeypatch.setattr(
        run_mod,
        "run_tart_macos",
        lambda argv, **k: fake_completed(returncode=0, stdout=json.dumps({"name": "gg-sbx-1", "ip": "10.0.0.9"})),
    )
    calls = []
    monkeypatch.setattr(run_mod, "run_playbook", lambda path, inv: calls.append((path, inv)) or 0)

    args = run_mod.parse_args(["up", "--repo", str(tmp_path)])
    rc = run_mod.cmd_up(args)

    assert rc == run_mod.tart_macos.EXIT_OK
    assert len(calls) == 1
    assert calls[0][0] == (tmp_path / "playbook.yml").resolve()


def test_cmd_up_fails_when_playbook_fails(monkeypatch, tmp_path):
    (tmp_path / "playbook.yml").write_text("---\n")
    monkeypatch.setattr(
        run_mod,
        "run_tart_macos",
        lambda argv, **k: fake_completed(returncode=0, stdout=json.dumps({"name": "gg-sbx-1", "ip": "10.0.0.9"})),
    )
    monkeypatch.setattr(run_mod, "run_playbook", lambda path, inv: 1)

    args = run_mod.parse_args(["up", "--repo", str(tmp_path)])
    rc = run_mod.cmd_up(args)
    assert rc == run_mod.tart_macos.EXIT_FAIL


def test_cmd_down_uses_state_file(monkeypatch, tmp_path):
    run_mod.write_state(tmp_path, "gg-sbx-1", "10.0.0.9")
    calls = []
    monkeypatch.setattr(run_mod, "run_tart_macos", lambda argv, **k: calls.append(argv) or fake_completed(returncode=0))

    args = run_mod.parse_args(["down", "--repo", str(tmp_path)])
    rc = run_mod.cmd_down(args)

    assert rc == run_mod.tart_macos.EXIT_OK
    assert calls == [["down", "gg-sbx-1"]]
    assert not run_mod.state_path(tmp_path).exists()


def test_cmd_down_without_state_or_name_fails(tmp_path):
    args = run_mod.parse_args(["down", "--repo", str(tmp_path)])
    rc = run_mod.cmd_down(args)
    assert rc == run_mod.tart_macos.EXIT_FAIL


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
