# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
"""
Suggesting next year's budget, category by category, from what was actually
spent and earned, with the reason for each suggestion so it can be checked.

History is read in 12-month blocks counting back from the last complete
month (so a year still under way counts fully): the last 12 months, the 12
before, the 12 before that. Only blocks the ledger fully covers are used.

For each subcategory (income and expense; transfers have no budget):
- One-offs: a single item far above the category's usual months, with
  nothing comparable in the other blocks (a $2,400 repair in a $100/month
  category). Left out by default and listed, so they can be added back.
- Yearly bills: a similar amount paid about 12 months apart (insurance).
  Budgeted in the month they're paid, or a twelfth a month.
- The level: the blocks' average, recent years weighted more if asked; a
  steady trend (rising, or falling, every block) is carried forward instead,
  capped at 15% a year.
- Recurring transactions set up for the category are a known minimum.
- The months: seasonal categories keep their shape (gifts in December);
  the rest is spread evenly.

Plain arithmetic on the user's own data; nothing leaves the server.
"""
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from statistics import median

from django.db import transaction as db_transaction

from .models import Budget, RecurringTransaction, SubCategory, Transaction, TransactionSplit

CENT = Decimal('0.01')
ROUNDINGS = (0, 1, 10, 100)
MAX_TREND = Decimal('0.15')          # a trend carried forward is capped at 15% a year
MIN_TREND = Decimal('0.03')          # smaller changes are noise: averaged instead
ONE_OFF_TIMES_USUAL = 3              # a one-off is over 3 times a usual month...
ONE_OFF_AT_LEAST = Decimal('100')    # ...and at least this much
SEASONAL_SHARE = Decimal(2) / 12     # a month with over twice its even share makes a category seasonal
PER_YEAR = {'WEEKLY': 52, 'BIWEEKLY': 26, 'SEMI_MONTHLY': 24, 'MONTHLY': 12, 'SEMI_ANNUALLY': 2, 'ANNUALLY': 1}
MONTH_NAMES = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
               'October', 'November', 'December')


class SuggestionError(Exception):
    """Something to choose differently; the message says what."""


def _month_index(day):
    return day.year * 12 + day.month - 1


def _from_index(index):
    return index // 12, index % 12 + 1


def _money(amount):
    return f'${amount.quantize(Decimal("1"), ROUND_HALF_UP):,}'


def _round(amount, rounding):
    if rounding:
        return (amount / rounding).quantize(Decimal('1'), ROUND_HALF_UP) * rounding
    return amount.quantize(CENT, ROUND_HALF_UP)


def _items(kinds, first, end):
    """{subcategory id: [(id, date, amount as budget, description)]} between two dates (end excluded).

    As budget: spending is positive for an expense, earnings for income; a
    refund in an expense category counts against it."""
    subs = {sub.id: sub for sub in SubCategory.objects.select_related('category') if sub.category.type in kinds}
    found = defaultdict(list)
    sign = {sid: (-1 if sub.category.type == 'EXP' else 1) for sid, sub in subs.items()}
    whole = Transaction.objects.filter(dtposted__gte=first, dtposted__lt=end, subcategory_id__in=subs,
                                       splits__isnull=True).values_list('id', 'dtposted', 'trnamt', 'name', 'subcategory_id')
    for tid, day, amount, name, sid in whole:
        found[sid].append((f't{tid}', day, amount * sign[sid], name or ''))
    parts = TransactionSplit.objects.filter(transaction__dtposted__gte=first, transaction__dtposted__lt=end,
                                            subcategory_id__in=subs).values_list(
        'id', 'transaction__dtposted', 'amount', 'transaction__name', 'subcategory_id')
    for pid, day, amount, name, sid in parts:
        found[sid].append((f's{pid}', day, amount * sign[sid], name or ''))
    return subs, found


def _blocks(today, years_back):
    """The 12-month blocks to read, newest first: [(first month index, end month index)], and the first day overall."""
    end = _month_index(today)               # this month isn't over: it's excluded
    earliest = Transaction.objects.order_by('dtposted').values_list('dtposted', flat=True).first()
    if earliest is None:
        return [], None
    # A block counts only if the ledger covers all of it (its first month included)
    covered_from = _month_index(earliest)
    blocks = []
    for n in range(years_back):
        start = end - 12 * (n + 1)
        if start < covered_from:
            break
        blocks.append((start, start + 12))
    return blocks, blocks[-1][0] if blocks else None


def _yearly_bills(items):
    """Items that come back at a similar amount about 12 months apart: {item id}, and the latest of each as (amount, month, description)."""
    big = sorted((i for i in items if i[2] > 0), key=lambda i: i[1])
    yearly_ids, latest = set(), {}
    for i, a in enumerate(big):
        for b in big[i + 1:]:
            gap = _month_index(b[1]) - _month_index(a[1])
            if gap > 13:
                break
            if 11 <= gap <= 13 and abs(b[2] - a[2]) <= Decimal('0.25') * max(a[2], b[2]):
                yearly_ids.update((a[0], b[0]))
                latest[b[1].month] = (b[2], b[1].month, b[3])
    return yearly_ids, list(latest.values())


