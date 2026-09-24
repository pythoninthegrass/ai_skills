#!/bin/bash
# Grant Screen Capture / Post Event / Apple Events permissions to SSH-driven
# osascript, without disabling SIP.
#
# Trimmed from cirruslabs/macos-image-templates' scripts/update-tcc-database.sh
# (https://github.com/cirruslabs/macos-image-templates, base.pkr.hcl provisioner,
# fetched verbatim -- an earlier version of this file was reconstructed from a
# doc summary instead of the real source and had `client`/`client_type`
# transposed in the column list, silently storing garbage until a live
# AppleEvent test caught it), keeping only the sshd-keygen-wrapper and
# osascript rows, dropping the tart-guest-agent/python rows this skill doesn't
# need, and adding com.apple.Terminal to the indirect AppleEvents grants (this
# project drives DOSBox-X and other GUI apps via `tell application "Terminal"
# to do script ...`, which upstream's own row set doesn't cover). Run this
# INSIDE the guest as the admin user (tart_macos.py golden copies it over and
# invokes it).
#
# Deliberately NOT granting kTCCServiceAccessibility here, confirmed live in
# both directions: a pre-seeded row (auth_value=2) never actually works for
# this category on this macOS build -- keystroke/AXRaise still fail with
# -1719, no interactive dialog, tccd treats the client as already-decided.
# Leaving it unseeded makes the client genuinely undetermined, and macOS DOES
# then show a real "<client> would like to control this computer using
# accessibility features" dialog with an "Open System Settings" button, on
# the FIRST real access -- but only for the named-process AXRaise form (`tell
# process "<name>" to perform action "AXRaise"`); a bare `keystroke` call to
# System Events with no rows either never produced a dialog in testing. See
# tart_macos.py's ACCESSIBILITY_TRIGGER_CMD, fired by `golden --grant` right
# before leaving the VM running -- watch the window and click through when it
# appears (it appears immediately, but hasn't been observed to auto-dismiss
# quickly either; still, don't dawdle).
#
# kTCCServiceScreenCapture (still seeded below) needs TWO further manual
# steps beyond this script, confirmed live -- there is no known way to
# automate either yet:
#   1. The seeded row does NOT make the client appear in System Settings ->
#      Privacy & Security -> Screen Recording (unlike Accessibility, where a
#      seeded row does list it). It must be added by hand: '+' ->
#      /usr/libexec/sshd-keygen-wrapper (accepted as a raw path, enabled by
#      default on add). Without this, `screencapture -l <windowid>` hard
#      fails with "could not create image from window".
#   2. Even with that, whole-screen `screencapture -x` silently degrades to
#      desktop-wallpaper+menu-bar-only (macOS's standard no-permission
#      fallback, not an error) until a SEPARATE, newer consent layer is
#      granted: running `screencapture -x` for the first time (not any
#      osascript/AppleScript call -- that was a false lead) triggers a real
#      dialog: "com.apple.sshd-session is requesting to bypass the system
#      private window picker and directly access your screen and audio" with
#      Allow/Open-System-Settings buttons. This grant is NOT stored anywhere
#      in TCC.db (checked every table: access, access_overrides,
#      active_policy, admin, expired, policies -- all empty or irrelevant
#      after granting it), so it cannot be pre-seeded or scripted at all;
#      clicking "Allow" once is the only known way, and it persists once
#      baked into a stopped golden VM's disk, inherited by every clone.
set -euo pipefail

