#!/usr/bin/env bash
# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
# ==========================================================
# A second development stack alongside your usual one, from the same code,
# with its own empty database: for trying a new install (the sign-in, the
# starter categories, the tour) without touching your dev data.
#
#   ./scripts/dev-copy.sh fresh          # writes .env.fresh, once
#   docker compose --env-file .env.fresh up -d --build
#   docker compose --env-file .env.fresh exec backend python manage.py first_admin
#   -> http://localhost:5174
#
# Every docker compose command for it needs --env-file .env.fresh. Start
# over with an empty database again:
#   docker compose --env-file .env.fresh down -v
#
# It copies your .env (so it runs the same way: COMPOSE_FILE and so on) and
# changes what must differ: its own project, container names, ports, data
# folder and database password. Single sign-on is left out, as on a new
# install. Both stacks can run at once.
# ==========================================================
set -euo pipefail
cd "$(dirname "$0")/.."

NAME="${1:?usage: $0 <name>   e.g. $0 fresh}"
case "$NAME" in *[!a-z0-9-]*|'') echo "Use lowercase letters, digits and dashes for the name." >&2; exit 1 ;; esac
TARGET=".env.$NAME"
DEV_PORT=5174

[ -f .env ] || { echo "No .env here to copy. Run ./scripts/setup.sh first." >&2; exit 1; }
if [ -f "$TARGET" ]; then
    echo "$TARGET already exists; using it as it is."
else
    random_secret() { openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
    OVERRIDDEN='COMPOSE_PROJECT_NAME|ENV_FILE|CONTAINER_PREFIX|DATA_DIR|BACKUP_DIR_HOST|DB_HOST_PORT|DEV_APP_PORT|DEV_BACKEND_PORT|APP_PORT|SITE_URL|DB_PASSWORD|SECRET_KEY|AUTH_DOMAIN|OIDC_APP_SLUG|OIDC_CLIENT_ID|OIDC_CLIENT_SECRET|OIDC_JWKS_URL|SESSION_COOKIE_NAME'
    ( umask 077
      grep -Ev "^($OVERRIDDEN)=" .env
      cat <<EOF

# --- Written by scripts/dev-copy.sh: a second stack alongside the first ---
COMPOSE_PROJECT_NAME=finance-$NAME
ENV_FILE=$TARGET
CONTAINER_PREFIX=finance-$NAME
DATA_DIR=./data-$NAME
DB_HOST_PORT=5440
DEV_APP_PORT=$DEV_PORT
DEV_BACKEND_PORT=8010
APP_PORT=8090
SITE_URL=http://localhost:$DEV_PORT
DB_PASSWORD=$(random_secret)
SECRET_KEY=$(random_secret)
# Its own sign-in cookie: browsers share cookies across ports
SESSION_COOKIE_NAME=sessionid-$NAME
# No single sign-on, like a new install: the app's own sign-in page
AUTH_DOMAIN=
OIDC_CLIENT_ID=
OIDC_CLIENT_SECRET=
EOF
    ) > "$TARGET"
    echo "Wrote $TARGET (readable by you only)."
fi
mkdir -p "data-$NAME/dropzone" "data-$NAME/backups"

cat <<EOF

Start it:     docker compose --env-file $TARGET up -d --build
First login (once 'docker compose --env-file $TARGET logs backend' shows it has migrated):
              docker compose --env-file $TARGET exec backend python manage.py first_admin
Open:         http://localhost:$DEV_PORT
Start over:   docker compose --env-file $TARGET down -v
EOF
