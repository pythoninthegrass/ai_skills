---
name: macos-sandbox
description: >
  Spin up the smallest usable macOS VM with Tart (Sequoia by default, runs
  on any Apple silicon host; Golden Gate/macOS 27 available for Tahoe+
  hosts), and wire osascript-mcp's desktop automation (keyboard, windows,
  menus, screenshots, Shortcuts) into it over SSH -- so automation runs in
  a sandboxed VM instead of taking over the host desktop. Use when the user
  wants desktop automation isolated from general computer use, asks to
  "sandbox osascript", "spin up a macOS VM for automation", "run
  osascript-mcp in a VM", or mentions Tart/tart-cli alongside desktop
  automation.
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

This skill gives osascript-mcp a Mac of its own: a small macOS VM under
Tart (Sequoia by default -- see "Host requirement" below for why), reached
over SSH. A second MCP server (`osascript-vm`, registered per session)
proxies into the VM, so the host's own `osascript-mcp` registration is
untouched and automation stays contained. Two servers, two Macs -- pick
`osascript-vm` for anything disruptive (typing, clicking, screenshots of a
full desktop), and the host one only when the task genuinely needs the
real machine.

## One-time setup: build the golden VM

```bash
"${SKILL_DIR}/scripts/tart_macos.py" doctor
```

