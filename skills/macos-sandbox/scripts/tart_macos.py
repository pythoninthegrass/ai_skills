#!/usr/bin/env -S uv run --script

# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = [
#     "python-decouple>=3.8",
# ]
# [tool.uv]
# exclude-newer = "2026-10-01T00:00:00Z"
# ///

# pyright: reportMissingImports=false

"""
Usage:
    tart_macos.py doctor
    tart_macos.py golden [--force] [--grant]
    tart_macos.py up [NAME] [--softnet] [--repo PATH] [--no-repo] [--repo-ro]
    tart_macos.py mcp [NAME] [--server-name NAME]
    tart_macos.py status
    tart_macos.py down [NAME] [--golden]

Commands:
    doctor   Check the host for tart, sshpass, Apple silicon, and free disk.
    golden   One-time bootstrap of the golden VM (pull, size, TCC grants, uv,
             authorize the host's GitHub public keys for inbound SSH).
    up       Clone an ephemeral sandbox VM from golden and boot it headless,
             mounting a repo directory (default: cwd) at the guest's shared
             'repo' folder.
    mcp      Print the `claude mcp add` command to wire osascript-mcp to a
             VM, with SSH agent forwarding (-A) so git push/pull against the
             mounted repo authenticates using the host's agent -- no
             private key is ever copied into the guest.
    status   List gg-* VMs, their state, and their IP.
    down     Stop and delete an ephemeral VM. Refuses the golden VM without
             --golden.

Note:
    Defaults for every tunable below are resolved through python-decouple:
    CLI flag > process env > skills/macos-sandbox/.env > hardcoded
    default. See .env.example for the full list of TART_MACOS_* names.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from decouple import Config, RepositoryEmpty, RepositoryEnv
from pathlib import Path

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = SCRIPT_DIR.parent / ".env"  # skills/macos-sandbox/.env, not cwd-relative

SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]

# macOS guests auto-mount every `--dir` share under this path, one subdirectory
# per mount tag -- see https://github.com/openai/tart/blob/main/docs/quick-start.md
GUEST_SHARED_ROOT = "/Volumes/My Shared Files"


def load_config(env_file):
    """Build a decouple Config that reads process env, then env_file if it
    exists. Deliberately not decouple's AutoConfig singleton -- that searches
    upward from cwd for a .env, which would pick up an unrelated one from
    whatever project this script happens to be running in."""
    if env_file is not None and Path(env_file).exists():
        return Config(RepositoryEnv(str(env_file)))
    return Config(RepositoryEmpty())


config = load_config(ENV_FILE)

IMAGE_DEFAULT = config("TART_MACOS_IMAGE", default="ghcr.io/cirruslabs/macos-golden-gate-vanilla:27.0")
CPU_DEFAULT = config("TART_MACOS_CPU", default=2, cast=int)
MEMORY_MB_DEFAULT = config("TART_MACOS_MEMORY_MB", default=4096, cast=int)
DISPLAY_DEFAULT = config("TART_MACOS_DISPLAY", default="1280x800")
GOLDEN_DEFAULT = config("TART_MACOS_GOLDEN", default="gg-golden")
SOFTNET_DEFAULT = config("TART_MACOS_SOFTNET", default=False, cast=bool)
SSH_USER_DEFAULT = config("TART_MACOS_SSH_USER", default="admin")
SSH_PASSWORD_DEFAULT = config("TART_MACOS_SSH_PASSWORD", default="admin")
MIN_FREE_GB_DEFAULT = config("TART_MACOS_MIN_FREE_GB", default=60, cast=int)
BOOT_TIMEOUT_DEFAULT = config("TART_MACOS_BOOT_TIMEOUT", default=180, cast=int)
OSASCRIPT_MCP_REF_DEFAULT = config(
    "TART_MACOS_OSASCRIPT_MCP_REF", default="git+https://github.com/pythoninthegrass/osascript-mcp"
)
MCP_SERVER_NAME_DEFAULT = config("TART_MACOS_MCP_SERVER_NAME", default="osascript-vm")
GITHUB_KEYS_USER_DEFAULT = config("TART_MACOS_GITHUB_KEYS_USER", default="pythoninthegrass")


def run(argv, **kwargs):
    """Single choke point for every subprocess invocation -- tests monkeypatch
    this function rather than subprocess.run directly, so argv-building logic
    stays testable without ever touching a real `tart`/`ssh` binary."""
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    return subprocess.run(argv, **kwargs)


TART_DYLD_BROKEN_HINT = (
    "tart is on PATH but won't run (dyld: libswiftCompatibilitySpan.dylib) -- openai/tools/tart "
    "2.35.0+ is built against the macOS 26/Xcode 27 Swift toolchain and crashes on Sequoia and "
    "older (https://github.com/openai/tart/issues/1302). Use 2.34.0 instead: "
    "curl -LO https://github.com/openai/tart/releases/download/2.34.0/tart.tar.gz && "
    "tar -xzvf tart.tar.gz && ln -sf \"$PWD/tart.app/Contents/MacOS/tart\" ~/.local/bin/tart"
)


def check_tart_runs():
    """`tart` being on PATH doesn't mean it runs -- 2.35.0+ links against a Swift
    6.2 dylib that's absent pre-Tahoe and crashes on invocation, including
    `--version`. Distinguish that known break from any other failure."""
    tart_path = shutil.which("tart")
    if not tart_path:
        return "tart is not on PATH -- install: brew install openai/tools/tart"
    result = run(["tart", "--version"])
    if result.returncode == 0:
        return None
    stderr = result.stderr or ""
    if "libswiftCompatibilitySpan" in stderr:
        return TART_DYLD_BROKEN_HINT
    return f"tart is on PATH but `tart --version` failed: {stderr.strip() or 'no output'}"


def doctor():
    problems = []
    tart_problem = check_tart_runs()
    if tart_problem:
        problems.append(tart_problem)
    if not shutil.which("sshpass"):
        problems.append("sshpass is not on PATH -- install: brew install cirruslabs/cli/sshpass")
    if not shutil.which("ssh"):
        problems.append("ssh is not on PATH")

    import platform

    if platform.machine() != "arm64":
        problems.append(f"host is {platform.machine()}, not arm64 -- tart requires Apple silicon")

    try:
        free_gb = shutil.disk_usage(Path.home()).free / 1e9
        if free_gb < MIN_FREE_GB_DEFAULT:
            problems.append(f"only {free_gb:.0f} GB free, want at least {MIN_FREE_GB_DEFAULT} GB for a macOS VM")
    except OSError as exc:
        problems.append(f"could not check free disk space: {exc}")

    return problems


DHCP_LEASE_PLIST = "/Library/Preferences/SystemConfiguration/com.apple.InternetSharing.default.plist"
DHCP_LEASE_MAX_SECS = 3600  # tart's own recommendation shrinks this to 600
DHCP_LEASE_WARNING = (
    "the built-in DHCP server's lease time isn't shortened (default 86,400s) -- cloning and booting "
    "many ephemeral VMs in one day can exhaust the address pool. Fix once: "
    f"sudo defaults write {DHCP_LEASE_PLIST} bootpd -dict DHCPLeaseTimeSecs -int 600 "
    "(or always pass `up --softnet`, which works around it automatically)."
)


def dhcp_lease_warning():
    """Not a blocker -- `up --softnet` sidesteps this entirely, and a single
    VM at a time never hits the default 86,400s lease's exhaustion point. Only
    matters once a host clones/boots VMs repeatedly across a day."""
    import re

    result = run(["defaults", "read", DHCP_LEASE_PLIST, "bootpd"])
    if result.returncode != 0:
        return DHCP_LEASE_WARNING
    match = re.search(r"DHCPLeaseTimeSecs\s*=\s*(\d+)", result.stdout)
    if not match or int(match.group(1)) > DHCP_LEASE_MAX_SECS:
        return DHCP_LEASE_WARNING
    return None


def build_clone_argv(image, dest):
    return ["tart", "clone", image, dest]


def build_set_argv(name, cpu, memory_mb, display):
    return ["tart", "set", name, "--cpu", str(cpu), "--memory", str(memory_mb), "--display", display]


def build_dir_spec(tag, host_path, ro=False):
    spec = f"{tag}:{host_path}"
    if ro:
        spec += ":ro"
    return spec


def build_run_argv(name, softnet=False, dirs=None):
    argv = ["tart", "run", name, "--no-graphics", "--no-audio", "--no-clipboard"]
    for spec in dirs or []:
        argv += ["--dir", spec]
    if softnet:
        argv.append("--net-softnet")
    return argv


def build_stop_argv(name):
    return ["tart", "stop", name]


def build_delete_argv(name):
    return ["tart", "delete", name]


def build_ip_argv(name):
    return ["tart", "ip", name]


def build_list_argv():
    return ["tart", "list"]


def build_ssh_argv(ip, user, password, remote_cmd, forward_agent=False):
    opts = [*SSH_OPTS, "-A"] if forward_agent else SSH_OPTS
    return ["sshpass", "-p", password, "ssh", *opts, f"{user}@{ip}", remote_cmd]


def build_scp_argv(local_path, ip, user, password, remote_path):
    return ["sshpass", "-p", password, "scp", *SSH_OPTS, str(local_path), f"{user}@{ip}:{remote_path}"]


def build_github_keys_setup_cmd(github_user):
    """No private key ever touches the guest. This authorizes the HOST's real
    SSH key for inbound login (replacing the fixed admin/admin password) by
    trusting whatever's already public on GITHUB_USER's account, and seeds
    known_hosts so a git clone/push to github.com doesn't hang on the first
    host-key prompt. Outbound git auth (actually pushing/pulling as that
    user) comes from `-A` agent forwarding on the mcp connection, below --
    the guest borrows the host's already-unlocked agent, it never gets a key
    of its own."""
    return (
        f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        f"curl -fsSL https://github.com/{github_user}.keys >> ~/.ssh/authorized_keys && "
        f"chmod 600 ~/.ssh/authorized_keys && "
        f"ssh-keyscan github.com >> ~/.ssh/known_hosts 2>/dev/null; true"
    )


def build_mcp_command(server_name, ip, user, password, osascript_mcp_ref):
    remote_cmd = f"~/.local/bin/uvx --from {osascript_mcp_ref} osascript-mcp"
    ssh_argv = build_ssh_argv(ip, user, password, remote_cmd, forward_agent=True)
    return ["claude", "mcp", "add", server_name, "--", *ssh_argv]


def clone_vm(src, dest):
    return run(build_clone_argv(src, dest))


def set_vm(name, cpu, memory_mb, display):
    return run(build_set_argv(name, cpu, memory_mb, display))


def run_vm(name, softnet=False, dirs=None):
    """Boot NAME headless and detached -- returns immediately, the VM keeps
    running as a background process outside this script's lifetime."""
    return subprocess.Popen(
        build_run_argv(name, softnet=softnet, dirs=dirs),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def stop_vm(name):
    return run(build_stop_argv(name))


def delete_vm(name):
    return run(build_delete_argv(name))


def get_ip(name):
    result = run(build_ip_argv(name))
    if result.returncode != 0:
        return None
    ip = result.stdout.strip()
    return ip or None


def wait_for_ip(name, timeout_s):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ip = get_ip(name)
        if ip:
            return ip
        time.sleep(2)
    return None


def ssh_run(ip, user, password, remote_cmd, timeout_s=30):
    return run(build_ssh_argv(ip, user, password, remote_cmd), timeout=timeout_s)


def wait_for_ssh(ip, user, password, timeout_s):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            result = ssh_run(ip, user, password, "true", timeout_s=10)
        except subprocess.TimeoutExpired:
            result = None
        if result is not None and result.returncode == 0:
            return True
        time.sleep(2)
    return False


def list_vms():
    result = run(build_list_argv())
    return result.stdout


def vm_exists(name):
    """Existence, not reachability -- a stopped VM still exists. Checking
    `get_ip` instead (as `golden` used to) is wrong: a normal `golden` run
    stops the VM at the end, so the very next run would see no IP, conclude
    it doesn't exist, and re-clone into a name that's already taken."""
    return any(name in line.split() for line in list_vms().splitlines())


def down(name, golden_name, allow_golden=False):
    if name == golden_name and not allow_golden:
        return EXIT_USAGE, f"refusing to delete '{name}' -- it's the golden VM. Pass --golden to confirm."
    stop_result = stop_vm(name)
    if stop_result.returncode != 0 and "not running" not in (stop_result.stderr or "").lower():
        return EXIT_FAIL, f"tart stop {name} failed: {stop_result.stderr.strip()}"
    delete_result = delete_vm(name)
    if delete_result.returncode != 0:
        return EXIT_FAIL, f"tart delete {name} failed: {delete_result.stderr.strip()}"
    return EXIT_OK, f"deleted {name}"


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="tart_macos.py", description="Bootstrap and drive a sandboxed macOS VM.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check host prerequisites")

    p_golden = sub.add_parser("golden", help="bootstrap the golden VM")
    p_golden.add_argument("--force", action="store_true", help="re-clone even if the golden VM already exists")
    p_golden.add_argument("--grant", action="store_true", help="boot with a display so TCC grants can be made by hand")

    p_up = sub.add_parser("up", help="clone and boot an ephemeral sandbox VM")
    p_up.add_argument("name", nargs="?", default=None)
    p_up.add_argument("--softnet", action="store_true", default=SOFTNET_DEFAULT)
    p_up.add_argument("--repo", default=None, help="host directory to mount at the guest's shared 'repo' folder (default: cwd)")
    p_up.add_argument("--no-repo", action="store_true", help="don't mount a repo directory")
    p_up.add_argument("--repo-ro", action="store_true", help="mount the repo read-only")

    p_mcp = sub.add_parser("mcp", help="print the claude mcp add command for a VM")
    p_mcp.add_argument("name", nargs="?", default=None)
    p_mcp.add_argument("--server-name", default=MCP_SERVER_NAME_DEFAULT)

    sub.add_parser("status", help="list gg-* VMs")

    p_down = sub.add_parser("down", help="stop and delete an ephemeral VM")
    p_down.add_argument("name", nargs="?", default=None)
    p_down.add_argument("--golden", action="store_true", help="confirm deleting the golden VM")

    return parser.parse_args(argv)


