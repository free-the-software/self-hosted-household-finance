# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
"""
Reading bank CSV files, whichever bank they come from.

A CSV has no fixed layout, so its columns are recognised by their headings:
a date, a description, and either one amount column or separate money-out
and money-in columns. A running balance, a memo and a reference number are
used when there are any. Rows above the headings (titles, blank lines) and
rows that aren't transactions (totals, legal text at the end) are skipped.
Dates are read as month-first (US) or year-first; amounts may carry $,
commas, a minus sign or brackets.

Fidelity's download has a layout of its own (Run Date, Action, Description,
Amount ($), Cash Balance ($)) and is read as it always has been, so its
transactions keep the same ids from one download to the next.

Which way the money went. Most files write money out as negative, but some
card issuers write purchases as positive, and some files give every amount
unsigned with the direction in a column of its own. The file's evidence is
gathered here (sign_facts) and weighed with what's known about the account
in decide_signs; when it doesn't settle the question, the Import Inbox asks
once, with a row from the file as the example, and the answer is kept with
the account.

A CSV rarely says which account it's for. Fidelity's file name carries the
account number; otherwise the file name, without its dates, stands in for
it ("csv:..."). The first file from an account waits in the Import Inbox
until you pick its account, and the choice is remembered (AccountAlias).
"""
import csv
import hashlib
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

# The headings recognised for each kind of column, after tidying (lower case,
# no punctuation, nothing in brackets). Where a file has more than one, the
# earlier in the list wins: the date the bank posted it, as in QFX files.
HEADINGS = {
    'date': ['post date', 'posted date', 'posting date', 'date posted', 'date', 'transaction date', 'trans date',
             'transaction posted date', 'effective date', 'run date', 'trade date'],
    'description': ['description', 'payee', 'name', 'merchant', 'merchant name', 'transaction description',
                    'details', 'original description', 'narrative'],
    'amount': ['amount', 'transaction amount', 'amount usd', 'net amount', 'value'],
    'debit': ['debit', 'debits', 'debit amount', 'withdrawal', 'withdrawals', 'withdrawal amount', 'money out',
              'paid out', 'charge', 'charges'],
    'credit': ['credit', 'credits', 'credit amount', 'deposit', 'deposits', 'deposit amount', 'money in', 'paid in'],
    'balance': ['balance', 'running balance', 'cash balance', 'available balance', 'ledger balance', 'running bal'],
    'memo': ['memo', 'notes', 'note', 'extended description', 'additional details'],
    'reference': ['reference', 'reference number', 'reference no', 'ref', 'transaction id', 'transaction reference',
                  'fitid', 'confirmation number'],
    # Which way each row went, for files whose amounts carry no sign (or as a check on those that do)
    'direction': ['credit debit indicator', 'debit credit indicator', 'debit credit', 'credit debit', 'dr cr', 'cr dr',
                  'indicator', 'transaction type', 'type'],
}
# Words in a direction column. "Payment" is money in on a card statement.
MONEY_OUT_WORDS = {'debit', 'dr', 'sale', 'purchase', 'withdrawal', 'charge', 'fee', 'fees', 'check'}
MONEY_IN_WORDS = {'credit', 'cr', 'payment', 'return', 'refund', 'deposit', 'reversal'}
# A card payment, by its description
PAYMENT = re.compile(r'\b(payment|autopay|auto pay|thank you)\b', re.IGNORECASE)
# How many of the rows that can be checked must agree for the file's evidence to settle it
SURE = 0.9
DATE_FORMATS = ['%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d', '%Y/%m/%d', '%m-%d-%Y', '%m-%d-%y', '%d/%m/%Y', '%d-%m-%Y',
                '%d.%m.%Y', '%b %d, %Y', '%d %b %Y', '%Y%m%d']
LOOK_FOR_HEADINGS_IN = 40      # rows


class CsvLayoutError(Exception):
    """The file couldn't be read as bank transactions; the message says why, for the Import Inbox."""


class CsvStatement:
    """What was read: the account it stands for, its transactions, and the latest balance if it gave one."""

    def __init__(self, account_id, transactions, balance=None, balance_date=None, sign_facts=None):
        self.account_id = account_id
        self.transactions = transactions        # [CsvTransaction], in the file's order
        self.balance = balance
        self.balance_date = balance_date
        # What the file says about which way its amounts go (see decide_signs)
        self.sign_facts = sign_facts or {'certain': True, 'reversed': False}


