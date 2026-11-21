#!/usr/bin/env bash
# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
# ==========================================================
# Update the app to the latest version.
#
#   ./scripts/update.sh              # back up, update, rebuild, restart, tidy up
#   ./scripts/update.sh --check      # only say what's new; change nothing
#   ./scripts/update.sh --no-backup  # skip the database backup first
#   ./scripts/update.sh --rebuild    # rebuild (or download again) even if there's nothing new
#   ./scripts/update.sh --to 1.2.0   # a release install: move to that release (e.g. to go back)
#
# Two kinds of install:
# - From a release (setup.sh's default; .env has APP_TAG): moves to the newest
#   release and downloads its ready-made images.
# - Built from the code (no APP_TAG): follows the branch you're on, and
#   rebuilds the images that changed.
#
# In order: checks it's safe to start; backs up the database; gets the new
# version; downloads or rebuilds the images; restarts, waiting for database
# changes to apply; clears Docker's leftovers (unused images, build pieces
# over a week old; never volumes, which hold the data); and says how to go back.
#
# Apart from APP_TAG for a release install, it never changes .env or data/,
# and looks after this app only.
# ==========================================================
set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31m%s\033[0m\n' "$*" >&2; exit 1; }

CHECK=0; BACKUP=1; REBUILD=0; TO=""
while [ $# -gt 0 ]; do
    arg="$1"; shift
    case "$arg" in
        --check) CHECK=1 ;;
        --no-backup) BACKUP=0 ;;
        --rebuild) REBUILD=1 ;;
        --to) TO="${1:-}"; [ -n "$TO" ] || fail "--to needs a version, e.g. --to 1.2.0"; shift; TO="${TO#v}" ;;
        -h|--help) sed -n '6,28p' "$0"; exit 0 ;;
        *) fail "Unknown option: $arg (see --help)" ;;
    esac
done
NEED_GB=5

# --- 1. Safe to start? ---
command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 || fail "Docker with the Compose plugin is needed."
[ -f .env ] || fail "No .env here. Run this from the folder the app is installed in."
[ -d .git ] || fail "This folder isn't a git checkout, so it can't be updated with git."
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    git status --short --untracked-files=no
    fail "The app's files have been changed here (above). Put those changes away first (git stash), so the update can't tangle with them."
fi
# The receipt reader is an optional add-on (COMPOSE_PROFILES=receipts in .env).
# A reader that's running but not switched on would be left behind, old.
# (Docker's whole answer is read first: searching it while it's still being
# written can cut it off, which strict mode takes as a failure.)
services=$(docker compose config --services) || fail "Docker Compose couldn't read the app's settings (its message is above). Nothing was changed."
reader_on=0
if grep -qx ai-worker <<<"$services"; then reader_on=1; fi
project=$(docker compose config | sed -n 's/^name: //p') || true
project=${project%%$'\n'*}
if [ "$reader_on" = 0 ] && [ -n "$project" ] && [ -n "$(docker ps -aq --filter "label=com.docker.compose.project=$project" --filter label=com.docker.compose.service=ai-worker)" ]; then
    fail "The receipt reader is installed here, but .env doesn't switch it on (it's an optional add-on now).
To keep it: add this line to .env, then run this again:
    COMPOSE_PROFILES=receipts
(If .env already has a COMPOSE_PROFILES line, add receipts to it, separated by a comma.)
To remove it instead: ./scripts/receipt-reader.sh off"
fi
free_gb=$(df -Pk . | awk 'NR==2 {printf "%d", $4 / 1048576}')

# A release install runs ready-made images: .env says which release (APP_TAG)
env_value() { sed -n "s/^$1=//p" .env | tail -1 | tr -d ' "'"'"; }
tag=$(env_value APP_TAG)
# Rewrites one setting in .env, keeping every other line and the file's
# permissions (it holds secrets: written in place, not replaced)
set_env_value() {
    local tmp; tmp=$(mktemp)
    grep -v "^$1=" .env > "$tmp" || true
    printf '%s=%s\n' "$1" "$2" >> "$tmp"
    cat "$tmp" > .env; rm -f "$tmp"
}
# Waits for the backend to finish applying database changes after a restart
wait_until_up() {
    echo "Waiting for the app to apply any database changes..."
    for _ in $(seq 1 90); do
        if docker compose exec -T backend python manage.py migrate --check >/dev/null 2>&1; then echo "The app is up."; return 0; fi
        sleep 2
    done
    docker compose logs backend --tail 30
    return 1
}
# Clears Docker's leftovers; never volumes, which hold the data
tidy_up() {
    say "Tidying up"
    local before_free after_free
    before_free=$(df -Pk . | awk 'NR==2 {print $4}')
    docker image prune -f >/dev/null
    docker builder prune -f --filter until=168h >/dev/null 2>&1 || true
    after_free=$(df -Pk . | awk 'NR==2 {print $4}')
    echo "Freed $(( (after_free - before_free) / 1024 )) MB of old images and build pieces; $(df -Pk . | awk 'NR==2 {printf "%d", $4 / 1048576}') GB free."
}
backup_first() {
    if [ "$BACKUP" = 1 ]; then
        say "Backing up the database"
        docker compose exec -T backend python manage.py create_backup db || fail "The backup failed, so nothing was changed. (--no-backup skips it.)"
    fi
}

