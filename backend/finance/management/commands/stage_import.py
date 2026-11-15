# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import hashlib
import shutil
import re
import io
from pathlib import Path
from datetime import datetime
from decimal import Decimal
import logging

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from ofxtools.Parser import OFXTree
from collections import Counter
from finance.csv_import import CsvLayoutError, decide_signs, read_csv
from finance.balances import mark_included
from finance.models import Account, BankImport, StagedTransaction
from finance.rules import active_rules, apply_rules, record_hits
from finance.services import LedgerMatcher

logger = logging.getLogger(__name__)

# =========================================================
# FALLBACK DUMMY CLASSES
# These mimic the ofxtools objects so the rest of the app 
# doesn't know the strict parser failed.
# =========================================================
class DummyTx:
    def __init__(self, fitid, trnamt, dtposted, name, memo):
        self.fitid = fitid
        self.trnamt = Decimal(str(trnamt))
        self.dtposted = dtposted
        self.name = name
        self.memo = memo

class DummyAccount:
    def __init__(self, acctid):
        self.acctid = acctid

class DummyLedgerBal:
    def __init__(self, balamt, dtasof):
        self.balamt = Decimal(str(balamt))
        self.dtasof = dtasof

class DummyTranList:
    def __init__(self, end_date):
        self.dtend = end_date

class DummyStmt:
    def __init__(self, acctid, balamt, dtasof, txs):
        self.account = DummyAccount(acctid)
        # No balance in the file (most CSVs): the account's balance is left alone
        self.ledgerbal = DummyLedgerBal(balamt, dtasof) if balamt is not None else None
        self.transactions = txs
        self.banktranlist = DummyTranList(dtasof)
        # What a CSV says about which way its amounts go; None for QFX/OFX (see decide_signs)
        self.sign_facts = None


def give_missing_ids(transactions):
    """
    A transaction the bank gave no id (FITID) gets one made from what it is:
    its date, amount, payee and memo, and which of the identical ones in the
    file it is. Two identical charges on one day stay two, and the same file
    downloaded again gives the same ids, so it is still recognised.
    """
    seen = Counter()
    for tx in transactions:
        if (getattr(tx, 'fitid', '') or '').strip() and tx.fitid != 'UNKNOWN':
            continue
        day = tx.dtposted.date() if hasattr(tx.dtposted, 'date') else tx.dtposted
        base = f"{day.isoformat()}|{Decimal(str(tx.trnamt))}|{getattr(tx, 'name', '') or ''}|{getattr(tx, 'memo', '') or ''}"
        seen[base] += 1
        tx.fitid = 'gen-' + hashlib.md5(f"{base}|{seen[base]}".encode('utf-8')).hexdigest()


