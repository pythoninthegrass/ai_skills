---
name: macos-sandbox
description: >
  Spin up the smallest usable macOS 27 (Golden Gate) VM with Tart, and wire
  osascript-mcp's desktop automation (keyboard, windows, menus, screenshots,
  Shortcuts) into it over SSH -- so automation runs in a sandboxed VM instead
  of taking over the host desktop. Use when the user wants desktop
  automation isolated from general computer use, asks to "sandbox
  osascript", "spin up a macOS VM for automation", "run osascript-mcp in a
  VM", or mentions Tart/tart-cli alongside desktop automation.
argument-hint: "[up|down|status|golden|doctor] [vm-name]"
---

# macos-sandbox

## Resolve `SKILL_DIR` (do this before running the bundled script)

`scripts/tart_macos.py` is a direct sibling of this file in every install
layout. Set `SKILL_DIR` to the absolute path of the directory containing
THIS SKILL.md you just Read, e.g.:

```text
Read ~/.claude/skills/macos-sandbox/SKILL.md → SKILL_DIR=~/.claude/skills/macos-sandbox
```

## Why this exists

`osascript-mcp` (`~/git/osascript-mcp`) runs `/usr/bin/osascript` as a local
subprocess -- it always drives whatever Mac it's running on, with no
remote/sandbox mode of its own. Running it directly on the host means every
automated click, keystroke, or window move happens on Lance's real desktop,
interrupting whatever else is going on there.

This skill gives osascript-mcp a Mac of its own: a small macOS 27 ("Golden
Gate") VM under Tart, reached over SSH. A second MCP server
(`osascript-vm`, registered per session) proxies into the VM, so the host's
own `osascript-mcp` registration is untouched and automation stays
contained. Two servers, two Macs -- pick `osascript-vm` for anything
disruptive (typing, clicking, screenshots of a full desktop), and the host
one only when the task genuinely needs the real machine.

## One-time setup: build the golden VM

```bash
"${SKILL_DIR}/scripts/tart_macos.py" doctor
```

Checks for `tart` (`brew install openai/tools/tart`), `sshpass`
(`brew install cirruslabs/cli/sshpass`), Apple silicon, and free disk. Fix
anything it flags before continuing -- it doesn't install for you. It also
warns (non-fatally) if the host's DHCP lease time isn't shortened yet -- see
the tweak just below; the warning doesn't block `golden`/`up`.

Also do this once per host, per Tart's own install notes -- the built-in
macOS DHCP server hands out 86,400s leases by default, which exhausts the
address pool if a host clones and boots many short-lived VMs in one day
(more than one every ~6 minutes). `--softnet` works around this
automatically; without it, shrink the lease time once:

```bash
sudo defaults write /Library/Preferences/SystemConfiguration/com.apple.InternetSharing.default.plist bootpd -dict DHCPLeaseTimeSecs -int 600
```

Persists across reboots. If a fresh VM still can't get an IP afterward, the
lease file may already be full of old 86,400s entries --
`sudo rm /var/db/dhcpd_leases` and it's recreated on the next `tart run`.

```bash
"${SKILL_DIR}/scripts/tart_macos.py" golden
```

Idempotent -- if `gg-golden` already exists this is a no-op (pass `--force`
to rebuild it). On first run it:

1. Clones `ghcr.io/cirruslabs/macos-golden-gate-vanilla:27.0` (the smallest
   Golden Gate image, no brew/tooling baked in -- ~31 GB download, ~36 GB on
   disk).
2. Sizes it to 2 vCPU / 4096 MB / 1280x800 -- per the Eclectic Light
   testing this article was seeded from, that's comfortably enough for
   everyday GUI automation on Apple silicon, using well under the memory
   ceiling.
3. Boots it headless, waits for an IP and SSH.
4. Runs `scripts/grant-tcc.sh` over SSH to grant Accessibility, Screen
   Capture, Post Event, and Apple Events (System Events + Safari)
   permissions to SSH-driven `osascript` -- **no SIP disable required**, the
   vanilla image ships with SIP on and this only needs `sudo sqlite3`
   access to the per-user TCC database.
5. Installs `uv` in the guest and stops the VM.

**If step 4 is refused** (a locked-down TCC database, or a macOS point
release that moved something): re-run with `--grant`, which boots the VM
with a display so the permissions can be granted once by hand in System
Settings → Privacy & Security. They persist in `gg-golden`, so every clone
inherits them -- this is a one-time fallback, not a per-session step.

Sending Apple Events to any app other than System Events or Safari still
prompts once the first time it happens. If a task needs another app,
trigger that prompt once inside `gg-golden` (via `--grant`) so clones
inherit the grant too.

## Per-session: clone, wire up, tear down

```bash
RES=$("${SKILL_DIR}/scripts/tart_macos.py" up)
NAME=$(echo "$RES" | jq -r .name)
IP=$(echo "$RES" | jq -r .ip)
```

Clones `gg-golden` into a fresh ephemeral VM (`gg-sbx-<timestamp>` unless
you pass a name), boots it headless (`--no-graphics --no-audio
--no-clipboard`), and waits for IP + SSH. APFS clones are sparse and
copy-on-write, so this is fast and cheap on disk despite the golden image's
size. Pass `--softnet` for stricter network isolation (Tart's Softnet
userspace filter) if the automation task shouldn't reach the LAN freely.

```bash
"${SKILL_DIR}/scripts/tart_macos.py" mcp "$NAME"
```

Prints (doesn't run) the registration command:

```bash
claude mcp add osascript-vm -- sshpass -p admin ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null admin@<ip> '~/.local/bin/uvx --from git+https://github.com/pythoninthegrass/osascript-mcp osascript-mcp'
```

Run it (or have the user approve running it) to register `osascript-vm` for
this session. From here, drive automation through `osascript-vm`'s tools,
not the host's `osascript` MCP server. Call `check_permissions` first to
confirm the TCC grants landed; if anything is missing, see the `--grant`
fallback above rather than trying to patch it live.

When the automation is done:

```bash
"${SKILL_DIR}/scripts/tart_macos.py" down "$NAME"
```

Stops and deletes the ephemeral clone. `down` without a name, or with the
golden VM's name, refuses unless `--golden` is passed -- this is
deliberate, don't work around it by naming the golden VM's own name as an
"ephemeral" clone.

```bash
"${SKILL_DIR}/scripts/tart_macos.py" status
```

Lists `gg-*` VMs and their state at any point -- useful before `up` if a
previous session's teardown didn't run.

## Concurrency limit

Apple's license permits at most 2 concurrent macOS VMs per host. Check
`status` before spinning up a second sandbox; don't fan out more than 2
`up` calls without stopping one first.

## Bundled scripts

`scripts/tart_macos.py` -- a self-contained `uv run --script` (PEP 723)
tool with `doctor`, `golden`, `up`, `mcp`, `status`, and `down`
subcommands, described above. Every tunable resolves through
`python-decouple`: CLI flag > process env > `skills/macos-sandbox/.env`
> hardcoded default. See `.env.example` for the full list of
`TART_MACOS_*` names. Run `scripts/tart_macos.py -h` for the flag list, and
review the script before first use.

`scripts/grant-tcc.sh` -- trimmed from cirruslabs/macos-image-templates'
`update-tcc-database.sh`, run inside the guest by `golden` to grant
Accessibility/Screen Capture/Post Event/Apple Events to SSH-driven
`osascript` without disabling SIP.

`scripts/test_tart_macos.py` -- the accompanying pytest suite, also a
self-contained `uv run --script`. Run it directly
(`./scripts/test_tart_macos.py`) after changing `tart_macos.py`.