Checks for `tart` (`brew install openai/tools/tart`), that `tart` actually
runs (not just that it's on PATH -- see the pinned-version note below),
`sshpass` (`brew install cirruslabs/cli/sshpass`), Apple silicon, **the
host's own macOS version** (see the ASIF requirement just below), and free
disk. Fix anything it flags before continuing -- it doesn't install for
you. It also warns (non-fatally) if the host's DHCP lease time isn't
shortened yet -- see the tweak just below; the warning doesn't block
`golden`/`up`.

**Host requirement (default image): none beyond Apple silicon.** The
default `TART_MACOS_IMAGE`, `ghcr.io/cirruslabs/macos-sequoia-vanilla`,
predates cirruslabs' move to Apple's ASIF disk format, so it runs on any
Apple-silicon host regardless of the host's own macOS version. This was
picked over the smaller/newer `macos-golden-gate-vanilla:27.0` (macOS 27)
specifically because of the requirement below, confirmed by real live
testing, not just reading upstream docs.

**If you opt into a newer guest image (e.g. `macos-golden-gate-vanilla`,
macOS 27) via `TART_MACOS_IMAGE`: the host itself must already be on macOS
26 (Tahoe) or later.** That image's disk uses Apple's ASIF format, which
the tart maintainers confirm
([openai/tart#1096](https://github.com/openai/tart/issues/1096)) "is
available only starting from macOS 26 (Tahoe). It's not available on macOS
15 (Sequoia)." On an older host, `tart run` fails immediately with `Disk
format 'asif' is not supported on this system` -- **unconditional, no
workaround via tart version or any flag.** `doctor` recognizes
`golden-gate` by name in `TART_MACOS_IMAGE` and checks the host's own
`sw_vers` before letting `golden`/`up` hang for `TART_MACOS_BOOT_TIMEOUT`
waiting for an IP that will never arrive (a VM that fails this way never
actually starts, so there's no error to catch downstream -- just a VM stuck
`stopped`). It correctly stays silent for the default Sequoia image even on
an old host, since that image doesn't need this at all.

**Known break, independent of the above: `openai/tools/tart` 2.35.0+
doesn't run on macOS Sequoia (or older) at all, regardless of which guest
image is configured.** That formula version and later are built against
the macOS 26/Xcode 27 Swift toolchain and crash on launch with a `dyld:
libswiftCompatibilitySpan.dylib` error on anything pre-Tahoe
([openai/tart#1302](https://github.com/openai/tart/issues/1302), open, fix
unmerged as of writing). `doctor` runs `tart --version` and reports this
exact error by name rather than a generic "not found". Fix by installing
2.34.0 directly (Homebrew now requires formulae live in a tap, so pointing
`brew install` at a loose `.rb` file won't work) -- this affects every host
on Sequoia or older, no matter which `TART_MACOS_IMAGE` is configured.

Extract to a permanent location outside any repo/scratch dir -- `$PWD`
means a later cleanup pass on whatever directory you happened to run this
from can delete `tart.app` out from under the symlink, leaving `tart`
"installed" but pointing at nothing:

```bash
mkdir -p ~/.local/opt ~/.local/bin
curl -L -o ~/.local/opt/tart.tar.gz https://github.com/openai/tart/releases/download/2.34.0/tart.tar.gz
tar -xzvf ~/.local/opt/tart.tar.gz -C ~/.local/opt
rm ~/.local/opt/tart.tar.gz
ln -sf ~/.local/opt/tart.app/Contents/MacOS/tart ~/.local/bin/tart
```

Re-check host compatibility if a later release ships the bundled-dylib fix
before relying on this pin indefinitely.

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

Idempotent -- if `macos-golden` already exists this is a no-op (pass `--force`
to rebuild it). On first run it:

1. Clones `ghcr.io/cirruslabs/macos-sequoia-vanilla:15.7.7` (vanilla, no
   brew/tooling baked in -- ~24 GB download, ~50 GB on disk; runs on any
   Apple-silicon host, see "Host requirement" above). Set `TART_MACOS_IMAGE`
   to `ghcr.io/cirruslabs/macos-golden-gate-vanilla:27.0` for the newer,
   smaller macOS 27 image instead, if the host is already on Tahoe+.
2. Sizes it to 2 vCPU / 4096 MB / 1280x800 -- per the Eclectic Light
   testing this article was seeded from, that's comfortably enough for
   everyday GUI automation on Apple silicon, using well under the memory
   ceiling.
3. Boots it headless, waits for an IP and SSH.
4. Runs `scripts/grant-tcc.sh` over SSH to grant Screen Capture, Post
   Event, and Apple Events (System Events, Safari, Terminal) permissions
   to SSH-driven `osascript` -- **no SIP disable required**, the vanilla
   image ships with SIP on and this only needs `sudo sqlite3` access to
   the per-user TCC database. Accessibility is deliberately left unseeded
   here -- see below.

   **Known gap, found live via a real e2e run (TASK-016 in
   swords_of_glass, driving DOSBox-X): Screen Capture has the identical
   seed-doesn't-take-effect problem as Accessibility, unfixed.**
   `screencapture` exits 0 but only ever renders desktop wallpaper + menu
   bar, never real window content -- macOS's standard silent fallback when
   Screen Recording isn't actually granted. `screencapture -l <windowid>`
   (no degraded fallback) confirms it: hard fails with `could not create
   image from window`, on both headless and `--gui` boots, despite
   `kTCCServiceScreenCapture` showing `auth_value=2` in the database.
   **Don't trust a screenshot from this skill to show real app content
   yet.** The Accessibility fix below is the likely template (leave the
   row unset, find the trigger that produces a real Screen Recording
   dialog, click through once into golden) but hasn't been attempted.
5. Installs `uv` in the guest.
6. Authorizes `TART_MACOS_GITHUB_KEYS_USER`'s (default `pythoninthegrass`)
   GitHub public keys for inbound SSH (`curl .../pythoninthegrass.keys >>
   ~/.ssh/authorized_keys`) and seeds `known_hosts` for `github.com` --
   **no private key is ever copied into the guest.** This is baked into
   `macos-golden` once, so every ephemeral clone inherits it from first boot.
7. Stops the VM.

`golden` is idempotent by checking whether `macos-golden` exists at all
(`tart list`), not whether it's currently reachable -- an earlier version of
this check used `tart ip`, which only works while a VM is running, so a
normal `golden` run (which ends by stopping the VM) would make the *next*
run wrongly conclude the VM didn't exist and try to re-clone into a name
that was already taken.

**If step 4 is refused** (a locked-down TCC database, or a macOS point
release that moved something): re-run with `--grant`, which boots the VM
with a display so the permissions can be granted once by hand in System
Settings → Privacy & Security. They persist in `macos-golden`, so every clone
inherits them -- this is a one-time fallback, not a per-session step.

**Accessibility needs this `--grant` path -- there's a real, working happy
path for it, not just a manual fallback.** Confirmed live: a pre-seeded
`kTCCServiceAccessibility` row (`auth_value=2`, allowed) never actually
works for `/usr/bin/osascript`/`/usr/libexec/sshd-keygen-wrapper`
(`sshd-keygen-wrapper` is the actual client macOS attributes an
SSH-invoked request to) -- `keystroke`/`AXRaise` still fail with `-1719`,
silently, no dialog, tccd treats the client as already-decided. So
`grant-tcc.sh` leaves it unseeded on purpose: a genuinely undetermined
client, hit with the right trigger (the named-process `AXRaise` form,
`tell process "Terminal" to perform action "AXRaise"` -- a bare
`keystroke` call did *not* produce a dialog in testing), makes macOS show
a real "`sshd-keygen-wrapper` would like to control this computer..."
dialog with an "Open System Settings" button. `golden --grant` fires this
trigger automatically and leaves the VM running with instructions --
watch the window, click through, then re-run `golden --grant`: it resumes
the running VM (doesn't re-clone), re-verifies via a side-effect-free
probe (`UI elements enabled`, which reports Accessibility's real state
without needing the permission itself to ask), and only then stops the VM
and reports `Accessibility confirmed active`. Verified end-to-end. This
whole dance is only needed for `keystroke`/`AXRaise`-style UI scripting
via System Events -- launching apps and driving them via `do script`
already work from a completely unpatched boot.

A PPPC configuration profile
(`com.apple.TCC.configuration-profile-policy`) is the Apple-sanctioned way
to pre-approve TCC grants without any manual step, and was tried first:
`profiles install -type configuration -path ...` on this macOS version
refuses outright (`profiles tool no longer supports installs`) -- Apple
removed CLI profile installation; it now needs genuine MDM enrollment, out
of scope for a standalone sandbox VM.

Sending Apple Events to any app other than System Events or Safari still
prompts once the first time it happens. If a task needs another app,
trigger that prompt once inside `macos-golden` (via `--grant`) so clones
inherit the grant too.

## Per-session: clone, wire up, tear down

```bash
RES=$("${SKILL_DIR}/scripts/tart_macos.py" up)
NAME=$(echo "$RES" | jq -r .name)
IP=$(echo "$RES" | jq -r .ip)
```

Clones `macos-golden` into a fresh ephemeral VM (`macos-sbx-<timestamp>` unless
you pass a name), boots it headless (`--no-graphics --no-audio
--no-clipboard`), mounts the current directory (or `--repo PATH`) at the
guest's shared `repo` folder -- `/Volumes/My Shared Files/repo`, a live
virtiofs share, no copy step -- and waits for IP + SSH. APFS clones are
sparse and copy-on-write, so this is fast and cheap on disk despite the
golden image's size. `--no-repo` skips the mount entirely; `--repo-ro`
mounts it read-only. Pass `--softnet` for stricter network isolation
(Tart's Softnet userspace filter) if the automation task shouldn't reach
the LAN freely.

```bash
"${SKILL_DIR}/scripts/tart_macos.py" mcp "$NAME"
```

Prints (doesn't run) the registration command:

```bash
claude mcp add osascript-vm -- sshpass -p admin ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -A admin@<ip> '~/.local/bin/uvx --from git+https://github.com/pythoninthegrass/osascript-mcp osascript-mcp'
```

The `-A` forwards the host's ssh-agent, so `git push`/`pull` against the
mounted repo, run from inside the guest, authenticates using the host's
already-loaded identity -- combined with golden's GitHub-keys bootstrap
above, no private key or password ever needs to reach the guest for either
direction of SSH.

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

Lists `macos-*` VMs and their state at any point -- useful before `up` if a
previous session's teardown didn't run.

## Optional: `run.py` + `playbook.yml` for repeatable per-repo provisioning

`up` + `mcp` + manual setup steps is enough for a one-off. When a repo needs
the same guest-side setup every time (install deps, open a specific app,
sanity-check the mount), drop a real Ansible playbook at its root and let
`run.py` drive the whole thing in one call:

```bash
cp "${SKILL_DIR}/playbook.example.yml" /path/to/repo/playbook.yml   # edit it
cd /path/to/repo
"${SKILL_DIR}/scripts/run.py" up
```

This calls `tart_macos.py up --repo "$PWD"` under the hood, builds a
dynamic Ansible inventory from the VM's own IP (same shape as
`~/git/nw_infra/pulumi/k3s/ansible/inventory.yml` -- `ansible_host` under
`hosts`, connection vars under `vars`, no password or private key needed
since golden's GitHub-keys bootstrap already covers auth), runs
`playbook.yml` in-process via `ansible.cli.playbook.PlaybookCLI` (the same
pattern as `~/git/nw_infra/networking/dhcp/run.py`), prints the `mcp add`
command, and remembers the VM in `.macos-sandbox-state.json` next to the
playbook. Tear down with no arguments:

```bash
"${SKILL_DIR}/scripts/run.py" down
```

`run.py up` skips provisioning (with a warning, not a failure) if the repo
has no `playbook.yml` -- it's an additive layer, not a requirement for
`tart_macos.py up`/`down` to keep working standalone.

## Concurrency limit

Apple's license permits at most 2 concurrent macOS VMs per host. Check
`status` before spinning up a second sandbox; don't fan out more than 2
`up` calls without stopping one first.

Separately, `golden`/`up`/`down` each take a per-VM-name lock file under
`/tmp/tart-macos-sandbox-<name>.lock` (`flock`, non-blocking) before
touching a VM -- `golden` takes it exclusive, `up` takes a shared lock on
the golden name (so multiple `up`s can clone from it concurrently) plus an
exclusive lock on its own new name. A second invocation racing the same
name fails fast with `FAIL: '<name>' is locked...` instead of interleaving
`tart clone`/`set`/`run`/`stop`/`delete` calls against the same VM --
discovered for real when a disowned background `golden` outlived its
wrapper and a second `golden` was launched before noticing, racing both
against `macos-golden`.

## Bundled scripts

`scripts/tart_macos.py` -- a self-contained `uv run --script` (PEP 723)
tool with `doctor`, `golden`, `up`, `mcp`, `status`, and `down`
subcommands, described above. Every tunable resolves through
`python-decouple`: CLI flag > process env > `skills/macos-sandbox/.env`
> hardcoded default. See `.env.example` for the full list of
`TART_MACOS_*` names. Run `scripts/tart_macos.py -h` for the flag list, and
review the script before first use.

`scripts/grant-tcc.sh` -- trimmed from cirruslabs/macos-image-templates'
`update-tcc-database.sh` (fetched verbatim from source -- an earlier version
of this file was reconstructed from a doc summary instead and had
`client`/`client_type` transposed in the `INSERT`'s column list, silently
writing garbage rows that nothing caught until a live AppleEvent test
actually exercised them), run inside the guest by `golden` to grant
Accessibility/Screen Capture/Post Event/Apple Events (System Events, Safari,
**and Terminal** -- added beyond upstream's own row set, since this is the
exact app this skill's target use case drives) to SSH-driven `osascript`
without disabling SIP.

`scripts/test_tart_macos.py` -- the accompanying pytest suite, also a
self-contained `uv run --script`. Run it directly
(`./scripts/test_tart_macos.py`) after changing `tart_macos.py`.

`scripts/run.py` -- optional `uv run --script` wrapper that shells out to
`tart_macos.py up`/`mcp`/`down` and drives a real Ansible playbook against
the fresh VM (see the section above). `scripts/test_run.py` covers its pure
plumbing (state file, inventory shape, playbook resolution); it doesn't
invoke `ansible-playbook` itself.

`playbook.example.yml` -- copy to `playbook.yml` in the repo being
automated. A real Ansible playbook, not a custom DSL.
