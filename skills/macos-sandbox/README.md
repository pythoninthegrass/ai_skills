# macos-sandbox

Spin up the smallest usable macOS 27 (Golden Gate) VM with
[Tart](https://github.com/openai/tart), and wire
[osascript-mcp](https://github.com/pythoninthegrass/osascript-mcp)'s
desktop automation into it over SSH -- so keyboard/mouse/screenshot
automation runs sandboxed, not on the host desktop. See
[SKILL.md](SKILL.md) for the full behavior.

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
  `gg-sbx-<timestamp>`), boots it headless, prints `{"name", "ip"}` once
  SSH is reachable. `--softnet` boots with Tart's Softnet network isolation.
- **`mcp [name]`** -- prints (doesn't run) the `claude mcp add` command that
  wires `osascript-vm` to that clone.
- **`down [name]`** -- stops and deletes a clone. Refuses the golden VM's
  name unless `--golden` is passed.
- **`golden`** -- one-time bootstrap; idempotent. `--force` rebuilds it,
  `--grant` boots with a display for a manual TCC-permission fallback.
- **`status`** -- lists `gg-*` VMs and their state.
- **`doctor`** -- checks `tart`, `sshpass`, Apple silicon, and free disk.

## Configuration

`scripts/tart_macos.py`'s defaults below can be overridden per machine
without editing the script: set the env var directly, or copy
`.env.example` to `.env` in this same directory and edit values there (the
file is resolved relative to the script, not the caller's cwd, and is
gitignored). A CLI flag always wins over either.

| Env var | Default |
| --- | --- |
| `TART_MACOS_IMAGE` | `ghcr.io/cirruslabs/macos-golden-gate-vanilla:27.0` |
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

## Example

```text
Run the login flow in a sandboxed macOS VM, not on my real desktop.
```

The agent runs `doctor` → `golden` (skipped if already built) → `up` →
`mcp` → registers `osascript-vm` → drives the flow through it → `down`.

## Known break: tart 2.35.0+ on pre-Tahoe hosts

`brew install openai/tools/tart` currently installs 2.37.0, which crashes
on launch (`dyld: ... libswiftCompatibilitySpan.dylib`) on macOS Sequoia
and older -- it's built against the macOS 26/Xcode 27 Swift toolchain
([openai/tart#1302](https://github.com/openai/tart/issues/1302), open).
`doctor` detects this by name and points here. Pin 2.34.0 instead, since
Homebrew now requires formulae to live in a tap (a loose `.rb` file won't
install):

```bash
brew uninstall tart 2>/dev/null
curl -LO https://github.com/openai/tart/releases/download/2.34.0/tart.tar.gz
tar -xzvf tart.tar.gz
mkdir -p ~/.local/bin
ln -sf "$PWD/tart.app/Contents/MacOS/tart" ~/.local/bin/tart
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

## Teardown

A clone left running still counts against Apple's 2-concurrent-macOS-VM
limit and holds disk. Always `down` a sandbox when the automation task is
done; `status` shows anything left over from an interrupted session.

## Sizing note

Sizing (2 vCPU / 4 GB / 1280x800) follows the testing in ["How fast is a
macOS VM, and how small could it
be?"](https://eclecticlight.co/2026/05/02/how-fast-is-a-macos-vm-and-how-small-could-it-be/),
which found 2 vCPU / 4 GB comfortably usable for everyday GUI tasks on
Apple silicon, using well under the memory ceiling. Disk can't shrink below
the base image's size, but APFS clones are sparse and copy-on-write, so
`up`'s per-session clone is fast and cheap regardless of the golden image's
~36 GB footprint.

## Permissions

`golden` grants Accessibility, Screen Capture, Post Event, and Apple Events
(System Events + Safari) to SSH-driven `osascript` via a direct TCC
database write -- no SIP disable needed. If that write is ever refused,
`golden --grant` boots with a display so the same grants can be made once
by hand; see [SKILL.md](SKILL.md) for the fallback and its Apple Events
caveat.
