# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
"""
Loans: what is owed, splitting a payment, and looking ahead.

A loan is an account. It starts with the balance owed on a date, and from
then on its balance comes from real transactions: the principal of each
payment recorded against it (record_payment), so an extra payment or a
missed one shows exactly as it happened. A balance from a statement can be
entered at any time (set_statement_balance) and becomes the new starting
point, which corrects any small difference in how the lender worked out
interest.

The schedule (fixed-rate amortisation, payments at the end of each period)
is only used to suggest how a payment splits into interest and principal,
and to look ahead: the payoff date from the actual balance. Weekly and
bi-weekly loans are treated as 52 or 26 payments a year.

Escrow (property tax, home insurance) and PMI paid with the payment are,
depending on the loan's escrow_mode, either expensed at once or paid into
an escrow account, from which the lender's tax and insurance bills are
recorded as withdrawals (record_escrow_disbursement).
"""
import calendar
import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction as db_transaction
from django.db.models import Sum

from .balances import mark_included
from .starter import subcategory_for

CENT = Decimal('0.01')
PERIODS_PER_YEAR = {'MONTHLY': 12, 'BIWEEKLY': 26, 'WEEKLY': 52}


def _periods_per_year(loan):
    return PERIODS_PER_YEAR.get(loan.payment_schedule, 12)


def _rate_per_period(loan):
    return Decimal(loan.interest_rate) / 100 / _periods_per_year(loan)


def number_of_payments(loan):
    return max(round(loan.term_months * _periods_per_year(loan) / 12), 1)


def scheduled_payment(loan):
    """Principal and interest per payment, worked out from amount, rate and term."""
    principal = Decimal(loan.original_balance)
    rate = _rate_per_period(loan)
    count = number_of_payments(loan)
    if rate == 0:
        return (principal / count).quantize(CENT, ROUND_HALF_UP)
    growth = (1 + rate) ** count
    return (principal * rate * growth / (growth - 1)).quantize(CENT, ROUND_HALF_UP)


def payment(loan):
    """The payment as on the loan papers, or as worked out."""
    return Decimal(loan.payment_amount) if loan.payment_amount else scheduled_payment(loan)


def _add_months(day, months):
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def payment_dates(loan):
    """Every scheduled payment date, first to last."""
    count = number_of_payments(loan)
    first = loan.first_payment_date
    if loan.payment_schedule in ('BIWEEKLY', 'WEEKLY'):
        step = timedelta(days=14 if loan.payment_schedule == 'BIWEEKLY' else 7)
        return [first + step * index for index in range(count)]
    return [_add_months(first, index) for index in range(count)]


def scheduled_balance(loan, day):
    """
    What the schedule says is still owed at the end of day: the original
    amount until the first payment, then less each payment's principal
    (including the usual extra principal), never below zero.
    """
    balance = Decimal(loan.original_balance)
    rate = _rate_per_period(loan)
    regular = payment(loan)
    extra = Decimal(loan.default_extra_principal or 0)
    dates = payment_dates(loan)
    for index, due in enumerate(dates):
        if due > day or balance <= 0:
            break
        if index == len(dates) - 1:
            # The last payment is adjusted to clear the loan, as lenders do:
            # rounding each payment to the cent leaves a few cents over
            return Decimal('0.00')
        interest = (balance * rate).quantize(CENT, ROUND_HALF_UP)
        balance -= (regular - interest) + extra
    return max(balance, Decimal('0')).quantize(CENT)


def payoff_date(loan):
    """The date of the last payment on the schedule (earlier with extra principal)."""
    balance = Decimal(loan.original_balance)
    rate = _rate_per_period(loan)
    regular = payment(loan)
    extra = Decimal(loan.default_extra_principal or 0)
    last = None
    for due in payment_dates(loan):
        if balance <= 0:
            break
        interest = (balance * rate).quantize(CENT, ROUND_HALF_UP)
        balance -= (regular - interest) + extra
        last = due
    return last


# --- The actual balance, from transactions ---

class LoanError(Exception):
    """Something about a payment that the user needs to put right; the message says what."""


def balance_on(account, day):
    return _balance_on(account, day).quantize(CENT)


