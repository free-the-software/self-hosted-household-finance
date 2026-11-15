# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import logging
import re
from decimal import Decimal
from django.db import transaction as db_transaction
from datetime import timedelta
from .models import (
    Account, Category, SubCategory, TransactionSplit,
    Transaction, StagedTransaction,
)
from .rules import active_rules, apply_rules


logger = logging.getLogger(__name__)


# --- MATCHING IMPORTED TRANSACTIONS TO THE LEDGER ---
# Modelled on Quicken's matching. A bank transaction is checked against the
# pending transactions entered by hand in the same account, strictest rule
# first. "days" below is the bank's date minus the entry's date, so a
# positive number means the bank posted it later.
#
# MATCH: the same amount, and
#   1. a similar payee, within 10 days either way, or
#   2. within 3 days either way, or
#   3. posted up to 15 days after the entry (or up to 3 days before it).
# NEAR: probably the same, but something differs, so the user decides:
#   4. the same amount, up to 30 days apart, or
#   5. a similar payee and an amount within 25% (a tip, a fuel pre-authorisation),
#      within 10 days either way, or
#   6. an amount within 5% (a fee, a rounded entry), within 5 days either
#      way, whatever the payee: what you call "ATM" the bank may call
#      "CASH ADVANCE COSTCO #0100".
# Nothing is ever matched without the user approving it in the Import Inbox.
#
# DUPLICATE: before any of that, a row is a duplicate when its bank ID is
# already in the ledger, or, because some banks issue new IDs when a statement
# is downloaded again (Fidelity has), when an already-imported transaction in
# the same account has the same amount and either the same date, the same
# bank memo (one with a reference number in it), or a similar payee within
# DUPLICATE_DAYS. Each imported transaction accounts for one row
# at most, so two identical coffees in a file against one in the ledger leave
# one of them new.
DUPLICATE_DAYS = 3
NEAR_DAYS = 30
NEAR_AMOUNT_SHARE = Decimal('0.25')       # rule 5, with a similar payee
CLOSE_AMOUNT_SHARE = Decimal('0.05')      # rule 6, any payee
MATCH_RULES = (
    # (match type, needs similar payee, how far the amounts may differ (a share
    #  of the larger; 0 means identical), earliest days, latest days, reason)
    (StagedTransaction.STATUS_MATCH, True, 0, -10, 10, 'same amount and payee'),
    (StagedTransaction.STATUS_MATCH, False, 0, -3, 3, 'same amount, within 3 days'),
    (StagedTransaction.STATUS_MATCH, False, 0, -3, 15, 'same amount, posted within 15 days of your entry'),
    (StagedTransaction.STATUS_NEAR, False, 0, -NEAR_DAYS, NEAR_DAYS, 'same amount, but {days} days apart'),
    (StagedTransaction.STATUS_NEAR, True, NEAR_AMOUNT_SHARE, -10, 10, 'same payee, but the amount differs by {difference}'),
    (StagedTransaction.STATUS_NEAR, False, CLOSE_AMOUNT_SHARE, -5, 5, 'the amount differs by {difference} and the payee by name'),
)
# Words in bank descriptions that say nothing about who was paid
PAYEE_NOISE = {
    'POS', 'SQ', 'TST', 'DEBIT', 'CREDIT', 'PURCHASE', 'PAYMENT', 'CARD', 'CHECKCARD', 'ACH', 'ONLINE',
    'THE', 'AND', 'INC', 'LLC', 'CORP', 'CO', 'STORE', 'WWW', 'COM', 'PAYPAL', 'RECURRING',
}


def payee_words(name):
    """The words of a payee name that identify it: "SQ *THE CORNER BAKERY #12" -> {"CORNER", "BAKERY"}."""
    words = re.findall(r'[A-Z]+', (name or '').upper().replace("'", ''))
    return {word for word in words if len(word) >= 3 and word not in PAYEE_NOISE}


def similar_payee(bank_name, entry_name):
    """True when the entry's name shares an identifying word with the bank's description."""
    return bool(payee_words(bank_name) & payee_words(entry_name))


