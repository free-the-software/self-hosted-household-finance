# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import os

from django.core.wsgi import get_wsgi_application

# Same settings module as manage.py. gunicorn loads this file directly,
# so nothing else sets it first.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_wsgi_application()