def cmd_doctor():
    problems = doctor()
    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)

    warning = dhcp_lease_warning()
    if warning:
        print(f"WARN: {warning}", file=sys.stderr)

    if problems:
        return EXIT_FAIL
    print("OK: tart, sshpass, and free disk all look fine")
    return EXIT_OK


def cmd_golden(args):
    name = GOLDEN_DEFAULT
    if vm_exists(name):
        if not args.force:
            print(f"OK: {name} already exists")
            return EXIT_OK
        stop_vm(name)
        delete_result = delete_vm(name)
        if delete_result.returncode != 0:
            print(f"FAIL: could not remove existing {name} for --force: {delete_result.stderr.strip()}", file=sys.stderr)
            return EXIT_FAIL

    clone_result = clone_vm(IMAGE_DEFAULT, name)
    if clone_result.returncode != 0:
        print(f"FAIL: tart clone failed: {clone_result.stderr.strip()}", file=sys.stderr)
        return EXIT_FAIL

    set_result = set_vm(name, CPU_DEFAULT, MEMORY_MB_DEFAULT, DISPLAY_DEFAULT)
    if set_result.returncode != 0:
        print(f"FAIL: tart set failed: {set_result.stderr.strip()}", file=sys.stderr)
        return EXIT_FAIL

    proc = run_vm(name, softnet=False)
    ip = wait_for_ip(name, BOOT_TIMEOUT_DEFAULT)
    if ip is None:
        print(f"FAIL: {name} never got an IP within {BOOT_TIMEOUT_DEFAULT}s", file=sys.stderr)
        return EXIT_FAIL
    if not wait_for_ssh(ip, SSH_USER_DEFAULT, SSH_PASSWORD_DEFAULT, BOOT_TIMEOUT_DEFAULT):
        print(f"FAIL: SSH to {name} ({ip}) never came up", file=sys.stderr)
        return EXIT_FAIL

    grant_script = SCRIPT_DIR / "grant-tcc.sh"
    if grant_script.exists():
        scp_result = run(build_scp_argv(grant_script, ip, SSH_USER_DEFAULT, SSH_PASSWORD_DEFAULT, "/tmp/grant-tcc.sh"))
        if scp_result.returncode == 0:
            ssh_run(ip, SSH_USER_DEFAULT, SSH_PASSWORD_DEFAULT, "chmod +x /tmp/grant-tcc.sh && /tmp/grant-tcc.sh")

    ssh_run(
        ip,
        SSH_USER_DEFAULT,
        SSH_PASSWORD_DEFAULT,
        "curl -LsSf https://astral.sh/uv/install.sh | sh",
        timeout_s=120,
    )

    github_keys_result = ssh_run(
        ip, SSH_USER_DEFAULT, SSH_PASSWORD_DEFAULT, build_github_keys_setup_cmd(GITHUB_KEYS_USER_DEFAULT)
    )
    if github_keys_result.returncode != 0:
        print(
            f"WARN: could not fetch https://github.com/{GITHUB_KEYS_USER_DEFAULT}.keys into the guest "
            f"(host offline, or curl unreachable from the VM?): {github_keys_result.stderr.strip()}",
            file=sys.stderr,
        )

    stop_vm(name)
    print(f"OK: {name} bootstrapped ({ip})")
    if args.grant:
        print(f"Re-run with --grant to boot {name} with a display and grant permissions by hand.", file=sys.stderr)
    return EXIT_OK


