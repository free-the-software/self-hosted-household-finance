# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
"""
Storage retention: how long archived bank files and receipt images are kept.

Every category defaults to "keep forever". Files are only ever deleted from
the folders named here, and only when a policy with a number of days has been
saved for that category. A receipt's database record is never deleted: when
its image expires, the record stays and is marked with image_expired_at.
"""
import logging
import os
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Receipt, RetentionPolicy, RetentionRun

logger = logging.getLogger(__name__)

RECEIPT_IMAGES = 'receipt_images'
RECEIPT_ARCHIVE = 'receipt_archive'
FAILED_RECEIPTS = 'failed_receipts'
BANK_FILES = 'bank_files'
DB_BACKUPS = 'db_backups'
FULL_BACKUPS = 'full_backups'

# The wording here is shown to the user in Settings, so it must say exactly
# what a policy removes and what it leaves alone.
CATEGORIES = [
    {
        'key': RECEIPT_IMAGES,
        'label': 'Receipt images',
        'description': 'The copy of each receipt that the app shows and links to a transaction. This is your audit copy.',
        'deletes': 'The image file of each receipt older than the period, measured from the receipt date '
                   '(or the upload date if no date could be read).',
        'keeps': 'The receipt record: its date, total, tax, extracted text and transaction link, plus any tax '
                 'split made from it. The receipt then shows as "image expired".',
        'age_basis': 'receipt date',
    },
    {
        'key': RECEIPT_ARCHIVE,
        'label': 'Receipt archive copies',
        'description': 'A second copy of each receipt image, left in the archive folder after scanning. '
                       'Nothing in the app uses it.',
        'deletes': 'Archive copies older than the period, measured from when they were archived.',
        'keeps': 'The receipt image shown in the app, the receipt record and its transaction link.',
        'age_basis': 'date archived',
    },
    {
        'key': FAILED_RECEIPTS,
        'label': 'Failed receipt scans',
        'description': 'Images the scanner could not read. They never became receipts.',
        'deletes': 'These image files, older than the period, measured from when the scan failed.',
        'keeps': 'Everything else. No receipt or transaction was ever created from these files.',
        'age_basis': 'date of the failed scan',
    },
    {
        'key': BANK_FILES,
        'label': 'Imported bank files',
        'description': 'The QFX, OFX and CSV files you imported, kept after the import finished.',
        'deletes': 'These files, older than the period, measured from when they were archived.',
        'keeps': 'Every transaction imported from them. You only lose the ability to re-import from the original file.',
        'age_basis': 'date archived',
    },
    {
        'key': DB_BACKUPS,
        'label': 'Database backups',
        'description': 'Backup files containing the database only, made by the schedule in the Backups tab or on demand.',
        'deletes': 'Database backup files older than the period, measured from when each was made.',
        'keeps': 'Your live data, every newer backup, and always the two most recent database backups, '
                 'however old they are.',
        'age_basis': 'date of the backup',
        'default_days': 30,
        'keep_newest': 2,
    },
    {
        'key': FULL_BACKUPS,
        'label': 'Full backups',
        'description': 'Backup files containing the database and the receipt images.',
        'deletes': 'Full backup files older than the period, measured from when each was made.',
        'keeps': 'Your live data and receipt images, every newer backup, and always the two most recent full '
                 'backups, however old they are.',
        'age_basis': 'date of the backup',
        'default_days': 30,
        'keep_newest': 2,
    },
]
CATEGORY_KEYS = [c['key'] for c in CATEGORIES]
# Categories that are pruned unless the user says otherwise. Everything else
# is kept forever by default.
DEFAULT_DAYS = {c['key']: c['default_days'] for c in CATEGORIES if c.get('default_days')}
# How many of the most recent files a policy must never delete. A backup
# schedule can be less frequent than the retention period, and a backup can
# fail; neither should ever leave the user with no backup at all.
KEEP_NEWEST = {c['key']: c['keep_newest'] for c in CATEGORIES if c.get('keep_newest')}

SAMPLE_LIMIT = 25           # files listed per category in a preview
MAX_RETENTION_DAYS = 36500  # 100 years


