# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
"""
Renaming, moving and deleting categories and subcategories.

Everything points at a subcategory by its id, so a rename or a move is
safe: history, budgets and rules simply show the new name. Deleting is
where care is needed, because ten kinds of records use a subcategory:
transactions and their split lines, renaming rules, budgets, recurring
bills, Import Inbox rows (and the split lines saved inside them), and the
four category choices on a loan. The database won't let a subcategory go
while any of them point at it (on_delete=PROTECT); a delete here first
moves them all to a replacement, which is also how two subcategories are
merged, or leaves them uncategorized.

A subcategory with a role (finance/starter.py ROLES) does a job for the app,
such as a loan payment's principal. Its role moves with it to a
replacement, and it must stay in a category of its job's kind.
"""
from django.db import transaction
from django.db.models import Q

from .models import (
    Budget, Category, LoanDetail, RecurringTransaction, StagedTransaction, SubCategory, Transaction,
    TransactionRule, TransactionSplit,
)
from .starter import ROLE_LABELS, ROLES

LOAN_FIELDS = ('interest_category', 'property_tax_category', 'homeowners_insurance_category', 'pmi_category')
KIND_NAMES = {'INC': 'an Income', 'EXP': 'an Expense', 'XFR': 'a Transfer'}


class CategoryError(Exception):
    """Something the user needs to choose differently; the message says what."""


def _inbox_rows_with_split_lines_in(sub):
    """Import Inbox rows whose saved split lines use this subcategory (kept as ids, not links)."""
    return [row for row in StagedTransaction.objects.exclude(splits=[]).only('id', 'splits')
            if any(line.get('subcategory') == sub.pk for line in row.splits or [])]


def usage(sub):
    """What uses a subcategory, counted for the delete window."""
    loans = LoanDetail.objects.filter(Q(**{LOAN_FIELDS[0]: sub}) | Q(**{LOAN_FIELDS[1]: sub})
                                      | Q(**{LOAN_FIELDS[2]: sub}) | Q(**{LOAN_FIELDS[3]: sub})).count()
    return {
        'transactions': Transaction.objects.filter(subcategory=sub).count(),
        'split_lines': TransactionSplit.objects.filter(subcategory=sub).count(),
        'rules': TransactionRule.objects.filter(subcategory=sub).count(),
        'budget_months': Budget.objects.filter(subcategory=sub).exclude(amount=0).count(),
        'recurring': RecurringTransaction.objects.filter(subcategory=sub).count(),
        'inbox_rows': StagedTransaction.objects.filter(subcategory=sub).count() + len(_inbox_rows_with_split_lines_in(sub)),
        'loans': loans,
        'role': ROLE_LABELS.get(sub.role) if sub.role else None,
    }


def check_kind(role, category):
    """A subcategory with a job must stay in a category of the job's kind."""
    if role and category.type != ROLES[role][2]:
        raise CategoryError(
            f"This subcategory is used for {ROLE_LABELS[role]}, which must stay in {KIND_NAMES[ROLES[role][2]]} category.")


def can_leave_uncategorized(sub):
    """Why not, or None: split lines and jobs need somewhere to go."""
    if sub.role:
        return f'The app uses it for {ROLE_LABELS[sub.role]}: choose where that should go instead.'
    if TransactionSplit.objects.filter(subcategory=sub).exists() or _inbox_rows_with_split_lines_in(sub):
        return 'Split lines use it, and every split line needs a category: choose where they should go.'
    return None