def cmd_up(args):
    name = args.name or f"gg-sbx-{int(time.time())}"
    clone_result = clone_vm(GOLDEN_DEFAULT, name)
    if clone_result.returncode != 0:
        print(f"FAIL: tart clone failed: {clone_result.stderr.strip()}", file=sys.stderr)
        return EXIT_FAIL

    dirs = []
    repo_path = None
    if not args.no_repo:
        repo_path = str(Path(args.repo).expanduser().resolve()) if args.repo else os.getcwd()
        dirs.append(build_dir_spec("repo", repo_path, ro=args.repo_ro))

    run_vm(name, softnet=args.softnet, dirs=dirs)
    ip = wait_for_ip(name, BOOT_TIMEOUT_DEFAULT)
    if ip is None:
        print(f"FAIL: {name} never got an IP within {BOOT_TIMEOUT_DEFAULT}s", file=sys.stderr)
        return EXIT_FAIL
    if not wait_for_ssh(ip, SSH_USER_DEFAULT, SSH_PASSWORD_DEFAULT, BOOT_TIMEOUT_DEFAULT):
        print(f"FAIL: SSH to {name} ({ip}) never came up", file=sys.stderr)
        return EXIT_FAIL

    result = {"name": name, "ip": ip, "repo": repo_path}
    if repo_path is not None:
        result["repo_guest_path"] = f"{GUEST_SHARED_ROOT}/repo"
    print(json.dumps(result))
    return EXIT_OK


