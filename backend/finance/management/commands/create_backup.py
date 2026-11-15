# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
from django.core.management.base import BaseCommand, CommandError

from finance import backups
from finance.models import Backup


class Command(BaseCommand):
    help = 'Makes a backup now (db or full), or with --due runs whichever scheduled backups have come due.'

    def add_arguments(self, parser):
        parser.add_argument('kind', nargs='?', choices=list(backups.KINDS), help='db = database only, full = database + receipt images')
        parser.add_argument('--due', action='store_true', help='Run the scheduled backups that are due (used by the scheduler).')

    def handle(self, *args, **options):
        if options['due']:
            records = backups.run_due_backups()
        elif options['kind']:
            records = [backups.create_backup(options['kind'], trigger='manual')]
        else:
            raise CommandError('Say which backup to make: db or full (or use --due).')

        if not records:
            self.stdout.write('No backup was due.')
        failed = False
        for record in records:
            if record.status == Backup.OK:
                self.stdout.write(self.style.SUCCESS(f'{record.get_kind_display()}: {backups.path_for(record)} ({record.size} bytes)'))
            else:
                failed = True
                self.stdout.write(self.style.ERROR(f'{record.get_kind_display()} FAILED: {record.error}'))
        if failed and not options['due']:
            raise CommandError('The backup failed.')
