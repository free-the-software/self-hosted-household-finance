# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
"""
The receipt reader: the ai-worker service, which reads the text of receipt
photos. It's an optional add-on (beta), installed with COMPOSE_PROFILES=receipts
in .env. Whether it's available is asked of the reader itself, so one that
is still starting or has stopped counts as not available.
"""
import os

import requests
from django.conf import settings
from django.core.cache import cache

CACHE_KEY = 'receipt-reader-available'
CHECK_EVERY_SECONDS = 30

HOW_TO_TURN_ON = (
    "The receipt reader isn't answering. If the app has only just started, wait a minute. Otherwise, "
    'on the computer the app runs on, start it with: docker compose up -d ai-worker '
    '(and see why it stopped with: docker compose logs ai-worker).'
)


NOT_INSTALLED = (
    "The receipt reader isn't installed. It's an optional add-on, in beta, that reads photos of receipts; "
    'it needs about 2.5 GB more disk and about 2 GB more memory while reading. To add it, run this '
    'on the computer the app runs on, in its folder: ./scripts/receipt-reader.sh on '
    '(Settings → Storage shows the exact command).'
)


def installed():
    return settings.RECEIPT_READER_INSTALLED


def how_to_turn_on():
    """What to do when the reader isn't available: install it, or start it."""
    return HOW_TO_TURN_ON if installed() else NOT_INSTALLED


def waiting():
    """How many receipt photos are in the dropzone, waiting to be read."""
    try:
        names = os.listdir(settings.DROPZONE_DIR)
    except OSError:
        return 0
    return sum(1 for n in names if n.lower().endswith(settings.RECEIPT_IMAGE_EXTENSIONS) and not n.startswith('.'))


def url(path=''):
    return f"{settings.RECEIPT_READER_URL.rstrip('/')}{path}"


def available():
    """Whether the reader is running and ready, checked at most every 30 seconds."""
    if not installed():
        return False
    known = cache.get(CACHE_KEY)
    if known is not None:
        return known
    try:
        response = requests.get(url('/health'), timeout=2)
        ready = response.status_code == 200 and response.json().get('status') == 'ready'
    except (requests.RequestException, ValueError):
        ready = False
    cache.set(CACHE_KEY, ready, CHECK_EVERY_SECONDS)
    return ready
