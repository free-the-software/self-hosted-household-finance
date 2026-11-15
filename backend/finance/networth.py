# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
"""
Net worth over time: every account's balance at each month end, added up.

There is no stored history of balances, so each account's past balances are
worked out backwards from the last balance its bank reported, through its
transactions: the balance on any day is that balance, minus whatever came
in or went out between that day and the bank's balance date. A gap in an
account's imported history therefore shifts every balance before the gap.

Balances are signed as stored: money you have is positive, money you owe
(credit cards, loans) is negative. Accounts marked invert_sign store debts
as positive numbers, so they are flipped. An account is counted from the
month of its first transaction, and not after the date it was closed.

Loans and escrow accounts work like bank accounts: a starting (or last
statement) balance, and the real payments recorded since (finance/loans.py).
A loan counts from the day it began: before its first known balance (a
statement entered later replaces the starting one), from its schedule.
Assets (homes, vehicles, investments...) have no transactions: each is worth
its latest recorded value (BalanceEntry), or its purchase price before any
value was recorded, times your ownership share.
"""
import calendar
from bisect import bisect_right
from datetime import date, timedelta
from decimal import Decimal
from itertools import accumulate

from .balances import included
from .models import Account, BalanceEntry, Transaction


# Ranges up to this long get a point for every day; longer ones, every month end
DAILY_UP_TO_DAYS = 92


def points_between(start, end):
    """The dates to show: start, then every day or every month end, then end."""
    if (end - start).days <= DAILY_UP_TO_DAYS:
        return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    middle = [day for day in month_ends(start, end) if start < day < end]
    return [start] + middle + [end]


def month_ends(start, end):
    """The last day of every month from start's month up to end, then end itself."""
    points = []
    year, month = start.year, start.month
    while True:
        last = date(year, month, calendar.monthrange(year, month)[1])
        if last >= end:
            break
        points.append(last)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    points.append(end)
    return points


class _Running:
    """A running total of (date, amount) rows in date order, for any day."""

    def __init__(self, rows):
        self.dates = [day for day, _amount in rows]
        self.totals = list(accumulate(amount for _day, amount in rows))

    def through(self, day):
        """The total of every row dated on or before day."""
        index = bisect_right(self.dates, day)
        return self.totals[index - 1] if index else Decimal('0')

    def total(self):
        return self.totals[-1] if self.totals else Decimal('0')


class _Balances:
    """
    One account's balance on any day: its bank balance, less the cleared
    transactions that balance includes dated after the day, plus the cleared
    ones it doesn't include dated up to it (finance/balances.py).
    """

    def __init__(self, account, transactions):
        # transactions: (date, amount, in_balance_as_of), cleared only, in date order
        self.sign = -1 if account.invert_sign else 1
        self.anchor = account.last_balance or Decimal('0')
        self.anchor_date = account.balance_date
        self.closed = account.closed_date
        counted = [(day, amount) for day, amount, as_of in transactions if included(as_of, self.anchor_date)]
        others = [(day, amount) for day, amount, as_of in transactions if not included(as_of, self.anchor_date)]
        self.counted, self.others = _Running(counted), _Running(others)
        self.dates = [day for day, _amount, _as_of in transactions]
        dates = self.dates + ([self.anchor_date] if self.anchor_date else [])
        self.opened = min(dates) if dates else None

    def on(self, day):
        """The balance at the end of day, or None if the account didn't exist yet (or had closed)."""
        if self.opened is None or day < self.opened or (self.closed and day > self.closed):
            return None
        if self.anchor_date is None:
            balance = self.counted.through(day) + self.others.through(day)     # no bank balance: transactions alone
        else:
            balance = self.anchor - (self.counted.total() - self.counted.through(day)) + self.others.through(day)
        return self.sign * balance


class _Loan(_Balances):
    """A loan's balance on any day: its schedule until its first known balance."""

    def __init__(self, account, transactions):
        from .loans import _add_months
        super().__init__(account, transactions)
        self.loan = account.loan_details
        began = self.loan.origination_date or _add_months(self.loan.first_payment_date, -1)
        self.known_from = self.opened
        self.opened = min(began, self.known_from) if self.known_from else began

    def on(self, day):
        if self.known_from is None or day < self.known_from:
            if day < self.opened or (self.closed and day > self.closed):
                return None
            from .loans import scheduled_balance
            return -self.sign * scheduled_balance(self.loan, day)
        return super().on(day)


class _Holding:
    """An asset's value on any day."""

    def __init__(self, account, entries, today):
        self.entries = entries                  # [(date, amount)], oldest first
        self.dates = [day for day, _amount in entries]
        self.asset = getattr(account, 'asset_details', None)
        self.closed = account.closed_date
        starts = list(self.dates)
        if self.asset and self.asset.purchase_date:
            starts.append(self.asset.purchase_date)
        # Something with only a price (no dates at all) is counted from today
        if not starts and self.asset and self.asset.purchase_price is not None:
            starts.append(today)
        self.opened = min(starts) if starts else None

    def on(self, day):
        if self.opened is None or day < self.opened or (self.closed and day > self.closed):
            return None
        index = bisect_right(self.dates, day)
        entry = self.entries[index - 1] if index else None
        if entry is not None:
            value = entry[1]
        elif self.asset and self.asset.purchase_price is not None:
            value = self.asset.purchase_price
        else:
            return None
        share = (self.asset.ownership_percent if self.asset else Decimal('100')) / 100
        return (value * share).quantize(Decimal('0.01'))


