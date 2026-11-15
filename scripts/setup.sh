#!/usr/bin/env bash
# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
# ==========================================================
# First-time setup for a new install.
#
#   ./scripts/setup.sh
#
# Writes .env with a random secret key and database password (unless one
# already exists), creates the data folders, builds and starts the app,
# and creates your first login ("admin", with a one-time password). Safe
# to run again: an existing .env, data and logins are kept.
#
# If the repository has releases, it offers the latest one (the default):
# ready-made images are downloaded instead of built, which takes minutes.
# ==========================================================
set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# A long random string, from whatever this machine has
random_secret() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32
    else
        head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
    fi
}

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    echo "Docker with the Compose plugin is needed: https://docs.docker.com/engine/install/" >&2
    exit 1
fi

if [ -f .env ]; then
    say "Keeping your existing .env"
    # Read the address back for the summary at the end
    APP_PORT=$(grep -E '^APP_PORT=' .env | cut -d= -f2- || true)
    SITE_URL=$(grep -E '^SITE_URL=' .env | cut -d= -f2- || true)
else
    say "Setting up .env"
    read -rp "Port for the app [8080]: " APP_PORT
    APP_PORT=${APP_PORT:-8080}
    echo "Who should be able to open it?"
    echo "  1) Only this computer (safest; default)"
    echo "  2) Other devices on your network"
    read -rp "Choose 1 or 2 [1]: " REACH
    if [ "${REACH:-1}" = "2" ]; then
        read -rp "Address other devices will use (this machine's IP or name, e.g. 192.168.1.20): " HOST
        if [ -z "$HOST" ]; then echo "An address is needed for option 2." >&2; exit 1; fi
        APP_BIND=0.0.0.0
    else
        HOST=localhost
        APP_BIND=127.0.0.1
    fi
    SITE_URL="http://${HOST}:${APP_PORT}"

    echo
    echo "Optional add-on: the receipt reader (BETA)."
    echo "  It reads photos of receipts (store, date, total, tax) and suggests the"
    echo "  transaction each belongs to. It often misreads receipts: what it reads is"
    echo "  only a suggestion, and nothing changes until you approve it."
    echo "  It needs about 2.5 GB more disk, about 2 GB more memory while reading,"
    echo "  and makes the first build 10 minutes or more. It runs on this computer;"
    echo "  receipts are never sent anywhere. Add or remove it later with"
    echo "  ./scripts/receipt-reader.sh on (or off)."
    read -rp "Add the receipt reader? [y/N]: " READER
    case "${READER:-n}" in
        [Yy]*) PROFILES_LINE="COMPOSE_PROFILES=receipts" ;;
        *) PROFILES_LINE="#COMPOSE_PROFILES=receipts" ;;
    esac

    # The latest release, if the repository has any (not test releases like 1.2.0-rc1)
    releases=$(git tag -l 'v[0-9]*.[0-9]*.[0-9]*' 2>/dev/null || true)
    LATEST=$(grep -v -- - <<<"$releases" | sort -V | tail -1 || true)
    RELEASE_LINES="# Built from the code in this folder (no APP_TAG)."
    if [ -n "$LATEST" ]; then
        echo
        echo "Which version?"
        echo "  1) The latest release, ${LATEST#v}: ready-made images, installs in minutes (default)"
        echo "  2) Build it from the code in this folder (for working on the app)"
        read -rp "Choose 1 or 2 [1]: " SOURCE
        if [ "${SOURCE:-1}" != "2" ]; then
            git -c advice.detachedHead=false checkout --quiet "$LATEST" || { echo "Couldn't switch to release ${LATEST#v}." >&2; exit 1; }
            RELEASE_LINES="COMPOSE_FILE=docker-compose.yaml:docker-compose.release.yml
APP_TAG=${LATEST#v}"
        fi
    fi

    # .env holds secrets: readable by you only
    ( umask 077; cat > .env ) <<EOF
# Written by scripts/setup.sh. See .env.example for every setting.

# --- Where the app is reached ---
DOMAIN_NAME=${HOST}
SITE_URL=${SITE_URL}
APP_BIND=${APP_BIND}
APP_PORT=${APP_PORT}

# --- Database (the bundled Postgres container) ---
DB_NAME=finance
DB_USER=finance
DB_PASSWORD=$(random_secret)
DB_HOST=finance_db
DB_PORT=5432

# --- Django ---
SECRET_KEY=$(random_secret)
DEBUG=False

# --- Receipt reader (optional add-on, beta) ---
# "receipts" installs and runs it; comment it out to go without
${PROFILES_LINE}

# --- Version ---
# A release install runs ready-made images of release APP_TAG, which
# scripts/update.sh moves to the newest release (behind Traefik, add
# :docker-compose.traefik.yml to COMPOSE_FILE)
${RELEASE_LINES}

# --- Updates ---
# Where the app is installed, so Settings shows the exact update command
INSTALL_DIR=$(pwd)
EOF
    echo "Wrote .env (readable by you only)."
fi

mkdir -p data/dropzone data/backups

services=$(docker compose config --services 2>/dev/null || true)
if grep -q '^APP_TAG=' .env; then
    say "Downloading and starting the app"
    if grep -qx ai-worker <<<"$services"; then
        echo "With the receipt reader, this downloads about 3 GB."
    fi
    docker compose pull --quiet
    docker compose up -d --no-build
else
    say "Building and starting the app"
    if grep -qx ai-worker <<<"$services"; then
        echo "With the receipt reader, the first build takes 10 minutes or more (it downloads about 2 GB)."
    else
        echo "The first build takes a few minutes."
    fi
    # The release being built, if this is one (shown in Settings)
    export APP_VERSION="$(git describe --tags --exact-match 2>/dev/null | sed 's/^v//' || true)"
    docker compose up -d --build
fi

say "Waiting for the app to be ready"
# Ready once the backend has finished setting up the database (it migrates
# as it starts); creating the login any sooner can race it
for _ in $(seq 1 90); do
    if docker compose exec -T backend python manage.py migrate --check >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 2
done
if [ -z "${ready:-}" ]; then
    echo "The backend did not start. See: docker compose logs backend" >&2
    exit 1
fi

# The first admin, with a one-time password chosen at random and shown once;
# you choose your own at the first sign-in. Nothing happens if a login exists.
say "Your login"
docker compose exec -T backend python manage.py first_admin

say "Done. Open ${SITE_URL:-http://localhost:${APP_PORT:-8080}}"
echo "Next: Settings → Accounts to add your accounts, then drop a bank file (.qfx, .ofx or .csv) in the Import Inbox."