def _now():
    # Separate function so tests can move this module's clock on its own
    return timezone.now()


def _sources():
    """
    Where each file-based category lives: (folder, file name prefix or None).
    These are the only folders a policy may delete from. Both kinds of backup
    share one folder and are told apart by name, so any other file kept there
    (a key file, say) belongs to no category and is never deleted.
    """
    return {
        RECEIPT_ARCHIVE: (os.path.join(settings.ARCHIVE_ROOT, 'receipts'), None),
        FAILED_RECEIPTS: (os.path.join(settings.ARCHIVE_ROOT, 'failed'), None),
        BANK_FILES: (os.path.join(settings.ARCHIVE_ROOT, 'qfx'), None),
        DB_BACKUPS: (settings.BACKUP_DIR, 'finance-db-'),
        FULL_BACKUPS: (settings.BACKUP_DIR, 'finance-full-'),
    }


def _scan_folder(folder, prefix=None):
    """Yields (path, size, archived_at) for each regular file in a folder."""
    if not os.path.isdir(folder):
        return
    with os.scandir(folder) as entries:
        for entry in entries:
            if prefix and not entry.name.startswith(prefix):
                continue
            # Never follow links out of the folder, and leave subfolders alone
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                continue
            stat = entry.stat(follow_symlinks=False)
            # A move into the archive updates ctime but can keep an old mtime,
            # so the later of the two is when the file arrived here.
            arrived = datetime.fromtimestamp(max(stat.st_mtime, stat.st_ctime), tz=dt_timezone.utc)
            yield entry.path, stat.st_size, arrived


def _receipt_items():
    """Yields (receipt, size, dated_at) for each receipt that still has an image."""
    for receipt in Receipt.objects.exclude(image='').order_by('id'):
        try:
            size = receipt.image.size
        except (OSError, ValueError):
            size = 0  # the file is already missing; expiring it just tidies the record
        if receipt.date:
            dated = datetime.combine(receipt.date, datetime.min.time(), tzinfo=dt_timezone.utc)
        else:
            dated = receipt.created_at
        yield receipt, size, dated


def _items(key):
    """Yields (target, name, size, dated_at); target is a path or a Receipt."""
    if key == RECEIPT_IMAGES:
        for receipt, size, dated in _receipt_items():
            yield receipt, os.path.basename(receipt.image.name), size, dated
    else:
        for path, size, dated in _scan_folder(*_sources()[key]):
            yield path, os.path.basename(path), size, dated


def get_policies():
    """Returns {category: days or None}; None means keep forever."""
    saved = dict(RetentionPolicy.objects.values_list('category', 'retention_days'))
    # A saved row wins, including a saved "keep forever" (None)
    return {key: saved[key] if key in saved else DEFAULT_DAYS.get(key) for key in CATEGORY_KEYS}


def clean_policies(raw):
    """
    Validates {category: days or None} from the API.
    Returns (policies, error); unknown categories and bad values are refused.
    """
    if not isinstance(raw, dict):
        return None, 'Policies must be an object of category: days.'
    cleaned = {}
    for key, value in raw.items():
        if key not in CATEGORY_KEYS:
            return None, f'Unknown category: {key}'
        if value is None or value == '':
            cleaned[key] = None
            continue
        # bool is an int in Python; reject it explicitly
        if isinstance(value, bool) or not isinstance(value, int):
            return None, f'{key}: the period must be a whole number of days, or empty to keep forever.'
        if value < 1 or value > MAX_RETENTION_DAYS:
            return None, f'{key}: the period must be between 1 and {MAX_RETENTION_DAYS} days.'
        cleaned[key] = value
    return cleaned, None


def save_policies(policies, user=None):
    for key, days in policies.items():
        RetentionPolicy.objects.update_or_create(
            category=key, defaults={'retention_days': days, 'updated_by': user}
        )
    logger.warning("Retention policies changed by %s: %s", user or 'system', policies)


