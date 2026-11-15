# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import os

from django.core.management.base import BaseCommand, CommandError

from finance import backups


class Command(BaseCommand):
    help = 'Restores the database (and, from a full backup, the receipt images) from a backup file. Replaces current data.'

    def add_arguments(self, parser):
        parser.add_argument('backup', help='Path to the backup file (.tar or .tar.age)')
        parser.add_argument('--identity', help='File holding your private key (the AGE-SECRET-KEY-... line), for encrypted backups')
        parser.add_argument('--database-only', action='store_true', help='From a full backup, restore the database but not the images')
        parser.add_argument('--yes', action='store_true', help='Do not ask for confirmation')

    def handle(self, *args, **options):
        path = options['backup']
        if not os.path.isfile(path):
            raise CommandError(f'No such file: {path}')

        if not options['yes']:
            self.stdout.write(self.style.WARNING(
                'This REPLACES everything in the database with the contents of the backup.\n'
                'Anything added since the backup was made will be lost.'
            ))
            if input("Type 'restore' to continue: ").strip() != 'restore':
                raise CommandError('Cancelled. Nothing was changed.')

        try:
            result = backups.restore(path, identity_path=options['identity'], database_only=options['database_only'])
        except backups.BackupError as exc:
            raise CommandError(str(exc))

        manifest = result['manifest']
        self.stdout.write(self.style.SUCCESS(
            f"Restored the {manifest['kind']} backup made {manifest['created_at']}: "
            f"database restored, {result['images_restored']} receipt images restored."
        ))
        self.stdout.write('Restart the app containers so every process sees the restored data.')
