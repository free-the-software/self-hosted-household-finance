# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
"""
Setting up a year's budget from the year before, as personal-finance
programs do: start from last year's budget, or from what was actually spent
and earned (each month as it was, so a December bill stays in December, or
the average month), adjust by a percentage (one for income, one for
expenses), round, and fill the year.

Budgets are kept per subcategory per month, as positive amounts for both
income and expenses (Budget). Actual spending is negative in the ledger, so
an expense's actual becomes its budget the other way round; a subcategory
that took in more than it spent (refunds) gets no budget. Transfers have no
budget.

The source year is the one before. While it is still under way (planning
2027 in November 2026), only its complete months count: the averaged
choice averages those, and month by month takes each missing month from the
same month a year earlier, if there is one.
"""
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import ExtractMonth

from .models import Budget, SubCategory, Transaction, TransactionSplit

SOURCES = ('budget', 'actual_monthly', 'actual_average', 'blank')
SOURCE_LABELS = {
    'budget': "Last year's budget",
    'actual_monthly': "Last year's actual, month by month",
    'actual_average': "Last year's actual, averaged",
    'blank': 'Blank',
}
ROUNDINGS = (0, 1, 10, 100)
CENT = Decimal('0.01')


class PlanError(Exception):
    """Something to choose differently; the message says what."""


def _complete_months(year, today):
    """The months of a year that are over: all of a past year, none of a future one."""
    if year < today.year:
        return list(range(1, 13))
    if year == today.year:
        return list(range(1, today.month))
    return []


def monthly_actuals(year, months):
    """{(subcategory id, month): actual} for those months; split transactions count through their lines."""
    actuals = defaultdict(Decimal)
    if not months:
        return actuals
    whole = Transaction.objects.filter(dtposted__year=year, subcategory__isnull=False, splits__isnull=True) \
        .annotate(month=ExtractMonth('dtposted')).filter(month__in=months) \
        .values('subcategory_id', 'month').annotate(total=Sum('trnamt'))
    parts = TransactionSplit.objects.filter(transaction__dtposted__year=year) \
        .annotate(month=ExtractMonth('transaction__dtposted')).filter(month__in=months) \
        .values('subcategory_id', 'month').annotate(total=Sum('amount'))
    for row in list(whole) + list(parts):
        actuals[(row['subcategory_id'], row['month'])] += row['total']
    return actuals


def _as_budget(actual, kind):
    """What was spent (negative) or earned (positive), as a budget amount; nothing for the other way round."""
    amount = -actual if kind == 'EXP' else actual
    return max(amount, Decimal('0'))


def source_amounts(year, source, kinds, today):
    """{(subcategory id, month): amount} for the year being planned, before adjusting."""
    previous = year - 1
    subs = {sub.id: sub.category.type for sub in SubCategory.objects.select_related('category') if sub.category.type in kinds}
    amounts = {}
    if source == 'budget':
        for row in Budget.objects.filter(year=previous, subcategory_id__in=subs).values('subcategory_id', 'month', 'amount'):
            amounts[(row['subcategory_id'], row['month'])] = row['amount']
    elif source == 'actual_monthly':
        months = _complete_months(previous, today)
        actuals = monthly_actuals(previous, months)
        # A month not over yet: the same month a year earlier
        earlier = monthly_actuals(previous - 1, [m for m in range(1, 13) if m not in months])
        for (sub, month), actual in list(actuals.items()) + list(earlier.items()):
            if sub in subs:
                amounts[(sub, month)] = _as_budget(actual, subs[sub])
    elif source == 'actual_average':
        months = _complete_months(previous, today)
        if not months:
            raise PlanError(f'{previous} has no complete month yet to take an average from.')
        totals = defaultdict(Decimal)
        for (sub, _month), actual in monthly_actuals(previous, months).items():
            totals[sub] += actual
        for sub, total in totals.items():
            if sub in subs:
                each = _as_budget(total / len(months), subs[sub])
                for month in range(1, 13):
                    amounts[(sub, month)] = each
    return amounts, subs


def _adjust(amount, percent, rounding):
    amount = amount * (1 + percent / 100)
    if rounding:
        return (amount / rounding).quantize(Decimal('1'), ROUND_HALF_UP) * rounding
    return amount.quantize(CENT, ROUND_HALF_UP)


