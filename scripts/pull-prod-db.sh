#!/usr/bin/env bash
# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
# ----------------------------------------------------------
# A developer tool: copy the live (production) database into your
# local dev copy, to try changes against real data. Run from the
# repo root on your development machine:
#
#   ./scripts/pull-prod-db.sh user@server
#
# Read-only on the server (pg_dump). REPLACES the local dev
# database. Real financial data ends up on this machine: keep it
# off shared or untrusted computers, and out of git and issues.
#
# The server's database container is found by name: finance_db,
# or <prefix>_db if CONTAINER_PREFIX is set there. Override with
#   PROD_DB_CONTAINER=name ./scripts/pull-prod-db.sh user@server
# Uses the POSTGRES_USER / POSTGRES_DB already set inside each
# database container, so no .env files are parsed.
# ----------------------------------------------------------
set -euo pipefail

SERVER="${1:?usage: $0 user@server}"
CONTAINER="${PROD_DB_CONTAINER:-finance_db}"

echo "This replaces your LOCAL dev database with a copy of the one on $SERVER."
read -r -p "Continue? [y/N] " answer
[[ "$answer" =~ ^[Yy] ]] || { echo "Nothing was changed."; exit 0; }

DUMP="$(mktemp --suffix=.dump)"
trap 'rm -f "$DUMP"' EXIT
chmod 600 "$DUMP"

echo "==> Dumping the database from $SERVER ($CONTAINER) ..."
ssh "$SERVER" \
  "docker exec $CONTAINER sh -c 'pg_dump -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Fc'" \
  > "$DUMP"
echo "    $(du -h "$DUMP" | cut -f1) downloaded"

echo "==> Restoring into the local dev database ..."
docker compose up -d finance_db
until docker compose exec -T finance_db sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; do
  sleep 1
done

docker compose exec -T finance_db sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-privileges' \
  < "$DUMP"

echo "==> Restarting the app containers ..."
docker compose restart backend watcher scheduler
echo "Done. Your dev copy now has the server's data."
