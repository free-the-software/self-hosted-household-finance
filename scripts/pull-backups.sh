#!/usr/bin/env bash
# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
# ----------------------------------------------------------
# Pull the app's backup files from the server to this machine.
#
# Run it on ANOTHER computer (a desktop, a NAS), by hand or from a timer.
# That machine fetches the backups over SSH, so the server never holds
# credentials for it: someone who breaks into the server cannot reach or
# destroy the copies kept here.
#
#   FINANCE_BACKUP_SERVER=user@server \
#   FINANCE_BACKUP_DEST=~/Finance_Backups \
#   ./scripts/pull-backups.sh
#
# Settings can also live in ~/.config/finance-backup-pull.env (one
# NAME=value per line), which the timer in the README uses.
#
#   FINANCE_BACKUP_SERVER      user@host of the server              (required)
#   FINANCE_BACKUP_DEST        folder on this machine               (required)
#   FINANCE_BACKUP_REMOTE_DIR  backups folder on the server         (default: finance/data/backups)
#   FINANCE_BACKUP_SSH_KEY     private key file to log in with      (default: your usual SSH keys)
#   FINANCE_BACKUP_KEEP_DAYS   delete local copies older than this  (default: 90, 0 = never delete)
#   FINANCE_BACKUP_KEEP_MIN    always keep this many of each kind   (default: 3)
#   FINANCE_BACKUP_WARN_DAYS   fallback only, see below            (default: 3, 0 = never warn)
#
# The server keeps a small note, backup-status.json, beside its backups: the
# schedule set in the app, the newest backup of each kind, and whether the app
# thinks its backups are healthy. This script fetches that note and warns
# according to it, so it follows whatever schedule is set in the app. It also
# passes on a failure reported by the server, and notices when the server's
# scheduler has stopped. FINANCE_BACKUP_WARN_DAYS is used only when there is no
# note to read (an older server, or python3 missing on this machine).
#
# Only copies files; it never deletes anything on the server, and it never
# deletes a local copy just because the server no longer has it.
#
# It talks to the server through rsync alone, so it works with a key that is
# locked down on the server to read-only access to the backups folder
# (rrsync). The README shows how to set one up; with such a key, set
# FINANCE_BACKUP_REMOTE_DIR=. because the key already points at the folder.
# ----------------------------------------------------------
set -euo pipefail

CONFIG="${FINANCE_BACKUP_CONFIG:-$HOME/.config/finance-backup-pull.env}"
if [ -f "$CONFIG" ]; then
  set -a; . "$CONFIG"; set +a
fi

SERVER="${FINANCE_BACKUP_SERVER:?set FINANCE_BACKUP_SERVER to user@server}"
DEST="${FINANCE_BACKUP_DEST:?set FINANCE_BACKUP_DEST to a folder on this machine}"
REMOTE_DIR="${FINANCE_BACKUP_REMOTE_DIR:-finance/data/backups}"
KEEP_DAYS="${FINANCE_BACKUP_KEEP_DAYS:-90}"
KEEP_MIN="${FINANCE_BACKUP_KEEP_MIN:-3}"
WARN_DAYS="${FINANCE_BACKUP_WARN_DAYS:-3}"

SSH_KEY="${FINANCE_BACKUP_SSH_KEY:-}"

# BatchMode: never stop to ask for a password, since nobody is there to answer
SSH="ssh -o BatchMode=yes -o ConnectTimeout=15"
if [ -n "$SSH_KEY" ]; then
  SSH="$SSH -i $SSH_KEY -o IdentitiesOnly=yes"
fi

notify() {
  echo "$1" >&2
  if command -v notify-send >/dev/null 2>&1; then
    notify-send --urgency=critical "Finance backups" "$1" 2>/dev/null || true
  fi
}

command -v rsync >/dev/null 2>&1 || { notify "rsync is not installed on this machine."; exit 1; }
mkdir -p "$DEST"

# --- 1. Copy any backup files this machine does not have yet ---
pulled=0
reachable=0
new_files=""
errors="$(mktemp)"
trap 'rm -f "$errors"' EXIT

# Ask the server which backups it has. Only files named like backups are listed.
if listing="$(rsync --list-only --include='finance-db-*.tar*' --include='finance-full-*.tar*' --exclude='*' \
      -e "$SSH" "$SERVER:$REMOTE_DIR/" 2>"$errors")"; then
  # Skip any older than the local keep period: fetching those would only
  # have them deleted again in step 2, on every run.
  cutoff=0
  [ "$KEEP_DAYS" -gt 0 ] && cutoff="$(( $(date +%s) - KEEP_DAYS * 86400 ))"
  # A listing line reads: -rw-r--r--  1,234 2026/10/11 02:00:05 name
  wanted="$(printf '%s\n' "$listing" | while read -r mode _size day time name; do
      case "$mode" in -*) ;; *) continue ;; esac
      [ -n "$name" ] || continue
      stamp="$(date -d "${day//\//-} $time" +%s 2>/dev/null || echo 0)"
      if [ "$stamp" -ge "$cutoff" ]; then printf '%s\n' "$name"; fi
    done)"

  if [ -n "$wanted" ]; then
    new_files="$(printf '%s\n' "$wanted" | rsync --archive --partial --out-format='%n' \
        --files-from=- -e "$SSH" "$SERVER:$REMOTE_DIR/" "$DEST/")"
    pulled="$(printf '%s\n' "$new_files" | grep -c 'finance-' || true)"
  fi
  echo "Pulled $pulled new backup file(s) from $SERVER."
  reachable=1
  # The server's own summary of its backups. An older server has none.
  rsync --archive -e "$SSH" "$SERVER:$REMOTE_DIR/backup-status.json" "$DEST/" 2>/dev/null || true
