# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import os
import shutil
import requests
import re
from decimal import Decimal
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.core.files import File
from finance.models import Receipt, Transaction
from finance.services import receipt_match_window
from finance import receipt_reader

class Command(BaseCommand):
    help = 'Sends images to AI Worker for Spatial OCR, parses geometry, and matches transactions.'

    def handle(self, *args, **options):
        # Without the add-on, photos wait in the dropzone: installing it later
        # reads them then (the watcher looks at waiting files when it starts)
        if not receipt_reader.installed():
            self.stdout.write("The receipt reader add-on isn't installed (COMPOSE_PROFILES=receipts in .env), "
                              "so receipt photos stay in the dropzone until it is.")
            return

        dropzone = '/data/dropzone'
        archive_dir = '/data/archive/receipts'
        failed_dir = '/data/archive/failed'

        for d in [archive_dir, dropzone, failed_dir]:
            if not os.path.exists(d):
                os.makedirs(d)

        self.stdout.write(f"Scanning {dropzone}...")

        for filename in os.listdir(dropzone):
            if filename.startswith('.'): continue
            filepath = os.path.join(dropzone, filename)
            if os.path.isdir(filepath): continue
            
            if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.heic', '.webp')):
                self.process_image(filepath, filename, archive_dir, failed_dir)

    def process_image(self, filepath, filename, archive_dir, failed_dir):
        self.stdout.write(f"\n[{filename}] Processing via Spatial AI Worker...")
        
        if Receipt.objects.filter(original_filename=filename).exists():
            self.stdout.write(self.style.WARNING(f"   -> Skipped: Already in DB."))
            shutil.move(filepath, os.path.join(archive_dir, f"DUP_{filename}"))
            return

        try:
            url = receipt_reader.url('/parse')
            with open(filepath, 'rb') as f:
                # Long enough for the reader to load its model first, after a quiet spell
                response = requests.post(url, files={'file': f}, timeout=300)

            if response.status_code != 200:
                raise Exception(f"AI Worker Error: {response.text}")

            # Each piece of text is followed by eight position tags, the four
            # corners of its box on a 0-1000 grid:
            #   "Tax<loc_65><loc_604><loc_130><loc_604><loc_130><loc_630><loc_65><loc_630>7.00<loc_...>..."
            raw_spatial_text = response.json().get('text', '')
            
            if not raw_spatial_text.strip():
                self.stdout.write(self.style.WARNING("   -> AI found no text."))
                shutil.move(filepath, os.path.join(failed_dir, filename))
                return

            receipt = Receipt(original_filename=filename)
            with open(filepath, 'rb') as f:
                receipt.image.save(filename, File(f))
            
            # Create a "clean" version without location tags for Date/Total parsing
            clean_text = self.strip_location_tags(raw_spatial_text)
            receipt.raw_text = clean_text 
            
            # --- SMART EXTRACTION ---
            receipt.date = self.extract_date_smart(clean_text, filename)
            # By position first; the text-only method is the fallback
            receipt.total_amount = self.extract_total_spatial(raw_spatial_text) or self.extract_amount_smart(clean_text)
            
            # Use the spatial geometry text for Tax
            receipt.tax_amount = self.extract_tax_spatial(raw_spatial_text)
            # A "tax" as large as the total was read from the wrong line
            if receipt.tax_amount and receipt.total_amount and receipt.tax_amount >= receipt.total_amount:
                receipt.tax_amount = None
            
            receipt.save()

            self.stdout.write(self.style.SUCCESS(
                f"   -> Extracted: Date={receipt.date}, Amt=${receipt.total_amount}, Tax=${receipt.tax_amount}"
            ))

            self.match_transaction(receipt)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            shutil.move(filepath, os.path.join(archive_dir, f"{timestamp}_{filename}"))

        except requests.ConnectionError:
            # The reader is stopped, restarting or still starting: the photo
            # isn't at fault. It waits here, and the watcher tries again once
            # the reader answers (watcher.py).
            self.stdout.write(self.style.WARNING("   -> The receipt reader can't be reached; the photo waits in the dropzone."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"   -> CRASH: {e}"))
            shutil.move(filepath, os.path.join(failed_dir, filename))

    # =========================================
    # Reading amounts by their position on the receipt
    # =========================================
    # A run of position tags. The model writes eight per text (four corners);
    # an older, never-produced format had one combined tag, kept here so text
    # stored in that shape would still be understood.
    LOCATION_TAGS = r'(?:<loc_\d+>)+|<loc_\d+_\d+_\d+_\d+>'

    def strip_location_tags(self, spatial_text):
        """The plain words of the receipt, with every position tag removed."""
        return re.sub(r'\s+', ' ', re.sub(self.LOCATION_TAGS, ' ', spatial_text)).strip()

    def text_blocks(self, spatial_text):
        """Splits the model's output into pieces of text with the box each one sits in."""
        blocks = []
        for match in re.finditer(r'(?P<text>[^<]+)(?P<tags>' + self.LOCATION_TAGS + ')', spatial_text):
            numbers = [int(n) for n in re.findall(r'\d+', match.group('tags'))]
            if len(numbers) >= 8:
                # Corner points: x at even positions, y at odd ones
                xs, ys = numbers[0:8:2], numbers[1:8:2]
            elif len(numbers) == 4:
                xs, ys = numbers[0::2], numbers[1::2]
            else:
                continue
            blocks.append({
                'text': match.group('text').strip(),
                'x1': min(xs),  # Left edge
                'y1': min(ys),  # Top edge
                'x2': max(xs),  # Right edge
                'y2': max(ys),  # Bottom edge
            })
        return blocks

    AMOUNT = r'\d[\d,]*\.\d{2}'

    def labelled_amount(self, blocks, label, limit, last=False):
        """
        The amount printed against a label such as "Total" or "Sales Tax".

        The model sometimes returns a label and its amount as one piece of
        text ("TOTAL $40.37") and sometimes as two pieces on the same line, so
        both are handled. With last=True the last matching label on the
        receipt wins (a grand total comes after any earlier totals).
        """
        pattern = re.compile(r'(?i)^(?:' + label + r')\s*:?\s*(?P<rest>.*)$')
        for block in (reversed(blocks) if last else blocks):
            match = pattern.match(block['text'])
            if not match:
                continue
            rest = match.group('rest').strip()
            if rest:
                # Only an amount may follow the label: "Tax 3.08" is the tax
                # line, "Tax ID 12-345" or "Total savings 4.14" is not
                same_block = re.fullmatch(r'[$\s]*(' + self.AMOUNT + r')', rest)
                amount = self.as_amount(same_block.group(1), limit) if same_block else None
            else:
                amount = self.amount_on_same_line(blocks, block, limit)
            if amount is not None:
                return amount
        return None

    def as_amount(self, text, limit):
        try:
            value = Decimal(text.replace(',', ''))
        except Exception:
            return None
        return value if value < limit else None  # Sanity check

    def amount_on_same_line(self, blocks, label, limit):
        """
        The amount to the right of a label, on the same printed line.

        "Same line" is judged by the height of the label itself, not by a fixed
        share of the page: on a long receipt the lines sit much closer together
        on the model's 0-1000 grid than on a short one, and a fixed allowance
        picks up the line above or below.
        """
        label_middle = (label['y1'] + label['y2']) / 2
        allowance = max(label['y2'] - label['y1'], 6)
        candidates = []
        for block in blocks:
            if block is label or block['x1'] < label['x1']:
                continue
            distance = abs((block['y1'] + block['y2']) / 2 - label_middle)
            # Ignore other lines, and percentages (e.g. "7.0%")
            if distance > allowance or "%" in block['text']:
                continue
            for amount in re.findall(self.AMOUNT, block['text']):
                value = self.as_amount(amount, limit)
                if value is not None:
                    # Nearest in height first, then furthest to the right
                    candidates.append((distance, -block['x1'], value))
        return min(candidates)[2] if candidates else None

    def extract_tax_spatial(self, spatial_text):
        """Finds the "Tax" label and takes the amount on its line."""
        blocks = self.text_blocks(spatial_text)
        return self.labelled_amount(blocks, r'(?:(?:sales|state|local)\s+)?tax|vat|gst|hst', limit=500)

    def extract_total_spatial(self, spatial_text):
        """
        Finds the "Total" label and takes the amount on its line. Reading the
        line itself avoids being thrown by the Tax line above it, or by larger
        figures elsewhere on the receipt such as cash tendered.
        """
        blocks = self.text_blocks(spatial_text)
        # The total is only used to find the bank's transaction, so a line
        # saying what was actually paid beats the printed total when the two
        # differ. ("Tendered" is deliberately not here: that is cash handed
        # over, before change.)
        paid = r'(?:amount|total)\s+(?:paid|charged)'
        total = r'grand\s+total|total(?:\s+(?:due|amount|sale))?|amount\s+due|balance\s+due'
        for label in (paid, total):
            amount = self.labelled_amount(blocks, label, limit=100000, last=True)
            if amount is not None:
                return amount
        return None

    # =========================================
    # STANDARD CLEANERS
    # =========================================
    def extract_amount_smart(self, text):
        """
        Fallback for when the total could not be found by position: the first
        amount after the last "total" in the plain text. Returns None rather
        than guessing; a blank box is easier to put right than a wrong figure
        taken from somewhere else on the receipt.
        """
        pattern = r'(?i)\b(total|amount due|balance due)\b'
        matches = list(re.finditer(pattern, text))

        for match in reversed(matches):
            window = text[match.end() : match.end() + 60]
            # "Sub total", "Tax total", "Savings total": not the total. Only
            # the word directly in front counts; the line above often ends in "tax".
            prefix_window = text[max(0, match.start() - 12) : match.start()]
            if re.search(r'(?i)(sub|tax|saved|savings)[\s-]*$', prefix_window):
                continue

            found = re.search(self.AMOUNT, window)
            if found:
                amount = self.as_amount(found.group(0), limit=100000)
                if amount is not None:
                    return amount
        return None

    def extract_date_smart(self, text, filename):
        valid_date = None
        date_patterns = [
            r'(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})',
            r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})',
            r'([A-Za-z]{3})\s+(\d{1,2})[,\s]+(\d{4})'
        ]
        
        for pat in date_patterns:
            for match in re.finditer(pat, text):
                prefix = text[max(0, match.start() - 20):match.start()]
                if re.search(r'(?i)(expire|valid|return by|policy)', prefix):
                    continue
                try:
                    groups = match.groups()
                    if len(groups) == 3 and not groups[0].isdigit():
                        dt = datetime.strptime(f"{groups[0]} {groups[1]} {groups[2]}", "%b %d %Y")
                        valid_date = dt.date()
                    else:
                        parts = [int(p) for p in groups]
                        dt = None
                        if parts[0] > 1000: dt = datetime(parts[0], parts[1], parts[2])
                        elif parts[2] > 1000: dt = datetime(parts[2], parts[0], parts[1])
                        elif parts[2] < 100: dt = datetime(2000+parts[2], parts[0], parts[1])
                        if dt and dt.date() <= (datetime.now() + timedelta(days=1)).date():
                            valid_date = dt.date()
                except: pass
                if valid_date: return valid_date

        filename_match = re.search(r'(\d{4})[-]?(\d{2})[-]?(\d{2})', filename)
        if filename_match:
            try:
                y, m, d = map(int, filename_match.groups())
                return datetime(y, m, d).date()
            except: pass
        return None

    def match_transaction(self, receipt):
        if not receipt.date or not receipt.total_amount:
            self.stdout.write(self.style.NOTICE("   -> Missing Date/Amount. Cannot match."))
            receipt.status = 'ORPHANED'
            receipt.save()
            return
        
        date_min, date_max = receipt_match_window(receipt.date)
        target_amt = receipt.total_amount * Decimal("-1.00")

        self.stdout.write(f"   -> Matching: {target_amt} between {date_min} and {date_max}")
        
        match = Transaction.objects.filter(dtposted__range=(date_min, date_max), trnamt=target_amt).first()

        if match:
            receipt.transaction = match
            receipt.status = 'MATCHED'
            receipt.save()
            self.stdout.write(self.style.SUCCESS(f"   -> MATCHED: {match.name}"))
            # The tax amount is only a reading. Splitting the transaction by
            # it waits for the user to review and approve it in the Receipt Inbox.
        else:
            receipt.status = 'ORPHANED'
            receipt.save()
            self.stdout.write("   -> No match found.")
