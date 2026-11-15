# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
from django.contrib import admin
from django.urls import path, re_path, include
from django.conf import settings

from finance.auth import protected_media

urlpatterns = [
    path('api/', include('finance.urls')),
    # Receipt images are financial data, so they go through a login check
    # instead of Django's open static() helper.
    re_path(r'^media/(?P<path>.*)$', protected_media, name='protected-media'),
]

if settings.OIDC_ENABLED:
    urlpatterns.append(path('oidc/', include('mozilla_django_oidc.urls')))

# Off once OIDC is configured, unless ENABLE_ADMIN=True (see settings.py)
if settings.ADMIN_ENABLED:
    urlpatterns.append(path('admin/', admin.site.urls))
