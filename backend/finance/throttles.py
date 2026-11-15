# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
"""
Rate limits: how often one visitor or one signed-in user may call the server.

They are a backstop, set well above what the app's own pages need, so a
person using the app never meets them. What they stop is a script hammering
the server: flooding uploads, starting backup after backup, or opening
endless sign-in attempts.

The limits themselves are in settings.API_RATE_LIMITS; a limit of None is off.
"""
from django.conf import settings
from django.http import JsonResponse
from rest_framework.throttling import SimpleRateThrottle


class _Limit(SimpleRateThrottle):
    """Reads its limit from settings each time, so it can be changed without a code change."""
    per = 'user'  # 'user': each signed-in user; 'address': each network address

    def get_rate(self):
        return settings.API_RATE_LIMITS.get(self.scope)

    def get_cache_key(self, request, view):
        user = getattr(request, 'user', None)
        signed_in = bool(user and user.is_authenticated)
        if self.per == 'address':
            # Signed-in users are counted by their own limit instead
            if signed_in and self.scope == 'anon':
                return None
            who = self.get_ident(request)
        else:
            if not signed_in:
                return None
            who = user.pk
        return self.cache_format % {'scope': self.scope, 'ident': who}


class VisitorLimit(_Limit):
    """Everything asked for without being signed in, per network address."""
    scope = 'anon'
    per = 'address'


class UserLimit(_Limit):
    """Everything a signed-in user asks for."""
    scope = 'user'


class UploadLimit(_Limit):
    """Files sent to the dropzone."""
    scope = 'uploads'


class HeavyLimit(_Limit):
    """Work that is expensive for the server, such as making a backup."""
    scope = 'heavy'


class SignInLimit(_Limit):
    """Starting or finishing a sign-in, per network address."""
    scope = 'sign_in'
    per = 'address'


# Also changing your own password, which checks the current one
SIGN_IN_PATHS = ('/oidc/', '/admin/login/', '/api/sign-in/', '/api/account/password/')


class SignInRateLimit:
    """
    Middleware. The sign-in pages are not part of the API, so the limits above
    do not reach them, yet each attempt costs a database write and a call to
    the identity provider.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(SIGN_IN_PATHS):
            limit = SignInLimit()
            if not limit.allow_request(request, None):
                wait = limit.wait()
                response = JsonResponse({'detail': 'Too many sign-in attempts. Wait a moment and try again.'}, status=429)
                if wait is not None:
                    response['Retry-After'] = str(int(wait) + 1)
                return response
        return self.get_response(request)