def kind_of(account):
    """What sort of account this is, for grouping on the Net Worth page."""
    asset = getattr(account, 'asset_details', None)
    loan = getattr(account, 'loan_details', None)
    if loan:
        return 'loan'
    if asset:
        return asset.kind.lower()
    return 'bank'


GROUP_ORDER = ['checking', 'savings', 'investments', 'property', 'other_assets', 'credit_cards', 'loans', 'other']
BANK_GROUPS = {
    'CHECKING': 'checking', 'JOINT CHECKING': 'checking', 'CASH': 'checking',
    'SAVINGS': 'savings',
    'INVESTMENT': 'investments', 'BROKERAGE': 'investments', 'RETIREMENT': 'investments',
    'CREDIT CARD': 'credit_cards',
    'LOAN': 'loans', 'MORTGAGE': 'loans',
    # A loan's escrow account is listed with the loans
    'ESCROW': 'loans',
}


def group_of(account):
    """Where an account is listed on the Net Worth page (in GROUP_ORDER)."""
    kind = kind_of(account)
    if kind == 'bank':
        return BANK_GROUPS.get((account.account_type or '').upper(), 'other')
    return {'loan': 'loans', 'investment': 'investments', 'home': 'property', 'vehicle': 'property'}.get(kind, 'other_assets')


def history(today=None, start=None, end=None):
    """
    Returns {'start', 'end', 'accounts': [...], 'points': [...]}: for each
    date in the range (every day for a short range, every month end for a
    long one, always the first and last day), every account's balance and
    the totals. With no start, the range begins at the first known balance.
    """
    today = today or date.today()
    end = min(end or today, today)
    accounts = list(Account.objects.select_related('asset_details', 'loan_details').order_by('name'))
    by_account = {account.pk: [] for account in accounts}
    # Only money that has actually moved: future-dated (scheduled) and pending
    # transactions (entered by hand or by a recurring bill, not yet through
    # the bank) are the outlook, not the balance; the bank's balance doesn't
    # include them either
    for account_id, day, amount, as_of in Transaction.objects.filter(dtposted__lte=today).exclude(
        status=Transaction.STATUS_PENDING
    ).order_by('dtposted', 'id').values_list(
        'account_id', 'dtposted', 'trnamt', 'in_balance_as_of'
    ):
        if account_id in by_account:
            by_account[account_id].append((day, amount, as_of))

    entries = {account.pk: [] for account in accounts}
    for account_id, day, amount in BalanceEntry.objects.order_by('date').values_list('account_id', 'date', 'amount'):
        if account_id in entries:
            entries[account_id].append((day, amount))

    def tracker(account):
        if kind_of(account) == 'loan':
            return _Loan(account, by_account[account.pk])
        if kind_of(account) == 'bank':
            return _Balances(account, by_account[account.pk])
        return _Holding(account, entries[account.pk], today)

    balances = {account.pk: tracker(account) for account in accounts}
    starts = [b.opened for b in balances.values() if b.opened and b.opened <= end]
    if not starts:
        return {'start': start, 'end': end, 'accounts': [], 'points': []}
    start = min(start or min(starts), end)

    points = []
    for day in points_between(start, end):
        per_account = {}
        for account in accounts:
            value = balances[account.pk].on(day)
            if value is not None:
                per_account[account.pk] = value
        assets = sum((v for v in per_account.values() if v > 0), Decimal('0'))
        debts = sum((v for v in per_account.values() if v < 0), Decimal('0'))
        points.append({
            'date': day,
            'assets': assets,
            'liabilities': debts,
            'net_worth': assets + debts,
            'accounts': {str(pk): value for pk, value in per_account.items()},
        })

    def freshness(account):
        """How current the figure is: (label, date), shown under the account's name."""
        kind = kind_of(account)
        if kind in ('bank', 'loan'):
            if account.balance_date is None:
                return 'From transactions', (by_account[account.pk][-1][0] if by_account[account.pk] else None)
            if kind == 'loan' or account.account_type == 'ESCROW':
                # The starting or statement balance, or the latest payment since
                latest = max([account.balance_date] + [row[0] for row in by_account[account.pk]])
                return 'Updated', latest
            return ('Calculated' if account.is_balance_calculated else 'Verified'), account.balance_date
        if entries[account.pk]:
            return 'Valued', entries[account.pk][-1][0]
        asset = getattr(account, 'asset_details', None)
        return 'Purchase price', (asset.purchase_date if asset else None)

    listed = [account for account in accounts if balances[account.pk].opened and balances[account.pk].opened <= end]
    listed.sort(key=lambda account: (GROUP_ORDER.index(group_of(account)), account.name.lower()))
    return {
        'start': start,
        'end': end,
        'accounts': [
            {
                'id': account.pk,
                'name': account.name,
                'type': (account.account_type or '').upper(),
                'kind': kind_of(account),
                'group': group_of(account),
                'closed': account.closed_date,
                'balance_is_calculated': account.is_balance_calculated,
                'as_of_label': freshness(account)[0],
                'as_of': freshness(account)[1],
            }
            for account in listed
        ],
        'points': points,
    }
