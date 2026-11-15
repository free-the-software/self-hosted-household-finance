# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import logging
from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management import call_command
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from django_apscheduler.jobstores import DjangoJobStore
from django_apscheduler.models import DjangoJobExecution
from django_apscheduler import util

logger = logging.getLogger(__name__)

# --- THE FIX IS HERE ---
# You must add this decorator to ANY job that interacts with the DB.
# It ensures stale connections are closed/reset before the logic runs.
@util.close_old_connections
def process_recurring_bills():
    """This function checks specifically for bills."""
    print("⏰ Scheduler: Checking for recurring transactions...")
    call_command('process_recurring')

@util.close_old_connections
def apply_retention_policies():
    """Deletes files past the retention periods set in Settings (none by default)."""
    print("⏰ Scheduler: Applying storage retention policies...")
    call_command('apply_retention')

@util.close_old_connections
def run_due_backups():
    """Makes any backup whose scheduled time has come (see Settings > Backups)."""
    call_command('create_backup', '--due')

@util.close_old_connections
def delete_old_job_executions(max_age=604_800):
    """Deletes execution logs older than 7 days."""
    DjangoJobExecution.objects.delete_old_job_executions(max_age)

class Command(BaseCommand):
    help = "Runs APScheduler."

    def handle(self, *args, **options):
        scheduler = BlockingScheduler(timezone=settings.TIME_ZONE)
        scheduler.add_jobstore(DjangoJobStore(), "default")

        # --- JOB 1: RUN TRANSACTIONS DAILY @ 6:00 AM ---
        scheduler.add_job(
            process_recurring_bills,
            trigger=CronTrigger(hour=6, minute=0), 
            id="process_recurring_bills", 
            max_instances=1,
            replace_existing=True,
        )
        print("✅ Added job: process_recurring_bills (Daily @ 6am)")

        # --- JOB 2: STORAGE RETENTION DAILY @ 3:30 AM ---
        scheduler.add_job(
            apply_retention_policies,
            trigger=CronTrigger(hour=3, minute=30),
            id="apply_retention_policies",
            max_instances=1,
            replace_existing=True,
        )
        print("✅ Added job: apply_retention_policies (Daily @ 3:30am)")

        # --- JOB 3: BACKUPS, CHECKED HOURLY ---
        # The schedule itself lives in Settings; this only asks "is one due?"
        scheduler.add_job(
            run_due_backups,
            trigger=CronTrigger(minute=5),
            id="run_due_backups",
            max_instances=1,
            replace_existing=True,
        )
        print("✅ Added job: run_due_backups (Hourly @ :05)")

        # --- JOB 4: CLEANUP LOGS WEEKLY ---
        scheduler.add_job(
            delete_old_job_executions,
            trigger=CronTrigger(day_of_week="mon", hour=0, minute=0),
            id="delete_old_job_executions",
            max_instances=1,
            replace_existing=True,
        )

        try:
            print("🚀 Starting Scheduler...")
            scheduler.start()
        except KeyboardInterrupt:
            scheduler.shutdown()
            print("Scheduler shut down successfully!")