# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
"""
The cash-flow forecast for an account: the Accounts page's card and its
transaction list (the ledger) both come from project(), so they cannot
disagree about whether, or when, the balance goes below zero.

The forecast starts from the last balance the bank reported, then adds, in
date order: every pending transaction, and every cleared one that balance
doesn't include yet (finance/balances.py).

A shortfall is reported when the balance is below zero today, or at any
transaction still to come: anything dated after today, or still pending.
Dips that happened in the past and have since cleared are history, not a
forecast, and are left out.
"""
from datetime import date
from decimal import Decimal

from django.db.models import Q

from .balances import not_included_q
from .models import Transaction


def project(account, today=None):
    """
    Returns {'start_balance', 'start_date', 'rows', 'current_balance',
    'lowest_balance', 'deficit_date'}. rows are (transaction, running
    balance) in the order the balance is worked out.
    """
    today = today or date.today()
    start_balance = account.last_balance or Decimal('0.00')
    start_date = account.balance_date or date(2000, 1, 1)

    transactions = Transaction.objects.filter(account=account).filter(
        Q(status='P') | not_included_q(start_date)
    ).order_by('dtposted', 'id')

    rows = []
    running = start_balance
    current = start_balance
    lowest = None
    deficit_date = None
    for tx in transactions:
        running += tx.trnamt
        rows.append((tx, running))
        if tx.dtposted <= today:
            current = running
        # Still to come: a future date, or not yet cleared by the bank
        if tx.dtposted > today or tx.status == 'P':
            lowest = running if lowest is None else min(lowest, running)
            if running < 0 and deficit_date is None:
                deficit_date = max(tx.dtposted, today)

    # Today's balance counts too: already below zero means a shortfall now
    lowest = current if lowest is None else min(lowest, current)
    if current < 0:
        deficit_date = today

    return {
        'start_balance': start_balance,
        'start_date': start_date,
        'rows': rows,
        'current_balance': current,
        'lowest_balance': lowest,
        'deficit_date': deficit_date,
    }
