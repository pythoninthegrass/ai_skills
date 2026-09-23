#!/bin/bash
# Grant Accessibility / Screen Capture / Post Event / Apple Events permissions
# to SSH-driven osascript, without disabling SIP.
#
# Trimmed from cirruslabs/macos-image-templates' scripts/update-tcc-database.sh
# (https://github.com/cirruslabs/macos-image-templates, base.pkr.hcl provisioner),
# keeping only the sshd-keygen-wrapper and osascript rows and dropping the
# tart-guest-agent / python rows this skill doesn't need. Run this INSIDE the
# guest as the admin user (tart_macos.py golden copies it over and invokes it).
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
  sudo sqlite3 "$1" <<-'EOF'
	INSERT OR REPLACE
	INTO access (
	  service,
	  client,
	  client_type,
	  auth_value,
	  auth_reason,
	  auth_version,
	  indirect_object_identifier,
	  flags
	)
	VALUES
	-- Indirect osascript invocation via SSH
	('kTCCServiceAccessibility', 1, '/usr/libexec/sshd-keygen-wrapper', 2, 0, 1, NULL, 'UNUSED'),
	('kTCCServiceScreenCapture', 1, '/usr/libexec/sshd-keygen-wrapper', 2, 0, 1, NULL, 'UNUSED'),
	('kTCCServicePostEvent', 1, '/usr/libexec/sshd-keygen-wrapper', 2, 0, 1, NULL, 'UNUSED'),
	('kTCCServiceAppleEvents', 1, '/usr/libexec/sshd-keygen-wrapper', 2, 0, 1, 0, 'com.apple.systemevents'),
	('kTCCServiceAppleEvents', 1, '/usr/libexec/sshd-keygen-wrapper', 2, 0, 1, 0, 'com.apple.Safari'),
	-- Direct osascript invocation
	('kTCCServiceAccessibility', 1, '/usr/bin/osascript', 2, 0, 1, NULL, 'UNUSED'),
	('kTCCServiceScreenCapture', 1, '/usr/bin/osascript', 2, 0, 1, NULL, 'UNUSED'),
	('kTCCServicePostEvent', 1, '/usr/bin/osascript', 2, 0, 1, NULL, 'UNUSED'),
	('kTCCServiceAppleEvents', 1, '/usr/bin/osascript', 2, 0, 1, 0, 'com.apple.systemevents'),
	('kTCCServiceAppleEvents', 1, '/usr/bin/osascript', 2, 0, 1, 0, 'com.apple.Safari');
	EOF
}

main() {
  local database
  database="$(resolve_user_tcc_database)"
  update_tcc_database "$database"
  echo "Granted Accessibility/ScreenCapture/PostEvent/AppleEvents to sshd-keygen-wrapper and osascript in: $database"
}

main "$@"