if [ -n "$tag" ]; then
    # ================= An install from a release =================
    echo "Installed: release $tag. Free disk: ${free_gb} GB."
    say "Checking for a new release"
    git fetch --quiet --tags origin || fail "Couldn't reach the repository to check for updates."
    if [ -n "$TO" ]; then
        target="$TO"
        git rev-parse --verify --quiet "refs/tags/v$target" >/dev/null || fail "There's no release $target. The releases are listed with: git tag -l 'v*'"
    else
        releases=$(git tag -l 'v[0-9]*.[0-9]*.[0-9]*')
        target=$(grep -v -- - <<<"$releases" | sort -V | tail -1 || true)
        target="${target#v}"
        [ -n "$target" ] || fail "No releases were found in the repository."
    fi
    if [ "$target" = "$tag" ] && [ "$REBUILD" = 0 ]; then
        echo "Already on the latest release ($tag)."
        exit 0
    fi
    if [ "$target" != "$tag" ]; then
        echo "Release $target is available (you have $tag)."
        if git rev-parse --verify --quiet "refs/tags/v$tag" >/dev/null && git merge-base --is-ancestor "v$tag" "v$target" 2>/dev/null; then
            echo "New since your release:"
            git log --oneline --no-decorate "v$tag..v$target" | sed 's/^/  /'
        fi
    fi
    [ "$CHECK" = 1 ] && { echo; echo "(--check: nothing was changed.)"; exit 0; }
    [ "$free_gb" -ge "$NEED_GB" ] || fail "Only ${free_gb} GB of disk is free; an update needs about ${NEED_GB} GB. Free some space, then run this again."

    backup_first

    say "Getting release $target"
    git -c advice.detachedHead=false checkout --quiet "v$target" || fail "Couldn't switch the app's files to release $target, so nothing was changed."
    set_env_value APP_TAG "$target"
    say "Downloading its images"
    if ! docker compose pull --quiet; then
        fail "Couldn't download the images for $target. To go back: ./scripts/update.sh --to $tag"
    fi

    say "Restarting"
    docker compose up -d --no-build
    wait_until_up || fail "The app didn't come back up (its log is above). To go back: ./scripts/update.sh --to $tag"
    tidy_up
    say "Done"
    echo "Release: $target (was $tag)."
    [ "$BACKUP" = 1 ] && echo "The backup from just before is listed in Settings → Backups."
    echo "To go back: ./scripts/update.sh --to $tag (if the database was changed, restore the backup too)."
    exit 0
fi

# ================= An install built from the code =================
[ -z "$TO" ] || fail "--to is for installs from a release. This one is built from the code: use git to choose a version."
before=$(git rev-parse --short HEAD)
version() { git describe --tags --always 2>/dev/null | sed 's/^v//'; }
echo "Installed: $(version) ($before). Free disk: ${free_gb} GB."

# --- What's new ---
say "Checking for a new version"
git fetch --quiet --tags origin || fail "Couldn't reach the repository to check for updates."
branch=$(git rev-parse --abbrev-ref HEAD)
[ "$branch" != HEAD ] || fail "You're on an earlier version on purpose (after going back). To update again: git switch main, then run this again."
upstream="origin/$branch"
git rev-parse --verify --quiet "$upstream" >/dev/null || fail "The branch you're on ($branch) isn't on GitHub, so there's nothing to update it from. Updates come from main: git switch main, then run this again."
if [ "$(git rev-list --count "HEAD..$upstream")" = 0 ]; then
    echo "Already up to date."
    if [ "$REBUILD" = 0 ]; then exit 0; fi
else
    echo "New since your version:"
    git log --oneline --no-decorate "HEAD..$upstream" | sed 's/^/  /'
fi
[ "$CHECK" = 1 ] && { echo; echo "(--check: nothing was changed.)"; exit 0; }
[ "$free_gb" -ge "$NEED_GB" ] || fail "Only ${free_gb} GB of disk is free; a rebuild needs about ${NEED_GB} GB. Free some space, then run this again."

# --- 2. Back up the database ---
backup_first

# --- 3. Get the new version ---
if [ "$(git rev-list --count "HEAD..$upstream")" != 0 ]; then
    say "Getting the new version"
    git merge --ff-only --quiet "$upstream" || fail "Your copy and the repository have gone separate ways, so nothing was changed. Ask for help before going further."
fi
after=$(git rev-parse --short HEAD)
# The version recorded in the app: the release this code is, or the latest
# release it's newer than, plus how many changes newer ("1.0.2+3"), so the
# release check knows where it stands. Test releases (1.2.0-rc1) don't count.
export APP_VERSION="$(git describe --tags --match 'v[0-9]*.[0-9]*.[0-9]*' --exclude '*-*' 2>/dev/null \
    | sed -E 's/^v//; s/-([0-9]+)-g[0-9a-f]+$/+\1/' || true)"

# --- 4. Rebuild what changed, one image at a time ---
say "Rebuilding"
docker compose build frontend
docker compose build backend
if [ "$reader_on" = 1 ] && { [ "$REBUILD" = 1 ] || ! git diff --quiet "$before" "$after" -- ai_worker; }; then
    echo "The receipt reader changed: rebuilding it too (this one takes a while)."
    docker compose build ai-worker
fi

# --- 5. Restart and confirm ---
say "Restarting"
docker compose up -d
wait_until_up || fail "The app didn't come back up (its log is above). To go back: git checkout $before && docker compose build frontend backend && docker compose up -d"

# --- 6. Clear Docker's leftovers ---
tidy_up

# --- 7. Summary ---
say "Done"
echo "Version: $(version) (was $before, now $after)."
[ "$BACKUP" = 1 ] && echo "The backup from just before is listed in Settings → Backups."
echo "To go back to the previous version: git checkout $before && docker compose build frontend backend && docker compose up -d"
echo "(and to update again later: git switch $branch, then this script.)"
