# macos-sandbox

Spin up the smallest usable macOS VM with
[Tart](https://github.com/openai/tart) (Sequoia by default -- runs on any
Apple-silicon host; Golden Gate/macOS 27 available for Tahoe+ hosts), and
wire [osascript-mcp](https://github.com/pythoninthegrass/osascript-mcp)'s
desktop automation into it over SSH -- so keyboard/mouse/screenshot
automation runs sandboxed, not on the host desktop. See
[SKILL.md](SKILL.md) for the full behavior.

## Host requirement (only if you opt into a newer guest image)

The default `TART_MACOS_IMAGE`
(`ghcr.io/cirruslabs/macos-sequoia-vanilla`) has no host requirement beyond
Apple silicon. If you set `TART_MACOS_IMAGE` to a newer guest image such as
`macos-golden-gate-vanilla:27.0` (macOS 27), the host itself must already
be on macOS 26 (Tahoe) or later -- confirmed by real testing, not just
reading the docs: that image's disk uses Apple's ASIF format, which Apple's
Virtualization framework only supports on a Tahoe+ **host** (openai/tart
maintainers, [#1096](https://github.com/openai/tart/issues/1096)) -- an
older host's `tart run` fails immediately with `Disk format 'asif' is not
supported on this system`, and `golden`/`up` would otherwise hang for
`TART_MACOS_BOOT_TIMEOUT` waiting for an IP a VM that never started can
never report. `doctor` recognizes `golden-gate` in `TART_MACOS_IMAGE` and
checks this before it bites; it stays silent for the default Sequoia image
even on an old host. See "Known break" below for a *separate*,
image-independent tart-version issue on the same older hosts.

## Quickstart

```bash
./scripts/tart_macos.py doctor    # check tart/sshpass/disk once
./scripts/tart_macos.py golden    # one-time: build the golden VM
./scripts/tart_macos.py up        # per session: clone + boot a sandbox
./scripts/tart_macos.py mcp <name>  # prints the `claude mcp add` command
# ... drive automation through the osascript-vm MCP server ...
./scripts/tart_macos.py down <name> # tear the sandbox down
```

## Parameters

- **`up [name]`** -- clones `gg-golden` into `name` (default
  `gg-sbx-<timestamp>`), boots it headless, mounts a repo directory (default
  cwd) at the guest's shared `repo` folder, prints `{"name", "ip", "repo",
  "repo_guest_path"}` once SSH is reachable. `--no-repo` skips the mount,
  `--repo PATH` overrides it, `--repo-ro` mounts read-only. `--softnet` boots
  with Tart's Softnet network isolation.
- **`mcp [name]`** -- prints (doesn't run) the `claude mcp add` command that
  wires `osascript-vm` to that clone, with `-A` agent forwarding so git
  push/pull against the mounted repo works from inside the guest.
- **`down [name]`** -- stops and deletes a clone. Refuses the golden VM's
  name unless `--golden` is passed.
- **`golden`** -- one-time bootstrap; idempotent. `--force` rebuilds it,
  `--grant` boots with a display for a manual TCC-permission fallback.
- **`status`** -- lists `gg-*` VMs and their state.
- **`doctor`** -- checks `tart`, `sshpass`, Apple silicon, free disk, and
  (only if `TART_MACOS_IMAGE` names a Tahoe+-only image) the host's own
  macOS version, see above.

## Configuration

`scripts/tart_macos.py`'s defaults below can be overridden per machine
without editing the script: set the env var directly, or copy
`.env.example` to `.env` in this same directory and edit values there (the
file is resolved relative to the script, not the caller's cwd, and is
gitignored). A CLI flag always wins over either.

| Env var | Default |
| --- | --- |
| `TART_MACOS_IMAGE` | `ghcr.io/cirruslabs/macos-sequoia-vanilla:15.7.7` |
| `TART_MACOS_CPU` | `2` |
| `TART_MACOS_MEMORY_MB` | `4096` |
| `TART_MACOS_DISPLAY` | `1280x800` |
| `TART_MACOS_GOLDEN` | `gg-golden` |
| `TART_MACOS_SOFTNET` | `false` |
| `TART_MACOS_SSH_USER` | `admin` |
| `TART_MACOS_SSH_PASSWORD` | `admin` |
| `TART_MACOS_MIN_FREE_GB` | `60` |
| `TART_MACOS_BOOT_TIMEOUT` | `180` |
| `TART_MACOS_OSASCRIPT_MCP_REF` | `git+https://github.com/pythoninthegrass/osascript-mcp` |
| `TART_MACOS_MCP_SERVER_NAME` | `osascript-vm` |
| `TART_MACOS_GITHUB_KEYS_USER` | `pythoninthegrass` |

## Example

```text
Run the login flow in a sandboxed macOS VM, not on my real desktop.
```

The agent runs `doctor` → `golden` (skipped if already built) → `up` →
`mcp` → registers `osascript-vm` → drives the flow through it → `down`.

## Known break: tart 2.35.0+ on pre-Tahoe hosts

Separate from the ASIF image-specific requirement above and independent of
which `TART_MACOS_IMAGE` is configured: `brew install openai/tools/tart`
currently installs 2.37.0, which crashes on launch (`dyld: ...
libswiftCompatibilitySpan.dylib`) on macOS Sequoia and older -- it's built
against the macOS 26/Xcode 27 Swift toolchain
([openai/tart#1302](https://github.com/openai/tart/issues/1302), open).
`doctor` detects this by name and points here. Pin 2.34.0 instead, since
Homebrew now requires formulae to live in a tap (a loose `.rb` file won't
install):

Extract to a permanent location, not `$PWD` -- a repo/scratch dir can get
cleaned up later and take `tart.app` with it, leaving the symlink dangling:

```bash
brew uninstall tart 2>/dev/null
mkdir -p ~/.local/opt ~/.local/bin
curl -L -o ~/.local/opt/tart.tar.gz https://github.com/openai/tart/releases/download/2.34.0/tart.tar.gz
tar -xzvf ~/.local/opt/tart.tar.gz -C ~/.local/opt
rm ~/.local/opt/tart.tar.gz
ln -sf ~/.local/opt/tart.app/Contents/MacOS/tart ~/.local/bin/tart
```

## Host DHCP lease (one-time, per host)

The built-in macOS DHCP server hands out 86,400s leases by default, which
exhausts the address pool if you clone and boot many ephemeral VMs in one
day. `up --softnet` works around this automatically; otherwise, per
[Tart's install notes](https://github.com/openai/tart/blob/main/docs/faq.md#changing-the-default-dhcp-lease-time),
shrink it once (persists across reboots):

```bash
sudo defaults write /Library/Preferences/SystemConfiguration/com.apple.InternetSharing.default.plist bootpd -dict DHCPLeaseTimeSecs -int 600
```

## Repeatable per-repo provisioning: `run.py` + `playbook.yml`

For a repo that needs the same guest-side setup every time, copy
`playbook.example.yml` to `playbook.yml` in that repo and drive the whole
lifecycle with one command:

```bash
cd /path/to/repo
/path/to/skill/scripts/run.py up     # up + dynamic inventory + ansible-playbook + mcp
/path/to/skill/scripts/run.py down   # no name needed -- reads .macos-sandbox-state.json
```

`playbook.yml` is a real Ansible playbook (not a custom DSL), run in-process
via `ansible.cli.playbook.PlaybookCLI` against a dynamic inventory built
from the VM's own IP -- the same shape and in-process-CLI pattern as
`~/git/nw_infra/networking/dhcp/run.py`. No `ansible_ssh_pass` or
`ansible_ssh_private_key_file` is needed: see "SSH auth" below. `run.py up`
skips provisioning (warns, doesn't fail) if the repo has no `playbook.yml`.

## SSH auth: no private key ever reaches the guest

- **Inbound (host → guest):** `golden` fetches
  `https://github.com/<TART_MACOS_GITHUB_KEYS_USER>.keys` and appends it to
  the guest's `~/.ssh/authorized_keys`, and seeds `known_hosts` for
  `github.com`. This is baked into `gg-golden` once, so every clone inherits
  it. Only public data ever leaves the host.
- **Outbound (guest → GitHub, for git push/pull against the mounted repo):**
  `mcp`'s registration command passes `-A` (agent forwarding), so git
  commands run inside the guest authenticate through the host's already
  unlocked ssh-agent. The guest never holds a private key of its own.

## Teardown

A clone left running still counts against Apple's 2-concurrent-macOS-VM
limit and holds disk. Always `down` a sandbox when the automation task is
done; `status` shows anything left over from an interrupted session.

## Concurrency safety

`golden`/`up`/`down` each take a non-blocking per-VM-name `flock` under
`/tmp/tart-macos-sandbox-<name>.lock` before touching a VM, and fail fast
with `'<name>' is locked...` rather than interleaving `tart` calls against
the same VM from two invocations. Discovered for real: a disowned
background `golden` outlived its wrapper, and a second `golden` got started
before that was noticed, racing both against `gg-golden`.

## Sizing note

Sizing (2 vCPU / 4 GB / 1280x800) follows the testing in ["How fast is a
macOS VM, and how small could it
be?"](https://eclecticlight.co/2026/05/02/how-fast-is-a-macos-vm-and-how-small-could-it-be/),
which found 2 vCPU / 4 GB comfortably usable for everyday GUI tasks on
Apple silicon, using well under the memory ceiling. Disk can't shrink below
the base image's size, but APFS clones are sparse and copy-on-write, so
`up`'s per-session clone is fast and cheap regardless of the golden VM's
~50 GB footprint (the default Sequoia image; Golden Gate is smaller, ~36
GB, if the host is on Tahoe+ and that image is configured instead).

## Permissions

`golden` grants Accessibility, Screen Capture, Post Event, and Apple Events
(System Events, Safari, and Terminal) to SSH-driven `osascript` via a direct
TCC database write -- no SIP disable needed. Confirmed live: `tell
application "Terminal" to do script "..."` works from a fresh, unpatched
`up` clone, and so does `screencapture`.

**Accessibility specifically doesn't reliably take effect from the database
write alone, even though every other grant above does.** Confirmed live:
`perform action "AXRaise"` and `keystroke` via `tell application "System
Events"` failed with `-1719`/`1002` on a fresh clone, with
`kTCCServiceAccessibility` rows already present and `auth_value=2` (allowed)
for both `/usr/bin/osascript` and `/usr/libexec/sshd-keygen-wrapper` --
`sshd-keygen-wrapper` is the actual client macOS attributes an SSH-invoked
request to, not `osascript` itself. Manually toggling `sshd-keygen-wrapper`
on in System Settings → Privacy & Security → Accessibility fixed both
immediately, with **no observable change to the TCC.db row** (same
`auth_value`, same `last_modified` timestamp before and after) -- so this
is a real live/cached-trust gap in the database-seeding approach for this
one TCC category specifically, not a wrong grant.

A PPPC configuration profile (`com.apple.TCC.configuration-profile-policy`)
is the Apple-sanctioned way to pre-approve TCC grants programmatically, and
was tried here: `profiles install -type configuration -path ...` on this
macOS version refuses outright (`profiles tool no longer supports
installs. Use System Settings Profiles to add configuration profiles.`) --
Apple removed CLI profile installation; it now requires either genuine MDM
enrollment (a real MDM server, out of scope for a standalone sandbox VM) or
the same manual System Settings step. **If a task needs
`keystroke`/`AXRaise`/other UI-scripting-via-System-Events calls (not just
launching apps or `do script`), boot with `golden --grant` (or `up --gui`)
once and manually enable Accessibility for `sshd-keygen-wrapper` in System
Settings** -- the same VM/clone then works from SSH afterward. This
doesn't block launching apps or driving them via `do script`/direct
AppleEvents, which is this skill's core path and works from a completely
unpatched boot.
