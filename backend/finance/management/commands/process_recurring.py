# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import logging
import hashlib
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from dateutil.relativedelta import relativedelta
from finance.models import RecurringTransaction, Transaction

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Processes active recurring transactions and creates entries in the register if due.'

    def handle(self, *args, **options):
        # Ensure we are strictly comparing Dates, not DateTimes
        today = timezone.now().date()
        rules = RecurringTransaction.objects.filter(is_active=True)

        self.stdout.write(f"--- Starting Recurring Check for {today} ---")
        count = 0

        for rule in rules:
            # Protect against DateTime fields from the DB
            current_due_date = rule.start_date.date() if hasattr(rule.start_date, 'date') else rule.start_date
            
            # A while loop ensures that if a rule is 3 months behind, 
            # it will generate all missing transactions right now to catch up.
            while True:
                trigger_date = current_due_date - timedelta(days=rule.create_days_before)

                if today >= trigger_date:
                    self.stdout.write(f"⚙️ Triggering: {rule.name} (Due: {current_due_date}, Triggered on: {trigger_date})")

                    # --- A. Determine Amount ---
                    final_amount = -abs(rule.amount)
                    if rule.subcategory and rule.subcategory.category.type == 'INC':
                        final_amount = abs(rule.amount)

                    # --- B. Generate a deterministic Synthetic FITID ---
                    hash_string = f"recurring_{rule.id}_{current_due_date}".encode('utf-8')
                    synthetic_fitid = hashlib.md5(hash_string).hexdigest()

                    # --- C. Create the Transaction ---
                    try:
                        Transaction.objects.create(
                            account=rule.account,
                            subcategory=rule.subcategory,
                            name=rule.name,
                            trnamt=final_amount,
                            dtposted=current_due_date,
                            status='P', 
                            is_manual=False,
                            fitid=synthetic_fitid,  # <--- This is the magic key that was missing!
                        )
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"❌ CRASH saving transaction {rule.name}: {e}"))
                        break # Break the while loop so we don't infinitely crash

                    # --- D. Calculate the FUTURE Date ---
                    next_occurrence = self.calculate_next_date(current_due_date, rule.frequency)
                    
                    # Safety Check: Prevent infinite loops
                    if next_occurrence <= current_due_date:
                        self.stdout.write(self.style.ERROR(f"❌ ERROR: Next occurrence logic failed for {rule.name}. Aborting rule."))
                        break

                    # --- E. Update the Rule ---
                    rule.start_date = next_occurrence
                    rule.save()
                    
                    current_due_date = next_occurrence # Update for the next iteration of the while loop
                    count += 1
                    self.stdout.write(self.style.SUCCESS(f"  ✅ Created. Next due date pushed to: {next_occurrence}"))
                else:
                    # If today is NOT >= trigger_date, we are fully caught up. Break the loop.
                    self.stdout.write(f"⏭️ Skipping {rule.name}: Not due yet. (Triggers on {trigger_date})")
                    break

        self.stdout.write(self.style.SUCCESS(f'--- Finished. Successfully processed {count} recurring occurrences. ---'))

    def calculate_next_date(self, current_date, frequency):
        """Calculates the next due date based on frequency."""
        if frequency == 'WEEKLY':
            return current_date + timedelta(weeks=1)
        elif frequency == 'BIWEEKLY':
            return current_date + timedelta(weeks=2)
        elif frequency == 'SEMI_MONTHLY':
            # Standardizes on the 1st and 15th of the month to prevent length-of-month drift
            if current_date.day < 15:
                return current_date.replace(day=15)
            else:
                next_month = current_date + relativedelta(months=1)
                return next_month.replace(day=1)
        elif frequency == 'MONTHLY':
            return current_date + relativedelta(months=1)
        elif frequency == 'SEMI_ANNUALLY':
            return current_date + relativedelta(months=6)
        elif frequency == 'ANNUALLY':
            return current_date + relativedelta(years=1)
        
        return current_date