# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
"""
Renaming rules: the one place that decides whether a rule matches a
transaction. The importer, the Import Inbox, the rule preview and "apply to
history" all use it, so a rule behaves the same everywhere.

How a rule is read:
- Text is compared ignoring case, repeated spaces and HTML escapes, so
  "SALT &amp; PEPPER" and "Salt  & pepper" are the same.
- An amount is compared by its size, whatever its sign: "exact 31.40"
  matches a $31.40 charge, which the ledger stores as -31.40.
- "Memo" looks at both the bank's memo and your own.
- Rules are tried from the top of the list and the first that matches
  wins, like an access list. The user arranges the list in Settings → Rules;
  a new rule is placed just above the first rule that would otherwise catch
  its transactions, so it is not hidden from the start.
"""
import html
import re
from decimal import Decimal, InvalidOperation

from django.db.models import F
from django.utils import timezone

TEXT_FIELDS = ('name', 'description', 'personal_memo', 'account')
FIELDS = TEXT_FIELDS + ('amount',)
TEXT_OPERATORS = ('contains', 'exact', 'startswith', 'endswith')
AMOUNT_OPERATORS = ('exact', 'gt', 'lt')
FIELD_LABELS = {'name': 'Merchant', 'description': 'Merchant', 'personal_memo': 'Memo', 'account': 'Account', 'amount': 'Amount'}


def normalize(text):
    """Text as rules compare it: no HTML escapes, single spaces, lower case."""
    return re.sub(r'\s+', ' ', html.unescape(str(text or ''))).strip().lower()


def as_amount(value):
    """The size of an amount, or None if it is not a number."""
    try:
        amount = Decimal(str(value).replace('$', '').replace(',', '').strip())
    except (InvalidOperation, ValueError):
        return None
    return abs(amount) if amount.is_finite() else None


class Facts:
    """What a rule can look at on one transaction."""

    def __init__(self, name='', amount=None, memo='', account=''):
        self.name = normalize(name)
        self.amount = as_amount(amount) if amount is not None else None
        self.memo = normalize(memo)
        # An Account, or just its name
        self.account = normalize(getattr(account, 'name', account) or '')

    @classmethod
    def of_transaction(cls, tx):
        """Facts about a ledger transaction (both memos count as the memo)."""
        return cls(tx.name, tx.trnamt, f'{tx.bank_memo or ""} {tx.personal_memo or ""}', tx.account.name if tx.account_id else '')


def condition_problem(cond):
    """Why a condition cannot be used, or None if it is fine."""
    if not isinstance(cond, dict):
        return 'Each condition needs a field, an operator and a value.'
    field, operator = cond.get('field', 'name'), cond.get('operator', 'contains')
    value = str(cond.get('value', '')).strip()
    if field not in FIELDS:
        return f'Unknown field: {field}.'
    if field == 'amount':
        if operator not in AMOUNT_OPERATORS:
            return 'An amount condition must be exact, greater than or less than.'
        if as_amount(value) is None:
            return 'An amount condition needs a number.'
    elif operator not in TEXT_OPERATORS:
        return f'Unknown operator for text: {operator}.'
    if not value:
        return f'The {FIELD_LABELS[field].lower()} condition needs a value.'
    return None


def conditions_problem(conditions):
    if not isinstance(conditions, list) or not conditions:
        return 'A rule needs at least one condition.'
    for cond in conditions:
        problem = condition_problem(cond)
        if problem:
            return problem
    return None


def condition_matches(cond, facts):
    field = cond.get('field', 'name')
    operator = cond.get('operator', 'contains')
    if field == 'amount':
        wanted = as_amount(cond.get('value'))
        if wanted is None or facts.amount is None:
            return False
        return {'exact': facts.amount == wanted, 'gt': facts.amount > wanted, 'lt': facts.amount < wanted}.get(operator, False)
    text = {'name': facts.name, 'description': facts.name, 'personal_memo': facts.memo, 'account': facts.account}.get(field, '')
    wanted = normalize(cond.get('value'))
    if not wanted:
        return False
    if operator == 'contains':
        return wanted in text
    if operator == 'exact':
        return wanted == text
    if operator == 'startswith':
        return text.startswith(wanted)
    if operator == 'endswith':
        return text.endswith(wanted)
    return False


def rule_matches(conditions, logic_operator, facts):
    if not conditions:
        return False
    results = (condition_matches(cond, facts) for cond in conditions)
    return any(results) if logic_operator == 'OR' else all(results)


def sort_key(rule):
    """The order rules are tried in: their place in the list."""
    return (rule.position, rule.pk or 0)


def ordered(rules):
    return sorted(rules, key=sort_key)


def active_rules():
    from .models import TransactionRule
    return ordered(TransactionRule.objects.filter(is_active=True).select_related('subcategory'))


def first_match(rules, facts):
    """The first rule, in order, that matches; rules must already be ordered."""
    for rule in rules:
        if rule_matches(rule.conditions, rule.logic_operator, facts):
            return rule
    return None


def apply_rules(rules, name, amount, memo, account):
    """
    Runs a bank transaction through the rules. Returns {'name': the name the
    matching rule gives it (or its own), 'subcategory': that rule's category
    or None, 'rule': the rule or None}.
    """
    rule = first_match(rules, Facts(name, amount, memo, account))
    if rule is None:
        return {'name': name or '', 'subcategory': None, 'rule': None}
    return {'name': rule.clean_merchant_name or name or '', 'subcategory': rule.subcategory, 'rule': rule}


