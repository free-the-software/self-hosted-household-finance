# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
"""
Signing out after a period without activity (SESSION_IDLE_MINUTES).

The page watches for activity and signs the user out itself, with a warning
first. This middleware enforces the same limit on the server, so it holds
when the tab was closed or the computer slept: a session whose last activity
is older than the limit is ended on its next request.

Requests the page makes on its own, such as a progress refresh while a
backup runs, carry "X-Activity: background" and do not count as activity.
"""
import time

from django.conf import settings
from django.contrib.auth import logout
from django.http import JsonResponse

SESSION_KEY = 'last_activity'
# Recording every request would write the session on every call; once in
# this many seconds is plenty against a limit measured in minutes
RECORD_EVERY = 30


def idle_limit_seconds():
    """The limit set in Settings, or SESSION_IDLE_MINUTES (finance/system.py)."""
    from .system import current
    return max(int(current()['idle_minutes'] or 0), 0) * 60


class IdleTimeout:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        limit = idle_limit_seconds()
        user = getattr(request, 'user', None)
        if limit and user is not None and user.is_authenticated:
            now = time.time()
            last = request.session.get(SESSION_KEY)
            if last is not None and now - last > limit:
                logout(request)
                # The session check and sign-out themselves carry on, signed out
                if request.path.startswith('/api/') and request.path not in ('/api/me/', '/api/logout/'):
                    minutes = limit // 60
                    return JsonResponse({'detail': f'Signed out after {minutes} minutes without activity.'}, status=401)
            elif request.headers.get('X-Activity') != 'background' and (last is None or now - last >= RECORD_EVERY):
                request.session[SESSION_KEY] = now
        return self.get_response(request)
