#!/bin/sh
# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.

# 1. Drop root.
#    The container starts as root only so it can hand the mounted /data
#    folder to the unprivileged "app" user (earlier versions ran as root and
#    left root-owned files there). Everything after this block, including
#    migrations and the server itself, runs as "app".
if [ "$(id -u)" = "0" ]; then
    if [ -d /data ]; then
        mkdir -p /data/media/receipts /data/dropzone
        find /data ! -user app -exec chown app:app {} +
    fi
    export HOME=/home/app
    exec setpriv --reuid=app --regid=app --init-groups "$0" "$@"
    # exec only returns if it failed; never carry on as root
    echo "Could not drop root privileges" >&2
    exit 1
fi

# 2. Standard Django Boot Steps
echo "Running Migrations..."
python manage.py migrate

echo "Starting Server..."
exec "$@"