def plan_year(year, source='budget', percent=Decimal('0'), rounding=0, kinds=('EXP', 'INC'), replace=False,
              save=True, today=None, income_percent=None, expense_percent=None):
    """
    Works out a year's budget and, with save, sets it. Keeps months that
    already have a budget unless replace, which clears the chosen kinds'
    budgets for the year first. Returns the totals it comes to and how many
    month amounts it set.
    """
    today = today or date.today()
    if source not in SOURCES:
        raise PlanError(f'source must be one of: {", ".join(SOURCES)}.')
    if rounding not in ROUNDINGS:
        raise PlanError('rounding must be 0, 1, 10 or 100.')
    kinds = [kind for kind in kinds if kind in ('EXP', 'INC')]
    if not kinds:
        raise PlanError('Choose income, expenses, or both.')

    amounts, subs = source_amounts(year, source, kinds, today)
    # Income and expenses can change by different amounts (a raise, prices going up)
    percents = {'INC': percent if income_percent is None else income_percent,
                'EXP': percent if expense_percent is None else expense_percent}
    planned = {key: _adjust(value, percents[subs[key[0]]], rounding) for key, value in amounts.items()}
    planned = {key: value for key, value in planned.items() if value > 0}
    # A month counts as budgeted only with an amount: a 0 looks the same as
    # no budget on screen, so it never blocks a plan
    existing = {} if replace else {
        (row['subcategory_id'], row['month']): row['amount']
        for row in Budget.objects.filter(year=year, subcategory_id__in=subs).exclude(amount=0)
        .values('subcategory_id', 'month', 'amount')
    }
    to_set = {key: value for key, value in planned.items() if key not in existing}

    # The year as it would be: kept months plus the new ones
    final = {**existing, **to_set}
    totals = {'income': Decimal('0'), 'expenses': Decimal('0')}
    for (sub, _month), amount in final.items():
        totals['income' if subs[sub] == 'INC' else 'expenses'] += amount
    # Each category's twelve months as planned, to review before anything is saved
    # Every income and expense category, so one with nothing behind it can be filled in too
    names = {sub.id: sub for sub in SubCategory.objects.select_related('category').filter(pk__in=subs)}
    last_year = monthly_actuals(year - 1, _complete_months(year - 1, today))
    existing_totals = defaultdict(Decimal)
    for (sub, _month), amount in existing.items():
        existing_totals[sub] += amount
    rows = []
    for sid, sub in names.items():
        kind = subs[sid]
        months = [planned.get((sid, m), Decimal('0')) for m in range(1, 13)]
        change = percents[kind]
        if any(months):
            reason = SOURCE_LABELS[source] + (f', {change:+g}%' if change else '')
        else:
            reason = f'Not budgeted in {year - 1}' if source == 'budget' else f'Nothing in {year - 1}'
        rows.append({
            'subcategory': sid, 'name': sub.name, 'category': sub.category.name, 'kind': kind,
            'history': [{'label': str(year - 1), 'total': sum((_as_budget(last_year.get((sid, m), Decimal('0')), 'INC' if kind == 'INC' else 'EXP')
                                                                for m in range(1, 13)), Decimal('0'))}],
            'suggestion': sum(months, Decimal('0')), 'months': months, 'reasons': [reason], 'one_offs': [],
            'existing_budget': existing_totals.get(sid, Decimal('0')),
        })
    order = {sid: (sub.category.sort_order, sub.category.name.lower(), sub.name.lower()) for sid, sub in names.items()}
    rows.sort(key=lambda r: (r['kind'] != 'INC', order[r['subcategory']]))
    result = {
        'year': year,
        'rows': rows,
        'income_budget': totals['income'],
        'expense_budget': totals['expenses'],
        'savings_budget': totals['income'] - totals['expenses'],
        'months_set': len(to_set),
        'months_kept': len(existing),
    }
    if save:
        with transaction.atomic():
            if replace:
                Budget.objects.filter(year=year, subcategory_id__in=subs).delete()
            else:
                # Saved zeros are no budget: cleared, so the plan fills those months
                Budget.objects.filter(year=year, subcategory_id__in=subs, amount=0).delete()
            Budget.objects.bulk_create(
                Budget(subcategory_id=sub, year=year, month=month, amount=amount) for (sub, month), amount in to_set.items())
    return result
