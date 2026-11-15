# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import time
import os
import sys
import subprocess
import threading
import requests
from watchdog.observers.polling import PollingObserver as Observer
from watchdog.events import FileSystemEventHandler

# 1. HARDCODE THE DIR (Safer than importing it)
SOURCE_DIR = "/data/dropzone"

BANK_EXTENSIONS = ('.qfx', '.ofx', '.csv')
RECEIPT_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.heic')

# A file is taken to be completely copied once its size and timestamp have
# not changed for this long...
SETTLE_SECONDS = float(os.environ.get('WATCHER_SETTLE_SECONDS', 2))
# ...and we give up on one that is still changing after this long.
MAX_WAIT_SECONDS = float(os.environ.get('WATCHER_MAX_WAIT_SECONDS', 600))

# Receipt photos wait in the dropzone while the receipt reader can't be
# reached (stopped, restarting, still starting). This often, the watcher asks
# whether it's answering again, and has them read as soon as it is.
RECEIPT_RETRY_SECONDS = float(os.environ.get('WATCHER_RECEIPT_RETRY_SECONDS', 60))
RECEIPT_READER_URL = os.environ.get('RECEIPT_READER_URL', 'http://ai-worker:5000')


def reader_installed():
    """The receipt reader is an optional add-on: COMPOSE_PROFILES=receipts in .env."""
    return 'receipts' in [p.strip() for p in os.environ.get('COMPOSE_PROFILES', '').split(',')]


def reader_answering():
    try:
        response = requests.get(f"{RECEIPT_READER_URL.rstrip('/')}/health", timeout=2)
        return response.status_code == 200 and response.json().get('status') == 'ready'
    except (requests.RequestException, ValueError):
        return False


def waiting_receipts(source_dir=SOURCE_DIR):
    """Receipt photos sitting in the dropzone (not ones still arriving)."""
    try:
        names = sorted(os.listdir(source_dir))
    except OSError:
        return []
    return [n for n in names if n.lower().endswith(RECEIPT_EXTENSIONS) and not n.startswith('.')
            and os.path.isfile(os.path.join(source_dir, n))]


def wait_until_complete(filepath, settle=None, max_wait=None, poll=0.25):
    """
    Look, wait, look again: returns True once the file has stopped changing.

    Files uploaded through the app arrive complete (written under a hidden
    name, then renamed), but one copied straight into the folder shows up
    while it is still being written. A fixed wait is too long for a small
    file and too short for a large one, so watch the file itself instead.

    Returns False if the file disappears (another run already imported it)
    or is still changing after max_wait.
    """
    settle = SETTLE_SECONDS if settle is None else settle
    max_wait = MAX_WAIT_SECONDS if max_wait is None else max_wait
    deadline = time.monotonic() + max_wait
    last_seen = None
    unchanged_since = time.monotonic()

    while True:
        try:
            stat = os.stat(filepath)
        except OSError:
            return False
        seen = (stat.st_size, stat.st_mtime)
        now = time.monotonic()

        if seen != last_seen:
            last_seen = seen
            unchanged_since = now
        # An empty file is one whose first bytes have not arrived yet
        elif stat.st_size > 0 and now - unchanged_since >= settle:
            return True

        if now >= deadline:
            return False
        time.sleep(poll)