class CsvTransaction:
    def __init__(self, fitid, day, amount, name, memo=''):
        self.fitid = fitid
        self.day = day
        self.amount = amount
        self.name = name
        self.memo = memo


def tidy_heading(text):
    text = re.sub(r'\(.*?\)', ' ', (text or '').lower())
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', text).split())


def money(text):
    """'$1,234.50', '-12.00', '(12.00)', '12.00-' -> Decimal; blank -> None."""
    text = (text or '').strip().upper().replace('USD', '').replace('$', '').replace(',', '').replace(' ', '')
    if not text:
        return None
    negative = text.startswith('-') or text.endswith('-') or (text.startswith('(') and text.endswith(')'))
    text = text.strip('-()+')
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return -value if negative else value


def account_id_for(filename):
    """Fidelity's account number from its file name, or the file name itself, without dates."""
    fidelity = re.search(r'Account_([A-Z0-9]+)', filename, re.IGNORECASE)
    if fidelity:
        return fidelity.group(1)
    stem = Path(filename).stem.lower()
    # A second download of the same file: "activity (1)", "activity_copy-1", "activity copy"
    stem = re.sub(r'[-_ ]*(\(\d+\)|copy([-_ ]?\d+)?)\s*$', '', stem)
    stem = re.sub(r'\(\d+\)', ' ', stem)
    stem = re.sub(r'\d{4}[-_.]?\d{2}[-_.]?\d{2}|\d{1,2}[-_.]\d{1,2}[-_.]\d{2,4}', ' ', stem)
    stem = re.sub(r'\b(to|thru|through|from)\b', ' ', stem)
    stem = re.sub(r'[^a-z0-9]+', '-', stem).strip('-')
    return f'csv:{stem or "file"}'


def mostly_money_in(amounts):
    """More than 60% of the rows are money in: odd for a credit card, whose rows are mostly purchases."""
    amounts = [a for a in amounts if a]
    return len(amounts) >= 3 and sum(1 for a in amounts if a > 0) > len(amounts) * 0.6


def _direction(value):
    """'out', 'in' or None, from a direction column's value ("Debit", "ACH_CREDIT", "Sale"...)."""
    words = set(re.split(r'[^a-z]+', (value or '').lower())) - {''}
    out, into = bool(words & MONEY_OUT_WORDS), bool(words & MONEY_IN_WORDS)
    return 'out' if out and not into else 'in' if into and not out else None


def _balance_says(rows):
    """
    From a running balance: whether the amounts add up as given or the other
    way round, or None if it can't tell. Rows are (amount, balance) in the
    file's order, which may be oldest or newest first. On a card, the balance
    may count what's owed; decide_signs only uses this for other accounts.
    """
    pairs = [(a, b) for a, b in rows if b is not None]
    if len(pairs) < 4:
        return None
    counts = {'as_given': 0, 'reversed': 0}
    checked = 0
    for newer_last in (True, False):
        for (_a0, b0), (a1, b1) in zip(pairs, pairs[1:]) if newer_last else zip(pairs[1:], pairs):
            step = b1 - b0
            if newer_last:
                checked += 1
            if abs(step - a1) < Decimal('0.005'):
                counts['as_given'] += 1
            elif abs(step + a1) < Decimal('0.005'):
                counts['reversed'] += 1
    best = max(counts, key=counts.get)
    if counts[best] >= 3 and counts[best] >= SURE * checked and counts[best] > 2 * counts[min(counts, key=counts.get)]:
        return best
    return None