# =========================================================
# THE COMMAND
# =========================================================
class Command(BaseCommand):
    help = 'Parses QFX/CSV, cleans names using Rules, updates Account Balances, and stages transactions'

    def handle(self, *args, **options):
        # In the order they are tried (see finance.rules)
        self.rules = active_rules()
        
        source_dir = Path("/data/dropzone")
        archive_dir = Path("/data/archive/qfx")
        
        # Look for both QFX, OFX, and CSV files
        qfx_files = list(source_dir.glob("*.[qQ][fF][xX]"))
        ofx_files = list(source_dir.glob("*.[oO][fF][xX]"))
        csv_files = list(source_dir.glob("*.[cC][sS][vV]"))
        files = [f for f in (qfx_files + ofx_files + csv_files) if "archive" not in str(f)]
        
        if not files:
            self.stdout.write("No QFX/OFX/CSV files found in /data/dropzone.")
            return
            
        for p in files:
            try:
                self.process_file(p, archive_dir)
            except Exception:
                # Set aside and listed in the Import Inbox, not retried forever;
                # the details go to this container's log, not the browser
                logger.exception("Importing %s failed", p.name)
                self.record_failure(p, f"{p.name}: something went wrong while importing it. "
                                       "The details are in the watcher's log (docker compose logs watcher).")

    def scrub_in_memory(self, filepath):
        """Attempts to prep the file for the strict ofxtools parser."""
        with open(filepath, 'rb') as f:
            raw_bytes = f.read()
            
        content = raw_bytes.decode('utf-8-sig', errors='ignore')

        # Check if this is a modern OFX v2 XML file
        if content.strip().startswith('<?xml'):
            return io.BytesIO(raw_bytes)

        start_idx = content.find('<OFX>')
        if start_idx == -1:
            start_idx = content.find('<OFX ')
            
        end_idx = content.rfind('</OFX>')

        if start_idx != -1 and end_idx != -1:
            ofx_body = content[start_idx:end_idx + 6].strip()
        else:
            ofx_body = content.strip() 

        standard_header = (
            "OFXHEADER:100\r\nDATA:OFXSGML\r\nVERSION:102\r\nSECURITY:NONE\r\n"
            "ENCODING:USASCII\r\nCHARSET:1252\r\nCOMPRESSION:NONE\r\n"
            "OLDFILEUID:NONE\r\nNEWFILEUID:NONE\r\n\r\n"
        )
        final_content = standard_header + ofx_body

        return io.BytesIO(final_content.encode('utf-8'))

    def _parse_date(self, date_str, fallback=None):
        """Helper to safely extract dates from messy OFX strings."""
        if not date_str: 
            return fallback or timezone.now()
            
        clean_str = str(date_str).strip()[:8]
        try:
            dt = datetime.strptime(clean_str, '%Y%m%d')
            return timezone.make_aware(dt)
        except:
            return fallback or timezone.now()

    def csv_statement(self, filepath):
        """Any bank's CSV (finance/csv_import.py), in the shape of a parsed QFX statement."""
        read = read_csv(filepath)
        transactions = [DummyTx(tx.fitid, tx.amount, tx.day, tx.name, tx.memo) for tx in read.transactions]
        stmt = DummyStmt(read.account_id, read.balance, read.balance_date, transactions)
        if read.balance is None:
            stmt.banktranlist = DummyTranList(max(tx.day for tx in read.transactions))
        stmt.sign_facts = read.sign_facts
        return stmt

    def record_failure(self, p, message):
        """
        A file that couldn't be imported: why, for the Import Inbox, and the
        file moved aside so it isn't tried again and again.
        """
        BankImport.objects.create(filename=p.name, status="Failed", error_log=message)
        failed_dir = Path(settings.ARCHIVE_ROOT) / "failed"
        try:
            failed_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(failed_dir / f"{datetime.now():%Y%m%d-%H%M%S}_{p.name}"))
        except OSError as e:
            self.stdout.write(self.style.ERROR(f"  -> Could not move the file aside: {e}"))
        self.stdout.write(self.style.ERROR(f"  [FAILED] {message}"))

    def fallback_parse(self, filepath):
        """
        THE NUCLEAR OPTION: Bypasses XML parsing entirely and uses regex 
        to forcefully extract financial data directly from the text file.
        """
        with open(filepath, 'rb') as f:
            content = f.read().decode('utf-8', errors='ignore')

        # Neither a transaction nor a balance: not a bank statement at all
        if '<STMTTRN>' not in content.upper() and '<LEDGERBAL>' not in content.upper():
            return None

        # 1. Extract Account ID
        acctid_match = re.search(r'<ACCTID>([^<\r\n]+)', content, re.IGNORECASE)
        acctid = acctid_match.group(1).strip() if acctid_match else None
        
        # If no account ID found inside, try filename
        if not acctid:
            filename = Path(filepath).name
            file_digits = re.findall(r'\d{4,}', filename)
            acctid = file_digits[0] if file_digits else "UNKNOWN"
            
        self.stdout.write(self.style.WARNING(f"    -> [FALLBACK] Identified Account: {acctid}"))
            
        # 2. Extract Balance
        balamt_match = re.search(r'<LEDGERBAL>.*?<BALAMT>([^<\r\n]+)', content, re.DOTALL | re.IGNORECASE)
        balamt = float(balamt_match.group(1).strip()) if balamt_match else 0.0
        
        dtasof_match = re.search(r'<LEDGERBAL>.*?<DTASOF>([^<\r\n\[]+)', content, re.DOTALL | re.IGNORECASE)
        dtasof = self._parse_date(dtasof_match.group(1)) if dtasof_match else timezone.now()
        
        # 3. Extract Transactions
        transactions = []
        trn_blocks = re.findall(r'<STMTTRN>(.*?)</STMTTRN>', content, re.DOTALL | re.IGNORECASE)
        
        if not trn_blocks:
            splits = re.split(r'<STMTTRN>', content, flags=re.IGNORECASE)
            trn_blocks = splits[1:] # Drop headers before the first transaction
            
        for block in trn_blocks:
            fitid_match = re.search(r'<FITID>([^<\r\n]+)', block, re.IGNORECASE)
            # None: given a stable id of its own (give_missing_ids)
            fitid = fitid_match.group(1).strip() if fitid_match else ""
            
            amt_match = re.search(r'<TRNAMT>([^<\r\n]+)', block, re.IGNORECASE)
            trnamt = float(amt_match.group(1).strip()) if amt_match else 0.0
            
            # Use DTUSER first (for Credit Cards), fallback to DTPOSTED
            dtuser_match = re.search(r'<DTUSER>([^<\r\n\[]+)', block, re.IGNORECASE)
            dtpost_match = re.search(r'<DTPOSTED>([^<\r\n\[]+)', block, re.IGNORECASE)
            
            if dtuser_match:
                dtposted = self._parse_date(dtuser_match.group(1), fallback=dtasof)
            elif dtpost_match:
                dtposted = self._parse_date(dtpost_match.group(1), fallback=dtasof)
            else:
                dtposted = dtasof
            
            name_match = re.search(r'<NAME>([^<\r\n]+)', block, re.IGNORECASE)
            name = name_match.group(1).strip() if name_match else ""
            
            memo_match = re.search(r'<MEMO>([^<\r\n]+)', block, re.IGNORECASE)
            memo = memo_match.group(1).strip() if memo_match else ""
            
            transactions.append(DummyTx(fitid, trnamt, dtposted, name, memo))
            
        return DummyStmt(acctid, balamt, dtasof, transactions)

    def apply_rules(self, tx, account):
        """Runs the transaction against the rules engine (see finance.services.apply_rules)."""
        return apply_rules(self.rules, getattr(tx, 'name', '') or '', tx.trnamt, getattr(tx, 'memo', ''), account)

    def process_file(self, p, archive_dir):
        self.stdout.write(f"\nProcessing: {p.name}")
        
        stmt = None
        
        # CSV files: recognised by their column headings, whichever bank
        if p.suffix.lower() == '.csv':
            try:
                stmt = self.csv_statement(p)
            except CsvLayoutError as e:
                self.record_failure(p, str(e))
                return

        else:
            try:
                # 1. Try the standard strict parser first
                clean_file_obj = self.scrub_in_memory(p)
                parser = OFXTree()
                parser.parse(clean_file_obj)
                ofx = parser.convert()
                
                if ofx.statements:
                    stmt = ofx.statements[0]
                    
            except Exception:
                pass 
                
            # 2. If strict parser crashed OR silently skipped data, force Regex Fallback
            if not stmt or not getattr(stmt, 'transactions', []):
                self.stdout.write(self.style.WARNING(f"  [PARSER] strict parser failed to find data. Deploying Regex Fallback..."))
                stmt = self.fallback_parse(p)
                
        if not stmt:
            self.record_failure(p, f"{p.name}: no bank statement found in it. Is it a QFX or OFX download from your bank?")
            return
        give_missing_ids(stmt.transactions)

        # 3. Identify Account: by its number, or a name remembered for it
        acct_num = getattr(stmt.account, 'acctid', None)
        account = Account.for_import(acct_num)

        # Which way the amounts go: some files write money out as positive.
        # Settled by the file or the account, or asked in the Import Inbox.
        amounts_reversed, settled = decide_signs(stmt.sign_facts, account)
        if amounts_reversed:
            self.stdout.write("  -> Money out is positive in this file: reversing the signs")
            for tx in stmt.transactions:
                tx.trnamt = -tx.trnamt
        sign_state = BankImport.SIGN_SETTLED if settled else BankImport.SIGN_ASK if account else BankImport.SIGN_WAITING
        
        # UNKNOWN ACCOUNT TRANSACTION HANDLING LOGIC
        unregistered_id = None
        if not account:
            self.stdout.write(self.style.WARNING(f"  [PARKING] Unknown account {acct_num}. Moving to holding pen..."))
            unregistered_id = acct_num

        # Safely find the newest transaction date for archiving purposes (Independent of balance logic)
        latest_tx_date = None
        if stmt.transactions:
            latest_tx_date = max(tx.dtposted.date() if hasattr(tx.dtposted, 'date') else tx.dtposted for tx in stmt.transactions)

        # =========================================================
        # Smart Account Balance Update (ONLY IF ACCOUNT IS KNOWN)
        # =========================================================
        balance_as_of = None
        if account:
            ledger_bal = getattr(stmt, 'ledgerbal', None)
            file_bal_amt = getattr(ledger_bal, 'balamt', None)
            file_bal_date = getattr(ledger_bal, 'dtasof', None)
            
            if file_bal_amt is not None and file_bal_date:
                file_date_obj = file_bal_date.date() if hasattr(file_bal_date, 'date') else file_bal_date
                
                max_tx_date = file_date_obj
                if latest_tx_date:
                    max_tx_date = max(file_date_obj, latest_tx_date)

                current_db_date = account.balance_date
                
                if not current_db_date or max_tx_date >= current_db_date:
                    if file_date_obj < max_tx_date:
                        delta = sum(tx.trnamt for tx in stmt.transactions if (tx.dtposted.date() if hasattr(tx.dtposted, 'date') else tx.dtposted) > file_date_obj)
                        calculated_balance = float(file_bal_amt) + float(delta)
                        
                        account.last_balance = calculated_balance
                        account.balance_date = max_tx_date
                        account.is_balance_calculated = True
                        self.stdout.write(self.style.SUCCESS(f"  -> Calculated Balance: ${calculated_balance} (As of {max_tx_date})"))
                    else:
                        account.last_balance = file_bal_amt
                        account.balance_date = file_date_obj
                        account.is_balance_calculated = False
                        self.stdout.write(self.style.SUCCESS(f"  -> Verified Balance: ${file_bal_amt} (As of {file_date_obj})"))
                        
                    account.save()
                    # Every row of this file is in that balance; so is anything
                    # cleared already in the ledger and dated up to it
                    balance_as_of = account.balance_date
                    mark_included(account)
                else:
                    self.stdout.write(f"  -> Skipping balance update (DB is newer)")
        else:
            self.stdout.write(self.style.WARNING("  -> Skipping balance update (Unregistered Account)"))

        # =========================================================
        # Staging Logic (Cleanly handles both known and parked txs)
        # =========================================================
        batch = BankImport.objects.create(
            filename=p.name,
            status="Partial", 
            is_committed=False,
            account=account,
            sign_facts=stmt.sign_facts,
            amounts_reversed=amounts_reversed,
            balance_as_of=balance_as_of,
            sign_state=sign_state,
        )
        
        counts = {StagedTransaction.STATUS_NEW: 0, StagedTransaction.STATUS_MATCH: 0,
                  StagedTransaction.STATUS_NEAR: 0, StagedTransaction.STATUS_DUPE: 0}
        rows = []
        for tx in stmt.transactions:
            tx_date = tx.dtposted.date() if hasattr(tx.dtposted, 'date') else tx.dtposted
            rows.append((tx, tx_date))

        # The same rules as the Import Inbox's re-check (finance.services);
        # each pending transaction is proposed to one row at most.
        account_id = account.pk if account else None
        matcher = LedgerMatcher([(account_id, getattr(tx, 'fitid', ''), tx_date) for tx, tx_date in rows])

        named = [(tx, tx_date, self.apply_rules(tx, account)) for tx, tx_date in rows]
        # Each rule's use count, shown in Settings, so unused rules can be found
        record_hits(Counter(result['rule'].pk for _tx, _date, result in named if result['rule']))
        results = matcher.match_all([
            # The bank's own description and any name a rule gave it
            (account_id, getattr(tx, 'fitid', ''), tx_date, Decimal(str(tx.trnamt)), f"{getattr(tx, 'name', '')} {rule_result['name']}",
             getattr(tx, 'memo', ''))
            for tx, tx_date, rule_result in named
        ])

        # Rows of this file already waiting in the Import Inbox (the same file,
        # or one covering the same days, dropped again before approving) aren't
        # staged a second time
        waiting = set(StagedTransaction.objects.filter(account=account, unregistered_account_id=unregistered_id or '')
                      .exclude(fitid='').values_list('fitid', flat=True))
        for (tx, tx_date, rule_result), (match_type, match_proposal, match_reason) in zip(named, results):
            if getattr(tx, 'fitid', '') in waiting:
                counts[StagedTransaction.STATUS_DUPE] += 1
                continue
            counts[match_type] += 1

            StagedTransaction.objects.create(
                batch=batch,
                account=account,
                unregistered_account_id=unregistered_id or  "", 
                fitid=getattr(tx, 'fitid', ''),
                date=tx_date,
                amount=tx.trnamt,
                name=rule_result['name'], 
                subcategory=rule_result['subcategory'],
                bank_memo=getattr(tx, 'memo', ''),
                personal_memo="",
                match_proposal=match_proposal,
                match_type=match_type,
                match_reason=match_reason,
            )
            
        batch.transactions_created = len(rows) - counts[StagedTransaction.STATUS_DUPE]
        batch.transactions_skipped = counts[StagedTransaction.STATUS_DUPE]
        batch.status = "Success"
        batch.save()
        
        self.stdout.write(
            f"  -> Staged: {counts['NEW']} New, {counts['MATCH']} Matches, "
            f"{counts['NEAR']} Near matches, {counts['DUPE']} Dupes."
        )

        # =========================================================
        # Archiving
        # =========================================================
        dtend = None
        if hasattr(stmt, 'banktranlist') and stmt.banktranlist:
            dtend = getattr(stmt.banktranlist, 'dtend', None)
            
        if dtend:
            date_prefix = dtend.strftime('%Y%m%d') if hasattr(dtend, 'strftime') else "20260101"
        elif latest_tx_date:
            date_prefix = latest_tx_date.strftime('%Y%m%d') if hasattr(latest_tx_date, 'strftime') else "20260101"
        else:
            date_prefix = datetime.now().strftime('%Y%m%d')
            
        dest_filename = f"{date_prefix}_{p.name}"
        destination = archive_dir / dest_filename
        
        try:
            shutil.move(str(p), str(destination))
            self.stdout.write(self.style.SUCCESS(f"  -> Archived to {destination}"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  -> Could not move file to archive: {e}"))