def delete_subcategory(sub, replacement=None):
    """
    Deletes a subcategory. With a replacement, everything that used it moves
    there (budgets are added into the replacement's, month by month); without
    one, transactions, recurring bills, inbox rows and loans are left
    uncategorized and its rules and budgets are deleted. All or nothing.
    """
    with transaction.atomic():
        sub = SubCategory.objects.select_for_update().get(pk=sub.pk)
        if replacement is None:
            reason = can_leave_uncategorized(sub)
            if reason:
                raise CategoryError(reason)
            Transaction.objects.filter(subcategory=sub).update(subcategory=None)
            RecurringTransaction.objects.filter(subcategory=sub).update(subcategory=None)
            StagedTransaction.objects.filter(subcategory=sub).update(subcategory=None)
            for field in LOAN_FIELDS:
                LoanDetail.objects.filter(**{field: sub}).update(**{field: None})
            TransactionRule.objects.filter(subcategory=sub).delete()
            Budget.objects.filter(subcategory=sub).delete()
            sub.delete()
            return

        replacement = SubCategory.objects.select_for_update().get(pk=replacement.pk)
        if replacement.pk == sub.pk:
            raise CategoryError('Choose a different subcategory to move everything to.')
        if sub.role and replacement.role:
            raise CategoryError(
                f'Both do a job for the app ({ROLE_LABELS[sub.role]}, {ROLE_LABELS[replacement.role]}). '
                'Choose a subcategory that has none.')
        check_kind(sub.role, replacement.category)

        Transaction.objects.filter(subcategory=sub).update(subcategory=replacement)
        TransactionSplit.objects.filter(subcategory=sub).update(subcategory=replacement)
        TransactionRule.objects.filter(subcategory=sub).update(subcategory=replacement)
        RecurringTransaction.objects.filter(subcategory=sub).update(subcategory=replacement)
        StagedTransaction.objects.filter(subcategory=sub).update(subcategory=replacement)
        for row in _inbox_rows_with_split_lines_in(sub):
            row.splits = [{**line, 'subcategory': replacement.pk} if line.get('subcategory') == sub.pk else line
                          for line in row.splits]
            row.save(update_fields=['splits'])
        for field in LOAN_FIELDS:
            LoanDetail.objects.filter(**{field: sub}).update(**{field: replacement})
        for budget in Budget.objects.filter(subcategory=sub):
            theirs, made = Budget.objects.get_or_create(
                subcategory=replacement, year=budget.year, month=budget.month, defaults={'amount': budget.amount})
            if not made:
                theirs.amount += budget.amount
                theirs.save(update_fields=['amount'])
            budget.delete()
        role, sub.role = sub.role, None
        sub.save(update_fields=['role'])
        sub.delete()
        if role:
            replacement.role = role
            replacement.save(update_fields=['role'])


def delete_category(category, move_to=None, replacement=None, uncategorize=False):
    """
    Deletes a category. Its subcategories either move to another category
    (merging into one there with the same name), or are deleted, sending what
    used them to one replacement subcategory or leaving it uncategorized.
    """
    with transaction.atomic():
        subs = list(category.subcategories.all())
        if subs:
            if move_to is not None:
                if move_to.pk == category.pk:
                    raise CategoryError('Choose a different category to move the subcategories to.')
                for sub in subs:
                    same_name = move_to.subcategories.filter(name__iexact=sub.name).first()
                    if same_name:
                        delete_subcategory(sub, same_name)
                    else:
                        check_kind(sub.role, move_to)
                        sub.category = move_to
                        sub.save(update_fields=['category'])
            elif replacement is not None:
                if replacement.category_id == category.pk:
                    raise CategoryError("Choose a replacement outside the category you're deleting.")
                for sub in subs:
                    delete_subcategory(sub, replacement)
            elif uncategorize:
                for sub in subs:
                    delete_subcategory(sub)
            else:
                raise CategoryError('Choose where its subcategories should go.')
        Category.objects.filter(pk=category.pk).delete()


def category_usage(category):
    """The totals for every subcategory in a category."""
    totals = {}
    for sub in category.subcategories.all():
        for key, value in usage(sub).items():
            if key == 'role':
                if value:
                    totals.setdefault('roles', []).append(value)
            else:
                totals[key] = totals.get(key, 0) + value
    totals['subcategories'] = category.subcategories.count()
    return totals
