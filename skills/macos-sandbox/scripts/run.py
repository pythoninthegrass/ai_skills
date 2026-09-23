#!/usr/bin/env -S uv run --script

# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = [
#     "ansible-core>=2.16",
#     "pyyaml>=6.0",
#     "python-decouple>=3.8",
# ]
# [tool.uv]
# exclude-newer = "2026-10-01T00:00:00Z"
# ///

# pyright: reportMissingImports=false

"""
Usage:
    run.py up   [--playbook PATH] [--repo PATH] [--repo-ro] [--softnet] [NAME]
    run.py down [NAME]

Commands:
    up    tart_macos.py up (clone golden, boot, mount --repo), then run
          PLAYBOOK against the fresh VM via an in-process ansible-playbook,
          using a dynamic inventory built from the VM's own IP. Remembers
          the VM in a state file so `down` needs no arguments.
    down  Tear down the VM `up` created here (state-file lookup by default,
          or NAME to target a specific one) via tart_macos.py down.

Note:
    playbook.yml is a REAL Ansible playbook -- see playbook.example.yml,
    modeled on ~/git/nw_infra/networking/dhcp/run.py's in-process
    PlaybookCLI + dynamic-inventory pattern. Connection auth relies on
    `tart_macos.py golden` having already authorized the host's GitHub
    public key for inbound SSH (see build_github_keys_setup_cmd in
    tart_macos.py) -- no private key or password is threaded through here.
"""

import argparse
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILENAME = ".macos-sandbox-state.json"
DEFAULT_PLAYBOOK_NAME = "playbook.yml"

spec = importlib.util.spec_from_file_location("tart_macos", SCRIPT_DIR / "tart_macos.py")
tart_macos = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tart_macos)


def run_tart_macos(args, **kwargs):
    """Shell out to the sibling CLI rather than re-import its orchestration --
    `up`/`golden`/`down` already own the clone/boot/wait/teardown logic and
    their own tests; this script's job is only the ansible handoff on top."""
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    return subprocess.run([sys.executable, str(SCRIPT_DIR / "tart_macos.py"), *args], **kwargs)


def state_path(base_dir):
    return Path(base_dir) / STATE_FILENAME


def write_state(base_dir, name, ip):
    state_path(base_dir).write_text(json.dumps({"name": name, "ip": ip, "started_at": time.time()}))


def read_state(base_dir):
    p = state_path(base_dir)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def build_inventory(name, ip, user):
    """Dynamic inventory, same shape as nw_infra's pulumi/k3s/ansible/inventory.yml
    -- ansible_host under hosts, common connection vars under vars. No
    ansible_ssh_private_key_file/ansible_ssh_pass: the guest already trusts
    the host's default SSH identity via golden's GitHub-keys bootstrap."""
    return {
        "sandbox": {
            "hosts": {name: {"ansible_host": ip}},
            "vars": {
                "ansible_user": user,
                "ansible_ssh_common_args": "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null",
                "ansible_python_interpreter": "/usr/bin/python3",
            },
        }
    }


def run_playbook(playbook_path, inventory):
    """In-process ansible-playbook, matching
    ~/git/nw_infra/networking/dhcp/run.py's PlaybookCLI pattern -- avoids a
    second `uv run --script` cold start just to shell out to the same
    ansible-core this script already resolved."""
    import tempfile
    import yaml
    from ansible.cli.playbook import PlaybookCLI

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", prefix="macos_sandbox_inventory_", delete=False) as f:
        yaml.dump(inventory, f, default_flow_style=False)
        inventory_path = f.name

    try:
        cli = PlaybookCLI(["ansible-playbook", str(playbook_path), "-i", inventory_path])
        cli.parse()
        return cli.run()
    finally:
        Path(inventory_path).unlink(missing_ok=True)


def resolve_playbook(playbook_arg, repo_dir):
    if playbook_arg:
        return Path(playbook_arg).expanduser().resolve()
    candidate = Path(repo_dir) / DEFAULT_PLAYBOOK_NAME
    return candidate if candidate.exists() else None


def cmd_up(args):
    repo_dir = Path(args.repo).expanduser().resolve() if args.repo else Path.cwd()
    playbook_path = resolve_playbook(args.playbook, repo_dir)

    up_argv = ["up"]
    if args.name:
        up_argv.append(args.name)
    up_argv += ["--repo", str(repo_dir)]
    if args.repo_ro:
        up_argv.append("--repo-ro")
    if args.softnet:
        up_argv.append("--softnet")

    up_result = run_tart_macos(up_argv)
    if up_result.returncode != 0:
        print(up_result.stderr or up_result.stdout, file=sys.stderr, end="")
        return tart_macos.EXIT_FAIL
    up_info = json.loads(up_result.stdout.strip().splitlines()[-1])
    name, ip = up_info["name"], up_info["ip"]

    write_state(repo_dir, name, ip)

    if playbook_path is None:
        print(
            f"WARN: no playbook found (looked for {repo_dir / DEFAULT_PLAYBOOK_NAME}) -- skipping provisioning", file=sys.stderr
        )
    else:
        inventory = build_inventory(name, ip, tart_macos.SSH_USER_DEFAULT)
        rc = run_playbook(playbook_path, inventory)
        if rc != 0:
            print(f"FAIL: ansible-playbook exited {rc}", file=sys.stderr)
            return tart_macos.EXIT_FAIL

    mcp_result = run_tart_macos(["mcp", name])
    print(json.dumps(up_info))
    print(mcp_result.stdout.strip())
    return tart_macos.EXIT_OK


def cmd_down(args):
    repo_dir = Path(args.repo).expanduser().resolve() if args.repo else Path.cwd()
    name = args.name
    if name is None:
        state = read_state(repo_dir)
        if state is None:
            print(f"FAIL: no state file in {repo_dir} and no NAME given -- was `up` run here?", file=sys.stderr)
            return tart_macos.EXIT_FAIL
        name = state["name"]

    down_result = run_tart_macos(["down", name])
    print(down_result.stdout, end="")
    print(down_result.stderr, end="", file=sys.stderr)
    if down_result.returncode == tart_macos.EXIT_OK:
        state_path(repo_dir).unlink(missing_ok=True)
    return down_result.returncode


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="run.py", description="Ansible-provisioned wrapper around tart_macos.py.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_up = sub.add_parser("up")
    p_up.add_argument("name", nargs="?", default=None)
    p_up.add_argument("--playbook", default=None, help="path to an ansible playbook (default: <repo>/playbook.yml)")
    p_up.add_argument("--repo", default=None, help="host directory to mount + provision (default: cwd)")
    p_up.add_argument("--repo-ro", action="store_true")
    p_up.add_argument("--softnet", action="store_true")

    p_down = sub.add_parser("down")
    p_down.add_argument("name", nargs="?", default=None)
    p_down.add_argument("--repo", default=None, help="directory holding the state file (default: cwd)")

    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "up":
        return cmd_up(args)
    if args.command == "down":
        return cmd_down(args)
    return tart_macos.EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