def record_hits(counts):
    """Adds to each rule's use count. counts: {rule id: times used}."""
    from .models import TransactionRule
    now = timezone.now()
    for rule_id, times in counts.items():
        if rule_id and times:
            TransactionRule.objects.filter(pk=rule_id).update(hit_count=F('hit_count') + times, last_matched=now)


# --- Arranging the list ---

def _renumber(rules):
    """Saves the given order as positions 1, 2, 3..."""
    from .models import TransactionRule
    changed = []
    for position, rule in enumerate(rules, start=1):
        if rule.position != position:
            rule.position = position
            changed.append(rule)
    TransactionRule.objects.bulk_update(changed, ['position'])


def move(rule, to=None, before=None):
    """
    Moves a rule to a place in the list: `to` is the 1-based place it should
    take (clamped to the list), or `before` another rule's id. The whole list
    is renumbered without gaps.
    """
    from django.db import transaction
    from .models import TransactionRule
    with transaction.atomic():
        others = [r for r in ordered(TransactionRule.objects.select_for_update()) if r.pk != rule.pk]
        if before is not None:
            index = next((i for i, other in enumerate(others) if other.pk == before), len(others))
        else:
            index = min(max(int(to) - 1, 0), len(others))
        others.insert(index, rule)
        _renumber(others)


def place_new(rule):
    """
    Puts a newly made rule in the list: just above the first active rule that
    would otherwise always catch its transactions, or at the bottom.
    """
    from .models import TransactionRule
    others = [r for r in ordered(TransactionRule.objects.exclude(pk=rule.pk)) if r.is_active]
    catcher = next((other for other in others if _covers(other, rule)), None)
    if catcher is None:
        last = TransactionRule.objects.exclude(pk=rule.pk).order_by('-position').values_list('position', flat=True).first()
        rule.position = (last or 0) + 1
        rule.save(update_fields=['position'])
    else:
        move(rule, before=catcher.pk)


# --- Finding rules that duplicate or hide others, for the Rules tab ---

def _signature(rule):
    conditions = sorted(
        (('name' if cond.get('field') == 'description' else cond.get('field', 'name')), cond.get('operator', 'contains'), normalize(cond.get('value')))
        for cond in (rule.conditions or []) if isinstance(cond, dict)
    )
    return (rule.logic_operator if len(conditions) > 1 else 'AND', tuple(conditions))


def _implies(later, earlier):
    """True when any text matching condition `later` also matches `earlier`."""
    field_of = lambda cond: 'name' if cond.get('field') == 'description' else cond.get('field', 'name')
    if field_of(later) != field_of(earlier):
        return False
    if field_of(later) == 'amount':
        return (later.get('operator'), as_amount(later.get('value'))) == (earlier.get('operator'), as_amount(earlier.get('value')))
    a, b = normalize(earlier.get('value')), normalize(later.get('value'))
    op_a, op_b = earlier.get('operator', 'contains'), later.get('operator', 'contains')
    if op_a == 'contains':
        return a in b      # whatever contains b, or is b, contains a
    if op_a == 'exact':
        return op_b == 'exact' and a == b
    if op_a == 'startswith':
        return op_b in ('startswith', 'exact') and b.startswith(a)
    if op_a == 'endswith':
        return op_b in ('endswith', 'exact') and b.endswith(a)
    return False


def _all_required(rule):
    """The conditions that must all hold, or None for an OR rule with several."""
    conditions = [cond for cond in (rule.conditions or []) if isinstance(cond, dict)]
    if len(conditions) > 1 and rule.logic_operator == 'OR':
        return None
    return conditions


def _covers(broader, narrower):
    """True when every transaction `narrower` matches is also matched by `broader`."""
    required, conditions = _all_required(broader), _all_required(narrower)
    return bool(required) and bool(conditions) and all(any(_implies(cond, need) for cond in conditions) for need in required)


def _same_result(a, b):
    return (normalize(a.clean_merchant_name), a.subcategory_id) == (normalize(b.clean_merchant_name), b.subcategory_id)


def analyse(rules):
    """
    For the Rules tab: each rule's place in the order, and whether it
    - repeats an earlier rule (duplicate_of),
    - can never be reached because an earlier one always matches first
      (shadowed_by), or
    - is redundant: a broader rule further down gives the same name and
      category anyway, and nothing in between would catch its transactions
      (redundant_with).
    Returns {rule id: {'order', 'duplicate_of', 'shadowed_by', 'redundant_with'}}.
    """
    rules = ordered(rules)
    active = [rule for rule in rules if rule.is_active]
    seen, result = {}, {}
    for index, rule in enumerate(rules):
        info = {'order': index + 1, 'duplicate_of': None, 'shadowed_by': None, 'redundant_with': None}
        signature = _signature(rule)
        if signature in seen and rule.is_active:
            info['duplicate_of'] = seen[signature]
        elif rule.is_active:
            seen[signature] = {'id': rule.pk, 'rule_name': rule.rule_name}
            later = _all_required(rule)
            if later:
                for earlier in rules[:index]:
                    if earlier.is_active and _covers(earlier, rule):
                        info['shadowed_by'] = {'id': earlier.pk, 'rule_name': earlier.rule_name}
                        break
                if not info['shadowed_by']:
                    # The first later rule that would also catch this one's
                    # transactions decides what removing it would change
                    position = active.index(rule)
                    for later_rule in active[position + 1:]:
                        # Its own duplicate is flagged on the duplicate instead;
                        # flagging both would invite deleting both
                        if _signature(later_rule) == signature:
                            continue
                        if _covers(later_rule, rule):
                            if _same_result(later_rule, rule):
                                info['redundant_with'] = {'id': later_rule.pk, 'rule_name': later_rule.rule_name}
                            break
        result[rule.pk] = info
    return result
