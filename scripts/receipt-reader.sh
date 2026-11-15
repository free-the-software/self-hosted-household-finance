#!/usr/bin/env bash
# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
# ==========================================================
# Add or remove the receipt reader, an optional add-on (BETA).
#
#   ./scripts/receipt-reader.sh on       # add it: builds and starts it
#   ./scripts/receipt-reader.sh off      # remove it and free its disk space
#   ./scripts/receipt-reader.sh status   # say whether it's installed
#
# It reads photos of receipts and suggests the transaction each belongs to.
# It often misreads, and nothing changes until you approve it. It needs
# about 2.5 GB more disk and about 2 GB more memory while reading; the first
# build takes 10 minutes or more.
#
# "on" adds "receipts" to COMPOSE_PROFILES in .env; "off" takes it out.
# Nothing else in .env changes, and receipts already read stay in the app.
# ==========================================================
set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31m%s\033[0m\n' "$*" >&2; exit 1; }

ACTION="${1:-status}"
case "$ACTION" in
    on|off|status) ;;
    -h|--help) sed -n '5,19p' "$0"; exit 0 ;;
    *) fail "Unknown option: $ACTION (use on, off or status)" ;;
esac
NEED_GB=5

command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 || fail "Docker with the Compose plugin is needed."
[ -f .env ] || fail "No .env here. Run this from the folder the app is installed in."

# The profiles in .env, comma-separated (empty if none)
profiles() { sed -n 's/^COMPOSE_PROFILES=//p' .env | tail -1 | tr -d ' "'"'"; }
is_on() { case ",$(profiles)," in *,receipts,*) return 0 ;; *) return 1 ;; esac; }

# Rewrite COMPOSE_PROFILES in .env, keeping every other line and the file's
# permissions (it holds secrets: written in place, not replaced)
set_profiles() {
    local value="$1" tmp
    tmp=$(mktemp)
    grep -v '^COMPOSE_PROFILES=' .env > "$tmp" || true
    [ -n "$value" ] && printf 'COMPOSE_PROFILES=%s\n' "$value" >> "$tmp"
    cat "$tmp" > .env
    rm -f "$tmp"
}

if [ "$ACTION" = status ]; then
    if is_on; then
        echo "The receipt reader is installed (COMPOSE_PROFILES=$(profiles) in .env)."
        docker compose ps ai-worker 2>/dev/null || true
    else
        echo "The receipt reader isn't installed. To add it: ./scripts/receipt-reader.sh on"
    fi
    exit 0
fi

if [ "$ACTION" = on ]; then
    if is_on; then
        echo "The receipt reader is already switched on in .env; making sure it's built and running."
    else
        free_gb=$(df -Pk . | awk 'NR==2 {printf "%d", $4 / 1048576}')
        [ "$free_gb" -ge "$NEED_GB" ] || fail "Only ${free_gb} GB of disk is free; the receipt reader needs about ${NEED_GB} GB to build. Free some space, then run this again."
        current=$(profiles)
        set_profiles "${current:+$current,}receipts"
        echo "Switched on in .env (COMPOSE_PROFILES=$(profiles))."
    fi
    if grep -q '^APP_TAG=' .env; then
        say "Downloading the receipt reader (about 3 GB)"
        docker compose pull ai-worker
    else
        say "Building the receipt reader (10 minutes or more the first time)"
        docker compose build ai-worker
    fi
    say "Starting it, and telling the app"
    # The backend and watcher are recreated to learn it's installed; the
    # watcher then reads any receipt photos waiting in the dropzone
    docker compose up -d --no-build
    say "Done"
    echo "The Receipts page takes photos now. It's in beta: check what it reads before you approve it."
    exit 0
fi

# off
if ! is_on && [ -z "$(docker compose --profile receipts ps -aq ai-worker 2>/dev/null)" ]; then
    echo "The receipt reader isn't installed; nothing to remove."
    exit 0
fi
image=$(docker compose --profile receipts config --images 2>/dev/null | grep -i 'ai.worker' | head -1 || true)
remaining=$(profiles | tr ',' '\n' | grep -vx receipts | paste -sd, - || true)
set_profiles "$remaining"
echo "Switched off in .env."
say "Removing the receipt reader"
docker compose --profile receipts rm -sf ai-worker
say "Telling the app"
docker compose up -d
if [ -n "$image" ]; then
    say "Freeing its disk space"
    docker image rm "$image" >/dev/null 2>&1 && echo "Removed its image ($image)." || echo "Its image ($image) is still in use or already gone."
fi
say "Done"
echo "Receipts already read stay in the app. To add the reader again: ./scripts/receipt-reader.sh on"
