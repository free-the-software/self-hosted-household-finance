# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
"""
Settings for running the test suite without a PostgreSQL server:

    python manage.py test finance --settings=config.settings_test

Everything is the same as the real settings except the database, which is a
throwaway SQLite one, and the rate limits, which are off. The backup tests stand in for pg_dump and pg_restore,
so nothing here needs PostgreSQL.
"""
import os

os.environ.setdefault('SECRET_KEY', 'test-only-not-a-real-key')

from .settings import *  # noqa: E402,F401,F403

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}

# Tests make requests far faster than a person does. Rate limits are off
# unless a test turns them on, and their counts stay in memory.
API_RATE_LIMITS = {name: None for name in API_RATE_LIMITS}  # noqa: F405
# Tests build their own categories; the starter set is tested on its own
STARTER_CATEGORIES = False
# Tests run faster than a person; the idle limit is tested on its own
SESSION_IDLE_MINUTES = 0
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
# Tests have the receipt reader installed unless a test says otherwise
RECEIPT_READER_INSTALLED = True
# Tests never ask GitHub for releases unless a test sets a repository
UPDATE_CHECK_REPO = ''