class LedgerMatcher:
    """
    Finds the pending transaction an imported one matches, for a whole set of
    imported rows at once: the ledger is read in a few queries up front, not
    once per row. Each pending transaction is proposed to one row at most,
    the row it fits best.
    """

    def __init__(self, rows):
        """rows: (account_id, fitid, date) for every row that will be matched."""
        rows = [row for row in rows if row[0]]
        self.pending = {}
        self.imported = {}
        self.in_ledger = set()
        self.account_ids = set()
        # Names are compared as the renaming rules would leave them; the rules
        # and accounts are read only when a duplicate check needs them
        self._rules = self._accounts = None
        self._renamed = {}
        # Imported transactions already shown to be some row's duplicate
        self.duplicate_of = set()
        # Pending transactions another staged row has already been offered
        self.claimed = set(
            StagedTransaction.objects.filter(match_proposal__isnull=False).values_list('match_proposal_id', flat=True)
        )
        if not rows:
            return
        accounts = {account for account, _fitid, _date in rows}
        self.account_ids = accounts
        # Every (account, bank ID) already in the ledger, in one query
        self.in_ledger = set(Transaction.objects.filter(
            account_id__in=accounts, fitid__in={fitid for _account, fitid, _date in rows if fitid},
        ).values_list('account_id', 'fitid'))
        # Every pending transaction any of the rules could reach, in one query
        window = timedelta(days=NEAR_DAYS)
        for tx in Transaction.objects.filter(
            account_id__in=accounts, status='P',
            dtposted__range=(min(row[2] for row in rows) - window, max(row[2] for row in rows) + window),
        ).order_by('dtposted', 'id'):
            self.pending.setdefault(tx.account_id, []).append(tx)
        # Every transaction already imported from a bank file near these dates
        window = timedelta(days=DUPLICATE_DAYS)
        for tx in Transaction.objects.filter(
            account_id__in=accounts, dtposted__range=(min(row[2] for row in rows) - window, max(row[2] for row in rows) + window),
        ).exclude(status='P').exclude(fitid='').select_related('account').order_by('dtposted', 'id'):
            self.imported.setdefault(tx.account_id, []).append(tx)

    def renamed(self, name, amount, memo, account_id):
        """A name together with the one the renaming rules would give it."""
        key = (name, amount, memo, account_id)
        if key not in self._renamed:
            if self._rules is None:
                self._rules = active_rules()
                uses_account = any(cond.get('field') == 'account' for rule in self._rules for cond in (rule.conditions or []))
                self._accounts = Account.objects.in_bulk(self.account_ids) if uses_account else {}
            clean = apply_rules(self._rules, name, amount, memo, self._accounts.get(account_id))['name']
            self._renamed[key] = name if clean == name else f'{name} {clean}'
        return self._renamed[key]

    def duplicate_candidates(self, account_id, fitid, day, amount, name, memo=''):
        """
        Already-imported transactions this row repeats, though its bank ID is
        new, as (score, transaction, reason). A lower score is likelier: the
        same bank memo first, then a similar payee, then only the same date.
        """
        memo = (memo or '').strip().upper()
        found = []
        for tx in self.imported.get(account_id, ()):
            if tx.id in self.duplicate_of or tx.trnamt != Decimal(amount) or tx.fitid == fitid:
                continue
            days = abs((day - tx.dtposted).days)
            if days > DUPLICATE_DAYS:
                continue
            # The bank's memo is never renamed, so the same memo is the best
            # sign; a rule or a match may have renamed the payee, and the
            # bank's wording may be in the memo
            # Only a memo carrying a reference number counts: a generic one
            # like Fidelity's "DIRECT DEBIT" is shared by unrelated rows
            same_memo = bool(re.search(r'\d{4}', memo)) and memo == (tx.bank_memo or '').strip().upper()
            similar = similar_payee(
                # Both sides as the renaming rules would leave them: the row may
                # not have been renamed yet, and the ledger's copy may have been
                # renamed by a rule made since it was imported
                self.renamed(name, amount, memo, account_id),
                f'{self.renamed(tx.name, tx.trnamt, tx.bank_memo, account_id)} {tx.bank_memo}',
            )
            if not (same_memo or similar or days == 0):
                continue
            found.append(((0 if same_memo else 1 if similar else 2, days, tx.id), tx,
                          f'already imported on {tx.dtposted} as "{tx.name}"; the bank gave it a new ID'))
        return found

    def candidates(self, account_id, fitid, day, amount, name):
        """
        Every pending entry this row could match, as (score, entry, match
        type, reason). A lower score is a better pair: a stricter rule first,
        then fewer days apart, then a smaller difference in amount.
        """
        amount = Decimal(amount)
        # Quicken's first rule: the bank's own ID is already in the ledger, so
        # this is a duplicate. Only a full match can override that (banks have
        # been seen to reuse an ID); a near match never does.
        rules = MATCH_RULES
        if self.is_duplicate(account_id, fitid):
            rules = [rule for rule in MATCH_RULES if rule[0] == StagedTransaction.STATUS_MATCH]

        found = []
        for tx in self.pending.get(account_id, ()):
            if tx.id in self.claimed:
                continue
            days = (day - tx.dtposted).days
            difference = abs(tx.trnamt - amount)
            for rank, (match_type, needs_payee, amount_share, earliest, latest, reason) in enumerate(rules):
                if not earliest <= days <= latest:
                    continue
                if not amount_share:
                    if tx.trnamt != amount:
                        continue
                else:
                    # Same direction, and no more than the allowed share apart
                    # (an identical amount was already tried by the rules above)
                    if (tx.trnamt < 0) != (amount < 0) or tx.trnamt == amount:
                        continue
                    if difference > amount_share * max(abs(tx.trnamt), abs(amount)):
                        continue
                if needs_payee and not similar_payee(name, tx.name):
                    continue
                # The first rule this pair meets is its best
                found.append(((rank, abs(days), difference, tx.id), tx, match_type,
                              reason.format(days=abs(days), difference=f'{difference:.2f}')))
                break
        return found

    def is_duplicate(self, account_id, fitid):
        return bool(fitid) and (account_id, fitid) in self.in_ledger

    def match_all(self, rows):
        """
        Matches several rows together. rows: (account_id, fitid, date, amount,
        name, bank memo); the memo may be left off. Returns one (match type, proposed entry or None, reason) per
        row, in the same order.

        The best pairs across all the rows are settled first, so an entry goes
        to the row it fits best, not to whichever row happens to be looked at
        first. (An ATM withdrawal entered as -99.95 on the 19th belongs to the
        bank's -100.00 on the 19th, not to a -95.00 cheque five days earlier
        that is also just within the limits.)
        """
        results = [None] * len(rows)

        # Duplicates first, as Quicken does. An imported transaction found by
        # its own bank ID cannot also be the duplicate of another row.
        for account_id, fitid, *_rest in rows:
            if account_id and self.is_duplicate(account_id, fitid):
                self.duplicate_of.update(tx.id for tx in self.imported.get(account_id, ()) if tx.fitid == fitid)
        duplicates = []
        for index, row in enumerate(rows):
            if row[0] and not self.is_duplicate(row[0], row[1]):
                for score, tx, reason in self.duplicate_candidates(*row):
                    duplicates.append((score, index, tx, reason))
        duplicates.sort(key=lambda pair: (pair[0], pair[1]))
        for _score, index, tx, reason in duplicates:
            if results[index] is None and tx.id not in self.duplicate_of:
                self.duplicate_of.add(tx.id)
                results[index] = (StagedTransaction.STATUS_DUPE, None, reason)

        pairs = []
        for index, row in enumerate(rows):
            if row[0] and results[index] is None:
                for score, tx, match_type, reason in self.candidates(*row[:5]):
                    pairs.append((score, index, tx, match_type, reason))
        pairs.sort(key=lambda pair: (pair[0], pair[1]))

        for _score, index, tx, match_type, reason in pairs:
            if results[index] is None and tx.id not in self.claimed:
                self.claimed.add(tx.id)
                results[index] = (match_type, tx, reason)

        for index, (account_id, fitid, *_rest) in enumerate(rows):
            if results[index] is None:
                if account_id and self.is_duplicate(account_id, fitid):
                    results[index] = (StagedTransaction.STATUS_DUPE, None, 'already in your ledger')
                else:
                    results[index] = (StagedTransaction.STATUS_NEW, None, '')
        return results

    def match(self, account_id, fitid, day, amount, name):
        """Matches a single row; see match_all."""
        return self.match_all([(account_id, fitid, day, amount, name)])[0]


