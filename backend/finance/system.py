# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
"""
The server itself: free disk space, and whether a newer release is out.

Disk: a warning shows on every page when the disk holding the app's data
(DISK_CHECK_PATH, /data in the containers) has less than DISK_WARN_GB free,
long before an update, a backup or an import fails for lack of space.

Settings an admin can change (SystemSettings: update checks, the disk
warning, idle sign-out, upload size) take effect at once; each falls back
to its default from .env or the app. They are remembered for a minute
(every request checks the idle limit) and forgotten as soon as they're
saved.

Updates: once a day the latest release of UPDATE_CHECK_REPO is fetched from
GitHub and compared with APP_VERSION. Nothing is ever installed from here:
the app only says a release is out and shows the command to run on the
server (scripts/update.sh). The app has no control over Docker, and doesn't
need any.
"""
import re
import shutil

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError

GB = 1024 ** 3
SETTINGS_CACHE_KEY = 'system-settings'
SETTINGS_CACHE_SECONDS = 60
# What each can be set to in Settings
LIMITS = {'disk_warn_gb': (0, 1000), 'idle_minutes': (0, 24 * 60), 'upload_max_mb': (1, 100)}


def defaults():
    """Each setting's value when it isn't set in the app."""
    return {
        'update_checks': True,
        'disk_warn_gb': settings.DISK_WARN_GB,
        'idle_minutes': settings.SESSION_IDLE_MINUTES,
        'upload_max_mb': settings.UPLOAD_MAX_MB,
    }


def _saved():
    """The values set in the app (None where not set), remembered for a minute."""
    saved = cache.get(SETTINGS_CACHE_KEY)
    if saved is None:
        from .models import SystemSettings
        try:
            row = SystemSettings.objects.filter(pk=1).values(*defaults()).first() or {}
        except DatabaseError:
            # The table isn't there yet (new code, database not updated): the
            # defaults, rather than every request failing; not remembered
            return {key: None for key in defaults()}
        saved = {key: row.get(key) for key in defaults()}
        cache.set(SETTINGS_CACHE_KEY, saved, SETTINGS_CACHE_SECONDS)
    return saved


def current():
    """Each setting as it applies now: set in the app, or its default."""
    return {key: value if value is not None else defaults()[key] for key, value in _saved().items()}


def save(values, user=None):
    """Sets the given settings (None restores a default) and applies them at once."""
    from .models import SystemSettings
    row = SystemSettings.load()
    for key, value in values.items():
        setattr(row, key, value)
    row.updated_by = user
    row.save()
    cache.delete(SETTINGS_CACHE_KEY)


def forget_settings():
    """For tests: drop what's remembered."""
    cache.delete(SETTINGS_CACHE_KEY)
UPDATE_CACHE_KEY = 'latest-release'
CHECK_EVERY_SECONDS = 24 * 60 * 60
RETRY_AFTER_SECONDS = 60 * 60            # after GitHub couldn't be reached


def disk_status():
    try:
        usage = shutil.disk_usage(settings.DISK_CHECK_PATH)
    except OSError:
        return {'known': False, 'low': False}
    free_gb = usage.free / GB
    low = free_gb < current()['disk_warn_gb']
    return {
        'known': True,
        'free_gb': round(free_gb, 1),
        'total_gb': round(usage.total / GB, 1),
        'low': low,
        'message': (f'The server has {free_gb:.1f} GB of disk space left. Updates, backups and imports need room: '
                    'free some up on the server.') if low else '',
    }


def version_tuple(text):
    """'v1.4.0', '1.4.0-3-gabc123' -> (1, 4, 0); None if there's no version in it."""
    found = re.match(r'v?(\d+)\.(\d+)(?:\.(\d+))?', (text or '').strip())
    return tuple(int(part or 0) for part in found.groups()) if found else None


def latest_release():
    """The newest release on GitHub, remembered for a day; None if unknown or switched off."""
    if not settings.UPDATE_CHECK_REPO or not current()['update_checks']:
        return None
    known = cache.get(UPDATE_CACHE_KEY)
    if known is not None:
        return known or None
    try:
        response = requests.get(f'https://api.github.com/repos/{settings.UPDATE_CHECK_REPO}/releases/latest',
                                headers={'Accept': 'application/vnd.github+json'}, timeout=5)
        response.raise_for_status()
        data = response.json()
        release = {'version': (data.get('tag_name') or '').lstrip('v'), 'url': data.get('html_url', ''),
                   'notes': (data.get('body') or '')[:2000], 'published': data.get('published_at')}
        cache.set(UPDATE_CACHE_KEY, release, CHECK_EVERY_SECONDS)
        return release
    except (requests.RequestException, ValueError):
        cache.set(UPDATE_CACHE_KEY, {}, RETRY_AFTER_SECONDS)
        return None


def host_command(script):
    """A command to paste on the server: one of the app's scripts, run from its folder."""
    folder = settings.INSTALL_DIR
    return f'cd {folder} && ./scripts/{script}' if folder else f'./scripts/{script}'


def update_command():
    return host_command('update.sh')


def update_status():
    running = settings.APP_VERSION
    status = {'enabled': bool(settings.UPDATE_CHECK_REPO) and current()['update_checks'],
              'repo': settings.UPDATE_CHECK_REPO, 'current': running, 'command': update_command(),
              'in_folder': bool(settings.INSTALL_DIR)}
    release = latest_release()
    if release:
        mine, theirs = version_tuple(running), version_tuple(release['version'])
        status.update(latest=release['version'], url=release['url'], notes=release['notes'],
                      newer=bool(theirs and (mine is None or theirs > mine)))
    return status
