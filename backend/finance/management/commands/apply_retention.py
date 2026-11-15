# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
from django.core.management.base import BaseCommand

from finance import retention


class Command(BaseCommand):
    help = 'Deletes archived files and receipt images that have passed their retention period.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='List what would be deleted, delete nothing.')

    def handle(self, *args, **options):
        if options['dry_run']:
            summary, _targets = retention.build_plan()
            verb = 'Would delete'
        else:
            summary = retention.apply_policies(trigger='scheduled')
            verb = 'Deleted'

        for category in summary['categories']:
            period = f"{category['retention_days']} days" if category['retention_days'] else 'keep forever'
            self.stdout.write(f"  {category['label']} ({period}): {category['files']} files, {category['bytes']} bytes")
        self.stdout.write(f"{verb} {summary['files']} files, {summary['bytes']} bytes in total.")
        for error in summary.get('errors', []):
            self.stdout.write(self.style.ERROR(f"  Could not delete: {error}"))
