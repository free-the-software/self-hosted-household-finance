# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
"""
A forgotten password: gives the user a random one-time password, printed
once, which they must change when they sign in. Their other sessions end.

    python manage.py reset_password <username>
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from finance.models import require_password_change

from .first_admin import temporary_password


class Command(BaseCommand):
    help = 'Gives a user a one-time password they must change at their next sign-in.'

    def add_arguments(self, parser):
        parser.add_argument('username')

    def handle(self, *args, username, **options):
        try:
            user = get_user_model().objects.get(username=username)
        except get_user_model().DoesNotExist:
            names = ', '.join(get_user_model().objects.order_by('username').values_list('username', flat=True)) or 'none'
            raise CommandError(f'No user called "{username}". Users: {names}.')
        password = temporary_password()
        user.set_password(password)
        user.save(update_fields=['password'])
        require_password_change(user)
        self.stdout.write(self.style.SUCCESS(f'One-time password for {username} (to be changed at sign-in):'))
        self.stdout.write(f'  {password}')