def _balance_on(account, day):
    """
    An account's balance at the end of day: its starting (or last statement)
    balance, adjusted by the transactions it does or doesn't already include
    (finance/balances.py). A loan's balance is negative: what is owed.
    """
    from .balances import balance_on as included_balance_on
    return included_balance_on(account, day)


def owed_on(loan_account, day):
    """What is owed on the loan at the end of day, as a positive number."""
    return max(-balance_on(loan_account, day), Decimal('0')).quantize(CENT)


def _ahead(loan, owed, after):
    """
    Each scheduled payment after `after`, with what is still owed once it's
    paid, if the regular payment (plus the usual extra principal) is paid
    every time, until the loan is paid off. Past the end of the original
    term it carries on monthly. A payment that doesn't cover the interest
    is yielded with None, and nothing after it.
    """
    rate = _rate_per_period(loan)
    regular = payment(loan)
    extra = Decimal(loan.default_extra_principal or 0)
    balance = Decimal(owed)
    count = 0
    last = None
    for due in payment_dates(loan):
        if due <= after:
            continue
        if balance <= 0:
            return
        interest = (balance * rate).quantize(CENT, ROUND_HALF_UP)
        principal = regular - interest + extra
        if principal <= 0:
            yield due, None
            return
        balance -= principal
        count += 1
        last = due
        yield due, max(balance, Decimal('0'))
    while balance > 0 and count < 1200:
        interest = (balance * rate).quantize(CENT, ROUND_HALF_UP)
        balance -= regular - interest + extra
        count += 1
        last = _add_months(last or after, 1)
        yield last, max(balance, Decimal('0'))


def projection(loan, owed, after):
    """
    Looking ahead from the actual balance: the payoff date and the number of
    payments left, if the regular payment (plus the usual extra principal)
    is paid on every scheduled date after `after`.
    """
    count = 0
    last = None
    for due, balance in _ahead(loan, owed, after):
        if balance is None:
            return {'payoff_date': None, 'payments_left': None}    # the payment doesn't cover the interest
        count += 1
        last = due
    return {'payoff_date': last, 'payments_left': count}


def equity_history(loan_account, today=None):
    """
    A loan and the home or vehicle it's secured on, at every month end over
    the loan's life: what the asset is worth, what is owed, and the equity
    between them (value less owed; negative when more is owed than it's
    worth). Up to today, values are the recorded ones and the loan's actual
    balance (from the schedule before its first known balance). After today
    the loan follows the schedule from today's balance, and the value stays
    at its latest figure.
    """
    from .models import BalanceEntry, Transaction
    from .networth import _Holding, _Loan, month_ends
    today = today or date.today()
    loan = loan_account.loan_details
    asset_account = loan.asset_account
    value_of = _Holding(asset_account, list(
        BalanceEntry.objects.filter(account=asset_account).order_by('date').values_list('date', 'amount')), today)
    # The same balances as the Net Worth page
    balance_of = _Loan(loan_account, list(
        Transaction.objects.filter(account=loan_account, dtposted__lte=today).exclude(status='P')
        .order_by('dtposted', 'id').values_list('dtposted', 'trnamt', 'in_balance_as_of')))

    def owed_until_today(day):
        balance = balance_of.on(day) or Decimal('0')
        known = balance_of.known_from and day >= balance_of.known_from
        return max(-balance, Decimal('0')).quantize(CENT), 'actual' if known else 'schedule'

    def value(day):
        worth = value_of.on(day)
        if worth is None and value_of.opened:
            worth = value_of.on(value_of.opened)     # before it was bought or valued: its first value
        return worth

    owed_now = owed_until_today(today)[0]
    ahead = list(_ahead(loan, owed_now, today))
    covers_interest = not ahead or ahead[-1][1] is not None
    if not covers_interest:
        ahead = []
    start = loan.origination_date or _add_months(loan.first_payment_date, -1)
    start = min(start, today)
    end = ahead[-1][0] if ahead else today
    days = month_ends(start, today) + [day for day in month_ends(today, end) if day > today]

    points = []
    for day in days:
        if day <= today:
            owed, source = owed_until_today(day)
        else:
            paid = [balance for due, balance in ahead if due <= day]
            owed, source = (paid[-1] if paid else owed_now).quantize(CENT), 'projected'
        worth = value(day)
        points.append({
            'date': day,
            'value': worth,
            'owed': owed,
            'equity': None if worth is None else worth - owed,
            'source': source,
        })
    now = next(point for point in points if point['date'] == today)
    return {
        'loan': {'id': loan_account.pk, 'name': loan_account.name},
        'asset': {'id': asset_account.pk, 'name': asset_account.name},
        'today': now,
        'payoff_date': end if ahead else None,
        'covers_interest': covers_interest,
        'points': points,
    }