resolve_user_tcc_database() {
  local macos_version macos_major user_id open_files line candidate database=""
  macos_version="$(sw_vers -productVersion)"
  macos_major="${macos_version%%.*}"
  case "$macos_major" in
    '' | *[!0-9]*)
      echo "Unexpected macOS version: $macos_version" >&2
      return 1
      ;;
  esac

  if [[ "$macos_major" -lt 27 ]]; then
    database="${HOME}/Library/Application Support/com.apple.TCC/TCC.db"
  else
    # macOS 27 moved the user database into a per-user ProtectedSystem
    # container. Inspect the daemon's open files to avoid using a stale copy.
    user_id="$(id -u)"
    if ! open_files="$(sudo lsof -a -u "$user_id" -c tccd -Fn)"; then
      echo "Unable to inspect the user TCC daemon for UID $user_id" >&2
      return 1
    fi
    while IFS= read -r line; do
      case "$line" in
        n/private/var/containers/Data/ProtectedSystem/*/Data/Library/Application\ Support/com.apple.TCC/TCC.db)
          candidate="${line#n}"
          if [[ -n "$database" && "$database" != "$candidate" ]]; then
            echo "Found multiple active user TCC databases for UID $user_id" >&2
            return 1
          fi
          database="$candidate"
          ;;
      esac
    done <<<"$open_files"
    if [[ -z "$database" ]]; then
      echo "Unable to find the active user TCC database for UID $user_id" >&2
      return 1
    fi
  fi

  if ! sudo test -f "$database"; then
    echo "User TCC database does not exist: $database" >&2
    return 1
  fi
  if [[ "$macos_major" -ge 27 && "$(sudo stat -f %u "$database")" != "$user_id" ]]; then
    echo "Unexpected owner for user TCC database: $database" >&2
    return 1
  fi
  printf '%s\n' "$database"
}

update_tcc_database() {
  sudo sqlite3 "$1" <<-EOF
	INSERT OR REPLACE
	INTO access (
	  service,
	  client_type,
	  client,
	  auth_value,
	  auth_reason,
	  auth_version,
	  indirect_object_identifier_type,
	  indirect_object_identifier
	) VALUES
	-- Indirect osascript invocation via SSH
	('kTCCServiceScreenCapture', 1, '/usr/libexec/sshd-keygen-wrapper', 2, 0, 1, NULL, 'UNUSED'),
	('kTCCServicePostEvent', 1, '/usr/libexec/sshd-keygen-wrapper', 2, 0, 1, NULL, 'UNUSED'),
	('kTCCServiceAppleEvents', 1, '/usr/libexec/sshd-keygen-wrapper', 2, 0, 1, 0, 'com.apple.systemevents'),
	('kTCCServiceAppleEvents', 1, '/usr/libexec/sshd-keygen-wrapper', 2, 0, 1, 0, 'com.apple.Safari'),
	('kTCCServiceAppleEvents', 1, '/usr/libexec/sshd-keygen-wrapper', 2, 0, 1, 0, 'com.apple.Terminal'),
	-- Direct osascript invocation
	('kTCCServiceScreenCapture', 1, '/usr/bin/osascript', 2, 0, 1, NULL, 'UNUSED'),
	('kTCCServicePostEvent', 1, '/usr/bin/osascript', 2, 0, 1, NULL, 'UNUSED'),
	('kTCCServiceAppleEvents', 1, '/usr/bin/osascript', 2, 0, 1, 0, 'com.apple.systemevents'),
	('kTCCServiceAppleEvents', 1, '/usr/bin/osascript', 2, 0, 1, 0, 'com.apple.Safari'),
	('kTCCServiceAppleEvents', 1, '/usr/bin/osascript', 2, 0, 1, 0, 'com.apple.Terminal');
	EOF
}

main() {
  local database
  database="$(resolve_user_tcc_database)"
  # Upstream writes both the system and per-user TCC.db; an earlier trim of
  # this file dropped the system write entirely. Restored for fidelity, even
  # though it wasn't the cause of the ScreenCapture gap documented above.
  update_tcc_database "/Library/Application Support/com.apple.TCC/TCC.db"
  update_tcc_database "$database"
  echo "Granted ScreenCapture/PostEvent/AppleEvents (System Events/Safari/Terminal) to sshd-keygen-wrapper and osascript in: $database (Accessibility and full ScreenCapture deliberately left unset -- see header comment)"
}

main "$@"
