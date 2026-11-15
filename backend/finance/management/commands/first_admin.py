# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
"""
The first login for a new install: an admin with a random one-time password,
printed once, which must be changed at the first sign-in.

    python manage.py first_admin              # username "admin"
    python manage.py first_admin --username sam

Does nothing if any user exists, so it is safe to run again (setup.sh runs
it every time).
"""
import secrets

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from finance.models import require_password_change


def temporary_password():
    """Easy to type from the screen: four groups of five letters and digits, no look-alikes."""
    letters = 'abcdefghjkmnpqrstuvwxyz23456789'
    return '-'.join(''.join(secrets.choice(letters) for _ in range(5)) for _ in range(4))


class Command(BaseCommand):
    help = 'Creates the first admin with a one-time password (only when there are no users yet).'

    def add_arguments(self, parser):
        parser.add_argument('--username', default='admin')

    def handle(self, *args, username, **options):
        users = get_user_model().objects
        if users.exists():
            self.stdout.write('A login already exists; nothing to do.')
            return
        password = temporary_password()
        user = users.create_superuser(username=username, email='', password=password)
        require_password_change(user)
        self.stdout.write(self.style.SUCCESS('Your first login (you will choose your own password when you sign in):'))
        self.stdout.write(f'  Username: {username}')
        self.stdout.write(f'  Password: {password}')