def reclassify_staged(staged_qs):
    """
    Re-checks staged transactions still marked NEW against the ledger, using
    the same rules as stage_import. Needed because that check is skipped for
    files from an unknown account: the rows stay NEW when the account is
    mapped later, even if they are already in the ledger, and because the
    ledger may have changed since the file was staged.
    """
    # Full matches stay, unless the ledger has moved on since: their bank
    # transaction has come in another way (the same file staged twice and the
    # other copy approved), or the entry they pair with is gone or no longer
    # pending. Those are weighed again with the rest. New rows, near matches
    # (only suggestions) and duplicates are always weighed again together, so
    # a better pair that has since appeared (an entry added by hand, another
    # file imported) wins, and two rows are never both called the duplicate
    # of one transaction.
    rows = list(
        staged_qs.filter(account__isnull=False).exclude(match_type='').select_related('match_proposal').order_by('date', 'id')
    )
    matches = [stx for stx in rows if stx.match_type == StagedTransaction.STATUS_MATCH]
    in_ledger = set(Transaction.objects.filter(
        account_id__in={stx.account_id for stx in matches}, fitid__in={stx.fitid for stx in matches if stx.fitid},
    ).values_list('account_id', 'fitid')) if matches else set()
    staged = []
    for stx in rows:
        if stx.match_type == StagedTransaction.STATUS_MATCH:
            stale = (stx.fitid and (stx.account_id, stx.fitid) in in_ledger) \
                or stx.match_proposal is None or stx.match_proposal.status != Transaction.STATUS_PENDING
            if not stale:
                continue
            stx.match_type, stx.match_proposal, stx.match_reason = StagedTransaction.STATUS_NEW, None, ''
        staged.append(stx)
    if not staged:
        return
    matcher = LedgerMatcher([(stx.account_id, stx.fitid, stx.date) for stx in staged])
    # Their own current proposals are free to be handed out again
    matcher.claimed -= {stx.match_proposal_id for stx in staged}
    results = matcher.match_all([(stx.account_id, stx.fitid, stx.date, stx.amount, stx.name, stx.bank_memo) for stx in staged])
    for stx, (match_type, proposal, reason) in zip(staged, results):
        proposal_id = proposal.id if proposal else None
        if (stx.match_type, stx.match_proposal_id, stx.match_reason) == (match_type, proposal_id, reason):
            continue
        stx.match_type, stx.match_proposal, stx.match_reason = match_type, proposal, reason
        stx.save(update_fields=['match_proposal', 'match_type', 'match_reason'])


