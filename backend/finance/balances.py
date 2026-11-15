# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
"""
Which transactions a bank's balance already includes.

An account keeps the last balance its bank reported (last_balance, as of
balance_date). Whether a transaction is already in that figure can't be
told from its date alone: a card purchase dated the 2nd may not post until
the 4th, after a balance dated the 3rd. So each transaction records the
balance that first included it (in_balance_as_of):

- a row approved from a bank file that set the balance: that balance,
  whatever the row's date, since the bank counted everything in its file;
- when a balance is set (an import, a loan statement), every cleared
  transaction dated up to it that no balance included yet;
- anything else cleared and dated up to the current balance, when it is
  saved (Transaction.save);
- never a pending one: it hasn't been through the bank.

An account's balance on a day is then its bank balance, less the included
transactions dated after that day, plus the cleared ones not included and
dated up to it. Pending and future-dated transactions are the forecast,
not the balance (finance/forecast.py).
"""
from decimal import Decimal

from django.db.models import Q, Sum

PENDING = 'P'


def included(in_balance_as_of, anchor_date):
    """Whether the account's current bank balance already counts this transaction."""
    return in_balance_as_of is not None and anchor_date is not None and in_balance_as_of <= anchor_date


def not_included_q(anchor_date):
    """The cleared transactions the bank balance as of anchor_date doesn't count yet."""
    return Q(in_balance_as_of__isnull=True) | Q(in_balance_as_of__gt=anchor_date)


def mark_included(account):
    """A balance was just set: the cleared transactions dated up to it are in it."""
    from .models import Transaction
    if account.balance_date is None:
        return
    Transaction.objects.filter(account=account, in_balance_as_of__isnull=True, dtposted__lte=account.balance_date) \
        .exclude(status=PENDING).update(in_balance_as_of=account.balance_date)


def balance_on(account, day):
    """An account's balance at the end of day, by the rule above (positive = money you have)."""
    from .models import Transaction
    cleared = Transaction.objects.filter(account=account).exclude(status=PENDING)
    total = lambda qs: qs.aggregate(s=Sum('trnamt'))['s'] or Decimal('0')
    anchor_date = account.balance_date
    if anchor_date is None:
        return total(cleared.filter(dtposted__lte=day))
    anchor = account.last_balance or Decimal('0')
    counted = cleared.filter(in_balance_as_of__isnull=False, in_balance_as_of__lte=anchor_date)
    others = cleared.filter(not_included_q(anchor_date))
    return anchor - total(counted.filter(dtposted__gt=day)) + total(others.filter(dtposted__lte=day))