def _one_offs(items, blocks, yearly_ids):
    """Single large items with nothing comparable in another block."""
    monthly = defaultdict(Decimal)
    for _id, day, amount, _name in items:
        monthly[_month_index(day)] += amount
    months = [monthly.get(m, Decimal('0')) for first, end in blocks for m in range(first, end)]
    usual = Decimal(str(median(months))) if months else Decimal('0')
    threshold = max(usual * ONE_OFF_TIMES_USUAL, ONE_OFF_AT_LEAST)
    block_of = lambda day: next((n for n, (first, end) in enumerate(blocks) if first <= _month_index(day) < end), None)
    found = []
    for item in items:
        iid, day, amount, _name = item
        if iid in yearly_ids or amount < threshold:
            continue
        here = block_of(day)
        comparable = any(other[2] >= amount / 2 and block_of(other[1]) != here
                         for other in items if other[0] != iid)
        if not comparable:
            found.append(item)
    return found


def _known_recurring(sub_id, kind, year):
    """Recurring transactions set up for this subcategory during the year: [(name, amount a year)]."""
    first, last = date(year, 1, 1), date(year, 12, 31)
    known = []
    for rec in RecurringTransaction.objects.filter(subcategory_id=sub_id, is_active=True, start_date__lte=last):
        if rec.end_date and rec.end_date < first:
            continue
        amount = rec.amount if kind == 'INC' else -rec.amount
        if amount > 0:
            known.append((rec.name, amount * PER_YEAR.get(rec.frequency, 12)))
    return known


def suggest(year, years_back=3, recent_first=True, yearly_bills='as_paid', kinds=('EXP', 'INC'),
            rounding=1, today=None):
    """One suggestion per subcategory for the year, with its reasons and what it was based on."""
    today = today or date.today()
    if not 1 <= int(years_back) <= 3:
        raise SuggestionError('Use 1, 2 or 3 years of history.')
    if yearly_bills not in ('as_paid', 'spread'):
        raise SuggestionError('Yearly bills are budgeted "as_paid" or "spread".')
    if rounding not in ROUNDINGS:
        raise SuggestionError('Rounding must be 0, 1, 10 or 100.')
    kinds = [k for k in kinds if k in ('EXP', 'INC')]
    if not kinds:
        raise SuggestionError('Choose income, expenses, or both.')

    blocks, first_index = _blocks(today, int(years_back))
    if not blocks:
        raise SuggestionError('There isn\'t a full 12 months of history yet to suggest from.')
    first_day = date(*_from_index(first_index), 1)
    end_day = date(*_from_index(blocks[0][1]), 1)
    subs, items_by_sub = _items(kinds, first_day, end_day)
    labels = [f'{date(*_from_index(first), 1):%b %Y} – {date(*_from_index(end - 1), 1):%b %Y}' for first, end in blocks]
    weights = [Decimal(len(blocks) - n) for n in range(len(blocks))] if recent_first else [Decimal(1)] * len(blocks)
    existing = defaultdict(Decimal)
    for sid, amount in Budget.objects.filter(year=year, subcategory_id__in=subs).values_list('subcategory_id', 'amount'):
        existing[sid] += amount

    rows = []
    for sid, sub in subs.items():
        kind = sub.category.type
        items = items_by_sub.get(sid, [])
        known = _known_recurring(sid, kind, year)
        yearly_ids, yearly = _yearly_bills(items)
        one_offs = _one_offs(items, blocks, yearly_ids)
        left_out = {i[0] for i in one_offs}

        totals, kept_totals = [], []
        for first, end in blocks:
            inside = [i for i in items if first <= _month_index(i[1]) < end]
            totals.append(sum((i[2] for i in inside), Decimal('0')))
            kept_totals.append(sum((i[2] for i in inside if i[0] not in left_out), Decimal('0')))

        reasons = []
        level = sum(t * w for t, w in zip(kept_totals, weights)) / sum(weights)
        growths = [kept_totals[n] / kept_totals[n + 1] - 1 for n in range(len(kept_totals) - 1) if kept_totals[n + 1] > 0]
        steady = (len(growths) == len(kept_totals) - 1 and growths
                  and (all(g >= MIN_TREND for g in growths) or all(g <= -MIN_TREND for g in growths)))
        if not items:
            suggestion = Decimal('0')
            reasons.append('Nothing in the last 12 months' if len(blocks) == 1 else f'Nothing in the last {len(blocks)} years')
        elif steady:
            trend = max(min(sum(growths) / len(growths), MAX_TREND), -MAX_TREND)
            suggestion = max(kept_totals[0] * (1 + trend), Decimal('0'))
            word = 'rising' if trend > 0 else 'falling'
            reasons.append(f'{_money(kept_totals[0] / 12)}/month over the last 12 months, {word} about '
                           f'{abs(trend) * 100:.0f}% a year')
        else:
            suggestion = max(level, Decimal('0'))
            if len(blocks) == 1:
                reasons.append(f'{_money(level / 12)}/month over the last 12 months')
            else:
                how = ', recent years counting more' if recent_first else ''
                reasons.append(f'Averaged {_money(level / 12)}/month over {len(blocks)} years{how}')
        if one_offs:
            reasons.append('Leaves out ' + ', '.join(f'{_money(i[2])} ({i[3] or "no description"}, {i[1]:%b %Y})'
                                                     for i in one_offs))
        known_total = sum((amount for _name, amount in known), Decimal('0'))
        if known_total > suggestion:
            suggestion = known_total
            reasons.append('Recurring: ' + ', '.join(f'{name} {_money(amount / 12)}/month' for name, amount in known))

        # The months: yearly bills where they fall (or spread), the rest by the category's shape
        months = [Decimal('0')] * 12
        yearly_total = Decimal('0')
        if yearly:
            for amount, month, name in yearly:
                share = min(amount, suggestion - yearly_total)
                if share <= 0:
                    continue
                yearly_total += share
                if yearly_bills == 'as_paid':
                    months[month - 1] += share
                else:
                    for m in range(12):
                        months[m] += share / 12
            reasons.append('Yearly: ' + ', '.join(
                f'{name or "a bill"} {_money(amount)} ' + (f'in {MONTH_NAMES[month - 1]}' if yearly_bills == 'as_paid' else 'spread over the year')
                for amount, month, name in yearly))
        rest = max(suggestion - yearly_total, Decimal('0'))
        by_month = defaultdict(Decimal)
        for iid, day, amount, _name in items:
            if iid not in left_out and iid not in yearly_ids:
                by_month[day.month] += amount
        shape_total = sum((v for v in by_month.values() if v > 0), Decimal('0'))
        shares = {m: max(by_month.get(m, Decimal('0')), Decimal('0')) / shape_total for m in range(1, 13)} if shape_total > 0 else {}
        seasonal = bool(shares) and max(shares.values()) > SEASONAL_SHARE
        for m in range(12):
            months[m] += rest * shares[m + 1] if seasonal else rest / 12
        if seasonal:
            peak = max(shares, key=shares.get)
            reasons.append(f'Seasonal: most in {MONTH_NAMES[peak - 1]}, kept that way')
        months = [_round(m, rounding) for m in months]

        rows.append({
            'subcategory': sid, 'name': sub.name, 'category': sub.category.name, 'kind': kind,
            'history': [{'label': label, 'total': total.quantize(CENT)} for label, total in zip(labels, totals)],
            'suggestion': sum(months, Decimal('0')),
            'months': months,
            'reasons': reasons,
            'one_offs': [{'id': i[0], 'date': i[1], 'description': i[3], 'amount': i[2].quantize(CENT)} for i in one_offs],
            'existing_budget': existing.get(sid, Decimal('0')).quantize(CENT),
        })
    # Income first, then expenses, each in the categories' own order
    order = {sid: (sub.category.sort_order, sub.category.name.lower(), sub.name.lower()) for sid, sub in subs.items()}
    rows.sort(key=lambda r: (r['kind'] != 'INC', order[r['subcategory']]))
    return {'year': year, 'blocks': labels, 'rows': rows}