# --- Splitting a payment ---

ESCROW_PARTS = ('property_tax', 'insurance', 'pmi')


def suggest_split(loan, loan_account, day, amount=None, extra_principal=Decimal('0')):
    """
    How a payment on day splits: interest on the actual balance owed before
    it, the escrow and PMI from the loan's terms, and the rest principal.
    With no amount, the regular payment plus escrow plus any extra principal.
    """
    owed = owed_on(loan_account, day - timedelta(days=1))
    interest = (owed * _rate_per_period(loan)).quantize(CENT, ROUND_HALF_UP)
    parts = {
        'interest': interest,
        'property_tax': Decimal(loan.property_tax_amount or 0),
        'insurance': Decimal(loan.homeowners_insurance_amount or 0),
        'pmi': Decimal(loan.pmi_amount or 0),
    }
    fixed = sum(parts.values())
    if amount is None:
        principal = payment(loan) - interest + Decimal(extra_principal or 0)
    else:
        principal = Decimal(amount) - fixed
    # Never pay off more than is owed
    parts['principal'] = max(min(principal, owed), Decimal('0')).quantize(CENT)
    parts['total'] = (fixed + parts['principal']).quantize(CENT)
    parts['owed_before'] = owed
    parts['owed_after'] = (owed - parts['principal']).quantize(CENT)
    return parts


def categories_for(loan):
    """
    Where each part of a payment goes: the loan's own categories, or the
    subcategories that do those jobs (finance/starter.py ROLES).
    """
    interest = {'Mortgage': 'mortgage_interest', 'Auto': 'auto_loan_interest'}.get(loan.loan_type, 'loan_interest')
    return {
        'interest': loan.interest_category or subcategory_for(interest),
        'property_tax': loan.property_tax_category or subcategory_for('property_tax'),
        'insurance': loan.homeowners_insurance_category or subcategory_for('home_insurance'),
        'pmi': loan.pmi_category or subcategory_for('pmi'),
        'principal': subcategory_for('loan_principal'),
        'escrow': subcategory_for('escrow'),
    }


def _money(value):
    try:
        amount = Decimal(str(value)).quantize(CENT)
    except Exception:
        raise LoanError('Every amount must be a number.')
    if amount < 0:
        raise LoanError('Amounts must be zero or more.')
    return amount