def sign_facts(rows, columns, unsigned_fixed=None):
    """
    What a file says about which way its amounts go. rows are (name, amount,
    direction or None, balance or None), amounts as they will be staged.
      certain/reversed: settled by the file alone (money-out and money-in
          columns, or a direction column that agrees, or disagrees, throughout)
      balance_says: 'as_given' / 'reversed' from the running balance, if any
      payments_negative: whether rows described as payments are negative
      example: a row that isn't a payment, to ask about
    """
    facts = {'certain': False, 'reversed': False, 'balance_says': None, 'payments_negative': None, 'example': None}
    if 'amount' not in columns or unsigned_fixed:
        facts['certain'] = True
        return facts
    checked = [(amount < 0) == (direction == 'out') for _n, amount, direction, _b in rows if direction and amount]
    if len(checked) >= 3:
        agree = sum(checked)
        if agree >= SURE * len(checked):
            facts['certain'] = True
            return facts
        if len(checked) - agree >= SURE * len(checked):
            facts['certain'], facts['reversed'] = True, True
            return facts
    facts['balance_says'] = _balance_says([(amount, balance) for _n, amount, _d, balance in rows])
    payments = [amount for name, amount, _d, _b in rows if PAYMENT.search(name) and amount]
    if payments:
        negative = sum(1 for amount in payments if amount < 0)
        if negative != len(payments) - negative:
            facts['payments_negative'] = negative > len(payments) - negative
    example = next(((name, amount) for name, amount, _d, _b in rows if amount and not PAYMENT.search(name)), None)
    if example:
        facts['example'] = {'name': example[0], 'amount': str(example[1])}
    return facts


def decide_signs(facts, account):
    """
    Whether to reverse a file's amounts, and whether that's settled
    (otherwise the Import Inbox asks). facts is None for a QFX/OFX file,
    whose standard says which way is which.
    """
    reversed_setting = bool(account and account.import_amounts_reversed)
    if facts is None:
        return reversed_setting, True
    if facts.get('certain'):
        return facts.get('reversed', False), True
    if account is None:
        return False, False                 # decided once the account is picked
    if account.import_sign_confirmed:
        return reversed_setting, True
    is_card = account.account_type == 'CREDIT CARD'
    if facts.get('balance_says') and not is_card and not getattr(account, 'loan_details', None):
        return facts['balance_says'] == 'reversed', True
    # A best guess, to be confirmed: payments are money in on a card
    if is_card and facts.get('payments_negative') is not None:
        return facts['payments_negative'], False
    return reversed_setting, False


def _text(path):
    raw = Path(path).read_bytes()
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        return raw.decode('cp1252', errors='replace')


def _rows_and_columns(text):
    """The rows, split on whichever separator gives recognisable headings (comma first)."""
    first_rows = None
    for delimiter in (',', ';', '\t', '|'):
        rows = list(csv.reader(text.splitlines(), delimiter=delimiter))
        first_rows = first_rows or rows
        found = _find_columns(rows)
        if found:
            return rows, found
    return first_rows or [], None


def _find_columns(rows):
    """The heading row and which column is which, or None."""
    for index, row in enumerate(rows[:LOOK_FOR_HEADINGS_IN]):
        tidied = [tidy_heading(cell) for cell in row]
        columns = {}
        for role, names in HEADINGS.items():
            found = [(names.index(name), position) for position, name in enumerate(tidied) if name in names]
            if found:
                columns[role] = min(found)[1]
        has_money = 'amount' in columns or 'debit' in columns or 'credit' in columns
        if 'date' in columns and 'description' in columns and has_money:
            return index, columns, tidied
    return None


def _date_format(values):
    """The format that reads the most of these dates (month-first wins a tie)."""
    best, best_count = None, 0
    for fmt in DATE_FORMATS:
        count = sum(1 for value in values if _parse_date(value, fmt))
        if count > best_count:
            best, best_count = fmt, count
    return best


def _parse_date(value, fmt):
    value = (value or '').strip()
    for candidate in (value, value.split('T')[0], value.split(' ')[0]):
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    return None


def _is_fidelity(tidied):
    return 'run date' in tidied and 'action' in tidied


def read_fidelity(rows, header_index, account_id):
    """Fidelity's own layout, read as before so the ids stay the same."""
    header = rows[header_index]
    transactions, seen = [], {}
    latest_balance = latest_day = None
    for values in rows[header_index + 1:]:
        row = dict(zip(header, values))
        raw_date = row.get('Run Date') or row.get('Trade Date', '')
        raw_amount = str(row.get('Amount ($)') or row.get('Amount') or '').strip()
        if not raw_date or not raw_amount:
            continue        # blank lines and the legal text at the end
        try:
            day = datetime.strptime(raw_date.strip(), '%m/%d/%Y').date()
        except ValueError:
            continue
        action = (row.get('Action') or '').strip()
        description = (row.get('Description') or row.get('Security Description') or '').strip()
        name = action if 'No Description' in description else f'{action} {description}'.strip()
        amount = float(raw_amount.replace('$', '').replace(',', '').strip() or '0')
        balance = money(row.get('Cash Balance ($)'))
        # Newest first: the first row of the newest day has its closing balance
        if balance is not None and (latest_day is None or day > latest_day):
            latest_day, latest_balance = day, balance
        # The id the importer has always given a Fidelity row
        base = f'{raw_date}{name}{amount}'
        count = seen.get(base, 0)
        seen[base] = count + 1
        fitid = hashlib.md5(f'{base}_{count}'.encode('utf-8')).hexdigest()
        transactions.append(CsvTransaction(fitid, day, Decimal(str(amount)), name, action))
    return CsvStatement(account_id, transactions, latest_balance, latest_day)