else
  # Not an error by itself: the server may be off, or this machine away
  # from home. The age check below is what catches a real problem.
  echo "Could not fetch from $SERVER. Nothing pulled. The reason given was:"
  sed 's/^/    /' "$errors" | tail -n 3
fi

# --- 2. Remove old local copies, keeping the newest few of each kind ---
if [ "$KEEP_DAYS" -gt 0 ]; then
  for prefix in finance-db- finance-full-; do
    # Newest first; skip the ones always kept; delete the rest once too old
    find "$DEST" -maxdepth 1 -type f -name "${prefix}*.tar*" -printf '%T@ %p\n' \
      | sort -rn | tail -n +"$((KEEP_MIN + 1))" | cut -d' ' -f2- \
      | while IFS= read -r file; do
          if [ -n "$(find "$file" -mtime +"$KEEP_DAYS" -print)" ]; then
            rm -f -- "$file"
            echo "Removed old local copy: $(basename "$file")"
          fi
        done
  done
fi

# --- 3. Decide whether anything needs the user's attention ---
count="$(find "$DEST" -maxdepth 1 -type f -name 'finance-*.tar*' | wc -l)"
echo "$count backup file(s) in $DEST."

if [ -f "$DEST/backup-status.json" ] && command -v python3 >/dev/null 2>&1; then
  # Judge by the server's note: its schedule, its newest backups, its health.
  problems="$(DEST="$DEST" REACHABLE="$reachable" NEW_FILES="$new_files" python3 - <<'PY'
import hashlib, json, os, sys, time
from datetime import datetime, timezone

dest = os.environ["DEST"]
reachable = os.environ["REACHABLE"] == "1"
just_pulled = set(os.environ["NEW_FILES"].split("\n"))
try:
    with open(os.path.join(dest, "backup-status.json")) as handle:
        status = json.load(handle)
    kinds = status["kinds"]
except (OSError, ValueError, KeyError):
    print("The server's backup-status.json could not be read.")
    sys.exit(0)

now = time.time()
day = 86400
# Days a copy of each kind may age before it is worth a warning: the
# interval between backups plus a margin for this machine being switched off.
allowed = {"daily": 3, "weekly": 9, "monthly": 35}
problems = []

if reachable:
    # Only meaningful when the note is fresh from the server
    health = status.get("health", {})
    if health.get("state") in ("failed", "overdue"):
        problems.append("The server reports a backup problem: " + health.get("message", ""))
    elif health.get("state") == "setup":
        problems.append("Backups are not set up on the server yet (encryption has not been chosen).")
    try:
        written = datetime.fromisoformat(status["written_at"]).timestamp()
        if now - written > 3 * 3600 and any(k.get("frequency") != "off" for k in kinds.values()):
            problems.append("The server last updated its backup status %d hours ago. Its scheduler may have stopped." % ((now - written) // 3600))
    except (KeyError, ValueError):
        pass

for kind, info in kinds.items():
    label = info.get("label", kind).lower()
    latest = info.get("latest")
    if latest:
        path = os.path.join(dest, latest["filename"])
        if not os.path.exists(path):
            if reachable:
                problems.append("The newest %s on the server (%s) has not been copied here." % (label, latest["filename"]))
        elif latest["filename"] in just_pulled and latest.get("sha256"):
            # Check a freshly copied file against the server's checksum
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != latest["sha256"]:
                problems.append("The copy of %s here does not match the server's checksum. It may be damaged." % latest["filename"])

    limit = allowed.get(info.get("frequency"))
    if limit is None:
        continue  # this kind of backup is switched off: nothing is expected
    copies = [os.path.getmtime(os.path.join(dest, name)) for name in os.listdir(dest)
              if name.startswith("finance-%s-" % kind) and ".tar" in name]
    if copies and now - max(copies) > limit * day:
        problems.append("No new %s has been copied here for %d days, but the schedule is %s." % (label, (now - max(copies)) // day, info["frequency"]))

print("\n".join(problems))
PY
)"
  if [ -n "$problems" ]; then
    notify "$problems"
    exit 1
  fi
  echo "Checked against the server's backup status: nothing needs attention."
elif [ "$WARN_DAYS" -gt 0 ]; then
  # No note from the server: fall back to a fixed age limit
  if [ "$count" -eq 0 ]; then
    notify "No backups have been copied to this machine yet."
    exit 1
  fi
  if [ -z "$(find "$DEST" -maxdepth 1 -type f -name 'finance-*.tar*' -mtime -"$WARN_DAYS" -print -quit)" ]; then
    notify "The newest backup copied to this machine is more than $WARN_DAYS days old. Check that backups are running on the server and that it can be reached."
    exit 1
  fi
fi
