# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
"""
The order accounts are listed in, in every drop-down and list: open
accounts before closed ones; then by type, everyday accounts first
(checking, savings, credit cards, cash, then investments and loans); within
a type, the most used first (transactions in the last 90 days); then by
name. Without this the database's own order came back, which put the newest
account first.
"""
from datetime import timedelta

from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.db.models.functions import Lower, Trim, Upper
from django.utils import timezone

# Account types, as written in Settings, imports and older installs
TYPE_GROUPS = (
    ('CHECKING',),
    ('SAVINGS', 'MONEYMRKT', 'MONEY MARKET'),
    ('CREDIT CARD', 'CREDITCARD', 'CREDITLINE', 'CREDIT LINE'),
    ('CASH',),
    ('INVESTMENT', 'BROKERAGE', 'RETIREMENT'),
    ('LOAN', 'MORTGAGE', 'AUTO LOAN'),
)
RECENT_DAYS = 90


def ordered(queryset, today=None):
    today = today or timezone.localdate()
    since = today - timedelta(days=RECENT_DAYS)
    return queryset.annotate(
        _type_key=Upper(Trim('account_type')),
    ).annotate(
        _closed=Case(When(Q(closed_date__isnull=False) | Q(active=False), then=Value(1)),
                     default=Value(0), output_field=IntegerField()),
        _type_rank=Case(*[When(_type_key__in=names, then=Value(rank)) for rank, names in enumerate(TYPE_GROUPS)],
                        default=Value(len(TYPE_GROUPS)), output_field=IntegerField()),
        _recent=Count('transactions', filter=Q(transactions__dtposted__gte=since)),
    ).order_by('_closed', '_type_rank', '-_recent', Lower('name'), 'pk')