def cmd_mcp(args):
    name = args.name
    if name is None:
        print("FAIL: mcp requires a VM name (from `up`'s output)", file=sys.stderr)
        return EXIT_USAGE
    ip = get_ip(name)
    if ip is None:
        print(f"FAIL: could not resolve an IP for {name} -- is it running?", file=sys.stderr)
        return EXIT_FAIL
    cmd = build_mcp_command(args.server_name, ip, SSH_USER_DEFAULT, SSH_PASSWORD_DEFAULT, OSASCRIPT_MCP_REF_DEFAULT)
    print(" ".join(cmd))
    return EXIT_OK


def cmd_status():
    print(list_vms())
    return EXIT_OK


def cmd_down(args):
    name = args.name or GOLDEN_DEFAULT
    rc, message = down(name, GOLDEN_DEFAULT, allow_golden=args.golden)
    print(message, file=sys.stderr if rc != EXIT_OK else sys.stdout)
    return rc


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)

    if args.command == "doctor":
        return cmd_doctor()
    if args.command == "golden":
        return cmd_golden(args)
    if args.command == "up":
        return cmd_up(args)
    if args.command == "mcp":
        return cmd_mcp(args)
    if args.command == "status":
        return cmd_status()
    if args.command == "down":
        return cmd_down(args)

    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