def apply(year, rows, replace=False):
    """
    Sets the budget for the chosen subcategories: rows is [{subcategory, months:
    12 amounts}]. Without replace, months that already have a budget are kept.
    """
    chosen = {}
    for row in rows:
        try:
            sid = int(row['subcategory'])
            months = [Decimal(str(m)) for m in row['months']]
        except (KeyError, TypeError, ValueError, ArithmeticError):
            raise SuggestionError('Each row needs a subcategory and 12 monthly amounts.')
        if len(months) != 12 or any(m < 0 for m in months):
            raise SuggestionError('Each row needs 12 monthly amounts, none below zero.')
        chosen[sid] = [m.quantize(CENT, ROUND_HALF_UP) for m in months]
    valid = set(SubCategory.objects.filter(pk__in=chosen, category__type__in=('EXP', 'INC')).values_list('pk', flat=True))
    if valid != set(chosen):
        raise SuggestionError('Budgets are for income and expense subcategories only.')
    set_count = kept = 0
    with db_transaction.atomic():
        if replace:
            Budget.objects.filter(year=year, subcategory_id__in=chosen).delete()
        else:
            # A 0 looks the same as no budget on screen: it's filled like an empty month
            Budget.objects.filter(year=year, subcategory_id__in=chosen, amount=0).delete()
        have = set(Budget.objects.filter(year=year, subcategory_id__in=chosen).values_list('subcategory_id', 'month'))
        new = []
        for sid, months in chosen.items():
            for month, amount in enumerate(months, start=1):
                if (sid, month) in have:
                    kept += 1
                elif amount > 0:
                    new.append(Budget(subcategory_id=sid, year=year, month=month, amount=amount))
        Budget.objects.bulk_create(new)
        set_count = len(new)
    return {'year': year, 'months_set': set_count, 'months_kept': kept}