class FileHandler(FileSystemEventHandler): # Renamed from QFXHandler to generic
    def __init__(self):
        super().__init__()
        # Only one import at a time, whether it was triggered by an event or
        # by the start-up sweep. Two overlapping runs could stage a file twice.
        self._one_at_a_time = threading.Lock()

    def process(self, filepath):
        """
        Helper method to handle the logic for import.
        Accepts a full filepath string.
        """
        # Get just the name for cleaner logging
        filename = os.path.basename(filepath)
        lowered = filename.lower()

        # Hidden names are files still on their way in (e.g. the app's own
        # ".upload-xxxx.part"); they are handled when they get their real name.
        if filename.startswith('.'):
            return

        if lowered.endswith(BANK_EXTENSIONS):
            kind, command, label = 'bank', 'stage_import', '💰 Bank File'
        elif lowered.endswith(RECEIPT_EXTENSIONS):
            kind, command, label = 'receipt', 'ingest_receipts', '🧾 Receipt Image'
        else:
            return

        with self._one_at_a_time:
            # One copy can raise several events (created, then modified). Events
            # are handled one at a time, so by the time a later one is looked at,
            # the earlier one has usually imported and archived the file.
            if not os.path.exists(filepath):
                return

            print(f"{label} Detected: {filename}")
            if not wait_until_complete(filepath):
                if os.path.exists(filepath):
                    print(f"⏳ {filename} was still changing after {MAX_WAIT_SECONDS:.0f}s. Skipped; it will be picked up when it changes again.")
                return

            try:
                if kind == 'bank':
                    print(f"🚀 Triggering Staged Import for {filename}...")
                else:
                    print(f"🧠 Triggering OCR Analysis for {filename}...")
                subprocess.run(["python", "manage.py", command], check=True)
                print(f"✅ {'Bank Import Complete' if kind == 'bank' else 'Receipt Processed'}: {filename}")
            except subprocess.CalledProcessError as e:
                print(f"❌ Error processing {'bank file' if kind == 'bank' else 'Receipt'}: {e}")
            except Exception as e:
                print(f"❌ General Watcher Failure: {e}")

    def on_created(self, event):
        if not event.is_directory:
            self.process(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self.process(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self.process(event.dest_path)


def process_waiting_files(handler, source_dir=SOURCE_DIR):
    """
    Handles files that were already in the folder when the watcher started
    (uploaded while it was down or restarting). Nothing would otherwise
    notice them until they changed.
    """
    try:
        names = sorted(os.listdir(source_dir))
    except OSError:
        return
    handled = set()
    for name in names:
        path = os.path.join(source_dir, name)
        lowered = name.lower()
        kind = 'bank' if lowered.endswith(BANK_EXTENSIONS) else 'receipt' if lowered.endswith(RECEIPT_EXTENSIONS) else None
        # Each importer works through every waiting file of its kind, so one
        # trigger per kind is enough.
        if kind and kind not in handled and not name.startswith('.') and os.path.isfile(path):
            handled.add(kind)
            handler.process(path)


def retry_waiting_receipts(handler, source_dir=SOURCE_DIR, answering=reader_answering):
    """If receipt photos are waiting and the reader answers, has them read. True if it did."""
    waiting = waiting_receipts(source_dir)
    if not waiting or not reader_installed() or not answering():
        return False
    print(f"🔁 The receipt reader is answering: reading {len(waiting)} waiting photo(s).")
    # The importer reads every waiting photo, so one trigger is enough
    handler.process(os.path.join(source_dir, waiting[0]))
    return True


if __name__ == "__main__":
    # Ensure the directory exists before watching
    if not os.path.exists(SOURCE_DIR):
        print(f"Waiting for {SOURCE_DIR} to be created...")
        while not os.path.exists(SOURCE_DIR):
            time.sleep(1)

    observer = Observer()
    print(f"Watcher started. Watching: {SOURCE_DIR}")

    try:
        handler = FileHandler()
        observer.schedule(handler, path=SOURCE_DIR, recursive=False)
        observer.start()
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        sys.exit(1)

    process_waiting_files(handler)

    try:
        next_retry = time.monotonic() + RECEIPT_RETRY_SECONDS
        while True:
            time.sleep(1)
            if time.monotonic() >= next_retry:
                next_retry = time.monotonic() + RECEIPT_RETRY_SECONDS
                try:
                    retry_waiting_receipts(handler)
                except Exception as e:
                    print(f"❌ Couldn't retry waiting receipts: {e}")
    except KeyboardInterrupt:
        observer.stop()
        print("\nWatcher stopped.")
    observer.join()