def read_csv(path, filename=None):
    """Reads a bank CSV. Raises CsvLayoutError, with what was found, if it can't."""
    filename = filename or Path(path).name
    rows, found = _rows_and_columns(_text(path))
    if not any(any(cell.strip() for cell in row) for row in rows):
        raise CsvLayoutError(f'{filename} is empty.')
    if not found:
        headings = next((row for row in rows[:LOOK_FOR_HEADINGS_IN] if sum(1 for cell in row if cell.strip()) >= 2), [])
        seen = ', '.join(cell.strip() for cell in headings if cell.strip()) or 'none'
        raise CsvLayoutError(
            f"{filename}: couldn't find a date, a description and an amount (or money-out and money-in) column. "
            f'Headings found: {seen}. Download the file as CSV with column headings, or as QFX/OFX if your bank offers it.')
    header_index, columns, tidied = found
    account_id = account_id_for(filename)
    if _is_fidelity(tidied):
        statement = read_fidelity(rows, header_index, account_id)
        if not statement.transactions:
            raise CsvLayoutError(f'{filename}: no transactions in it.')
        return statement

    data = rows[header_index + 1:]
    cell = lambda row, role: row[columns[role]].strip() if role in columns and columns[role] < len(row) else ''
    fmt = _date_format([cell(row, 'date') for row in data])
    if not fmt:
        raise CsvLayoutError(f"{filename}: the dates in the {rows[header_index][columns['date']]} column couldn't be read.")

    parsed = []
    for row in data:
        day = _parse_date(cell(row, 'date'), fmt)
        if day is None:
            continue        # a total, a note, a blank line
        if 'amount' in columns:
            amount = money(cell(row, 'amount'))
        else:
            out, into = money(cell(row, 'debit')), money(cell(row, 'credit'))
            amount = None if out is None and into is None else (into or Decimal('0')) - abs(out or Decimal('0'))
        if amount is None:
            continue
        name = ' '.join(cell(row, 'description').split())
        parsed.append([day, amount, name, cell(row, 'memo'), cell(row, 'reference'), money(cell(row, 'balance')),
                       _direction(cell(row, 'direction')) if 'direction' in columns else None])
    if not parsed:
        raise CsvLayoutError(f'{filename}: no transactions in it.')

    # Unsigned amounts with a direction column: the column gives each row its sign
    unsigned_fixed = False
    if 'amount' in columns and 'direction' in columns and all(row[1] >= 0 for row in parsed):
        known = [row for row in parsed if row[6]]
        if len(known) >= SURE * len(parsed):
            for row in known:
                row[1] = -row[1] if row[6] == 'out' else row[1]
            unsigned_fixed = True
    facts = sign_facts([(row[2], row[1], row[6], row[5]) for row in parsed], columns, unsigned_fixed)

    transactions, seen = [], {}
    for day, amount, name, memo, reference, _balance, _direction_of_row in parsed:
        if reference:
            fitid = reference
        else:
            base = f'{day.isoformat()}|{name}|{amount}'
            count = seen.get(base, 0)
            seen[base] = count + 1
            fitid = 'csv-' + hashlib.md5(f'{base}|{count}'.encode('utf-8')).hexdigest()
        transactions.append(CsvTransaction(fitid, day, amount, name, memo))

    # The closing balance: the last row of the newest day, in date order
    balance = balance_date = None
    with_balance = [(row[0], row[5]) for row in parsed if row[5] is not None]
    if with_balance:
        newest_first = parsed[0][0] > parsed[-1][0]
        newest = max(day for day, _bal in with_balance)
        on_newest = [bal for day, bal in with_balance if day == newest]
        balance, balance_date = (on_newest[0] if newest_first else on_newest[-1]), newest
    return CsvStatement(account_id, transactions, balance, balance_date, sign_facts=facts)