def usage():
    """What is stored right now, per category."""
    result = {}
    for key in CATEGORY_KEYS:
        files = total = 0
        oldest = None
        for _target, _name, size, dated in _items(key):
            files += 1
            total += size
            if oldest is None or dated < oldest:
                oldest = dated
        result[key] = {'files': files, 'bytes': total, 'oldest': oldest.date().isoformat() if oldest else None}
    result[RECEIPT_IMAGES]['expired'] = Receipt.objects.filter(image_expired_at__isnull=False).count()
    return result


def build_plan(policies=None, now=None):
    """
    Works out what the given policies would delete, without deleting anything.
    Returns (summary for the API, {category: [targets]}).
    """
    policies = get_policies() if policies is None else {**get_policies(), **policies}
    now = now or _now()
    labels = {c['key']: c['label'] for c in CATEGORIES}

    categories, targets = [], {}
    for key in CATEGORY_KEYS:
        days = policies.get(key)
        entry = {'key': key, 'label': labels[key], 'retention_days': days,
                 'files': 0, 'bytes': 0, 'oldest': None, 'newest': None, 'sample': []}
        targets[key] = []
        if days:
            cutoff = now - timedelta(days=days)
            items = sorted(_items(key), key=lambda item: item[3])
            # The newest few are protected whatever their age
            protected = KEEP_NEWEST.get(key, 0)
            candidates = items[:len(items) - protected] if protected else items
            expired = [item for item in candidates if item[3] < cutoff]
            for target, name, size, dated in expired:
                targets[key].append(target)
                entry['files'] += 1
                entry['bytes'] += size
                if len(entry['sample']) < SAMPLE_LIMIT:
                    entry['sample'].append({'name': name, 'bytes': size, 'date': dated.date().isoformat()})
            if expired:
                entry['oldest'] = expired[0][3].date().isoformat()
                entry['newest'] = expired[-1][3].date().isoformat()
        categories.append(entry)

    summary = {
        'categories': categories,
        'files': sum(c['files'] for c in categories),
        'bytes': sum(c['bytes'] for c in categories),
    }
    return summary, targets


def _expire_receipt_image(receipt, now):
    """Deletes a receipt's image file and marks the record; the record stays."""
    with transaction.atomic():
        receipt.image.delete(save=False)
        receipt.image = ''
        receipt.image_expired_at = now
        receipt.save(update_fields=['image', 'image_expired_at'])


def apply_policies(user=None, trigger='manual', now=None):
    """Deletes everything the saved policies say has expired. Returns the summary."""
    now = now or _now()
    summary, targets = build_plan(now=now)
    allowed_folders = {os.path.realpath(folder) for folder, _prefix in _sources().values()}

    deleted = freed = 0
    errors = []
    for entry in summary['categories']:
        done = done_bytes = 0
        for target in targets[entry['key']]:
            try:
                if isinstance(target, Receipt):
                    try:
                        size = target.image.size
                    except (OSError, ValueError):
                        size = 0
                    _expire_receipt_image(target, now)
                else:
                    # Last line of defence: the file must sit directly in one
                    # of the archive folders.
                    if os.path.dirname(os.path.realpath(target)) not in allowed_folders:
                        raise ValueError('outside the archive folders')
                    size = os.path.getsize(target)
                    os.remove(target)
                done += 1
                done_bytes += size
            except Exception as exc:
                name = getattr(target, 'original_filename', None) or os.path.basename(str(target))
                errors.append(f"{entry['label']}: {name}: {exc}")
        entry['files'], entry['bytes'] = done, done_bytes
        deleted += done
        freed += done_bytes

    summary.update({'files': deleted, 'bytes': freed, 'errors': errors})
    if deleted or errors:
        RetentionRun.objects.create(
            trigger=trigger, user=user, files_deleted=deleted, bytes_freed=freed,
            details={'categories': [
                {k: c[k] for k in ('key', 'retention_days', 'files', 'bytes')} for c in summary['categories']
            ], 'errors': errors},
        )
    logger.warning("Retention clean-up (%s) by %s: deleted %d files, %d bytes, %d errors",
                   trigger, user or 'scheduler', deleted, freed, len(errors))
    return summary