def record_payment(loan, loan_account, day, parts, from_account=None, existing=None, memo=''):
    """
    Records a loan payment. parts: interest, property_tax, insurance, pmi,
    principal (extra principal included). Either `existing` (a payment
    already in the ledger, e.g. imported from checking) is split and linked,
    or a new pending payment is made in from_account, which a later bank
    import will match. Returns the payment transaction.
    """
    from .models import LoanDetail, Transaction, TransactionSplit
    amounts = {name: _money(parts.get(name, 0)) for name in ('interest', 'principal') + ESCROW_PARTS}
    total = sum(amounts.values())
    if total <= 0:
        raise LoanError('The payment is zero.')
    if amounts['principal'] > owed_on(loan_account, day) + CENT:
        raise LoanError('The principal is more than is owed on the loan.')

    escrow_into_account = loan.escrow_mode == LoanDetail.EscrowMode.ACCOUNT and loan.escrow_account_id
    categories = categories_for(loan)

    with db_transaction.atomic():
        if existing is not None:
            if existing.trnamt != -total:
                raise LoanError(f'The parts add up to {total}, but the payment in the ledger is {-existing.trnamt}.')
            if existing.linked_transaction_id or existing.payment_parts.exists():
                raise LoanError('That transaction is already linked to something else.')
            payment_tx = existing
        else:
            if from_account is None:
                raise LoanError('Choose the account the payment comes from.')
            payment_tx = Transaction.objects.create(
                account=from_account, dtposted=day, trnamt=-total, status='P', is_manual=True,
                name=f'{loan.lender or loan_account.name} payment', personal_memo=memo,
                fitid=f'LOANPMT-{uuid.uuid4().hex[:16]}',
            )
        payment_tx.subcategory = None
        payment_tx.save(update_fields=['subcategory'])
        payment_tx.splits.all().delete()

        def split(category, amount, note):
            if amount:
                TransactionSplit.objects.create(transaction=payment_tx, subcategory=category, amount=-amount, memo=note)

        split(categories['principal'], amounts['principal'], f'Principal: {loan_account.name}')
        split(categories['interest'], amounts['interest'], 'Interest')
        escrow_total = amounts['property_tax'] + amounts['insurance'] + amounts['pmi']
        if escrow_into_account:
            split(categories['escrow'], escrow_total, f'Escrow: {loan.escrow_account.name}')
        else:
            split(categories['property_tax'], amounts['property_tax'], 'Escrow: property tax')
            split(categories['insurance'], amounts['insurance'], 'Escrow: home insurance')
            split(categories['pmi'], amounts['pmi'], 'PMI')

        def part(account, amount, suffix, name):
            if amount:
                Transaction.objects.create(
                    account=account, dtposted=day, trnamt=amount, status='C', is_manual=True, name=name,
                    subcategory=categories['principal'] if suffix == 'P' else categories['escrow'],
                    source_transaction=payment_tx, fitid=f'LOANPMT-{payment_tx.pk}-{suffix}',
                )

        part(loan_account, amounts['principal'], 'P', f'Payment from {payment_tx.account.name}')
        if escrow_into_account:
            part(loan.escrow_account, escrow_total, 'E', f'Escrow from {payment_tx.account.name}')
    return payment_tx


def payment_candidates(loan_account, day, total, days=10):
    """Payments already in the ledger that could be this loan payment: same amount, near the date, not yet split or linked."""
    from .models import Transaction
    return Transaction.objects.filter(
        trnamt=-Decimal(total), dtposted__range=(day - timedelta(days=days), day + timedelta(days=days)),
        linked_transaction__isnull=True, splits__isnull=True, payment_parts__isnull=True, source_transaction__isnull=True,
        account__loan_details__isnull=True, account__asset_details__isnull=True, account__escrow_for_loan__isnull=True,
    ).select_related('account').distinct().order_by('dtposted')[:10]


def record_escrow_disbursement(loan, day, amount, kind, memo=''):
    """The lender paid a bill from escrow: a withdrawal from the escrow account, which is the expense."""
    from .models import LoanDetail, Transaction
    if loan.escrow_mode != LoanDetail.EscrowMode.ACCOUNT or not loan.escrow_account_id:
        raise LoanError('This loan expenses its escrow with each payment; there is no escrow account to pay from.')
    amount = _money(amount)
    if not amount:
        raise LoanError('Enter the amount paid from escrow.')
    categories = categories_for(loan)
    category = {'property_tax': categories['property_tax'], 'insurance': categories['insurance'],
                'pmi': categories['pmi']}.get(kind) or subcategory_for('escrow_payments')
    label = {'property_tax': 'Property tax', 'insurance': 'Home insurance', 'pmi': 'PMI'}.get(kind, 'Escrow payment')
    return Transaction.objects.create(
        account=loan.escrow_account, dtposted=day, trnamt=-amount, status='C', is_manual=True,
        name=f'{label} paid from escrow', personal_memo=memo, subcategory=category,
        fitid=f'ESCROWOUT-{uuid.uuid4().hex[:16]}',
    )


def set_statement_balance(account, day, amount, source='', negative=True):
    """
    A balance from a statement becomes the account's starting point: later
    transactions are added to it. Also kept in the account's value history.
    """
    from .models import BalanceEntry
    amount = _money(amount)
    with db_transaction.atomic():
        account.last_balance = -amount if negative else amount
        account.balance_date = day
        account.is_balance_calculated = False
        account.save(update_fields=['last_balance', 'balance_date', 'is_balance_calculated'])
        # The payments dated up to the statement are in its balance
        mark_included(account)
        BalanceEntry.objects.update_or_create(account=account, date=day, defaults={'amount': amount, 'source': source})