# --- RECEIPT MATCHING ---
# A card charge can post a couple of days either side of the receipt's date
RECEIPT_MATCH_DAYS_BEFORE = 2
RECEIPT_MATCH_DAYS_AFTER = 5
# How far above the receipt's total a transaction may be and still be offered
# as a possible match. The bank's figure is the one that counts; a receipt
# often shows less than was finally charged.
RECEIPT_MATCH_OVER_PERCENT = 30


def receipt_match_window(receipt_date):
    return (receipt_date - timedelta(days=RECEIPT_MATCH_DAYS_BEFORE),
            receipt_date + timedelta(days=RECEIPT_MATCH_DAYS_AFTER))


def suggest_receipt_matches(receipt, limit=5):
    """
    Purchases that could be this receipt's transaction: close to its date, for
    the receipt's total or somewhat more, and not already holding a receipt.
    Closest amount first. Only suggestions; nothing is linked here.
    """
    if not receipt.date or not receipt.total_amount or receipt.total_amount <= 0:
        return Transaction.objects.none()
    total = Decimal(receipt.total_amount)
    most = total * (100 + RECEIPT_MATCH_OVER_PERCENT) / 100
    # Purchases are negative, so "the total or more" runs from -most to -total
    return Transaction.objects.filter(
        dtposted__range=receipt_match_window(receipt.date),
        trnamt__range=(-most, -total),
        receipt__isnull=True,
    ).select_related('account').order_by('-trnamt', 'dtposted')[:limit]


# --- Tax splitter ---
def tax_split_problem(transaction, receipt):
    """Why this receipt's tax cannot be split out of the transaction, or None if it can."""
    if transaction is None:
        return "This receipt is not linked to a transaction."
    if not receipt.tax_amount or receipt.tax_amount <= 0:
        return "This receipt has no tax amount."
    if transaction.splits.exists():
        return "This transaction is already split."
    # We cannot create a 'Base Amount' split if we don't know what the base category is.
    if not transaction.subcategory:
        return "This transaction has no category yet. Give it one first, so the rest of the amount has somewhere to go."
    if Decimal(receipt.tax_amount) >= abs(transaction.trnamt):
        return "The tax amount is as large as the whole transaction. Check the amounts."
    return None


def apply_tax_split(transaction, receipt):
    """
    Splits a transaction into 'Base Amount' and 'Sales Tax' using the tax
    amount on the receipt. Never called automatically: OCR misreads receipts
    often enough that a person must review the amount and ask for the split.
    Returns (done, message).
    """
    problem = tax_split_problem(transaction, receipt)
    if problem:
        return False, problem

    try:
        with db_transaction.atomic():
            # The subcategory doing the sales-tax job, wherever it is (finance/starter.py ROLES)
            from .starter import subcategory_for
            sales_tax_sub = subcategory_for('sales_tax')

            # The receipt's tax is positive. The split takes the transaction's
            # direction: negative for a purchase (-107.00 -> -7.00 and -100.00),
            # positive for a refund.
            direction = -1 if transaction.trnamt < 0 else 1
            tax_split_amount = Decimal(receipt.tax_amount) * direction
            base_amount = transaction.trnamt - tax_split_amount

            TransactionSplit.objects.create(
                transaction=transaction, subcategory=sales_tax_sub, amount=tax_split_amount,
                memo=f"Tax from Receipt ({receipt.original_filename})"
            )
            # The rest keeps the transaction's own category
            TransactionSplit.objects.create(
                transaction=transaction, subcategory=transaction.subcategory, amount=base_amount, memo="Base amount"
            )
        return True, "Transaction split."
    except Exception:
        logger.exception("Tax split failed for transaction %s", transaction.pk)
        return False, "The split could not be saved because of a server error."
