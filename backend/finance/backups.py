# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
"""
Backups: one self-contained, verified, optionally encrypted file per backup.

A backup file is a tar archive holding
    manifest.json   what this backup is, with checksums
    db.dump         the database, in pg_dump's custom format
    media/...       the receipt images (full backups only)
and is encrypted as a whole in the age format (https://age-encryption.org)
when encryption is set up. Nothing here is specific to this app's format, so a
backup can be opened with the `age`, `tar` and `pg_restore` tools alone.

Files are written to settings.BACKUP_DIR. Getting them off the machine is the
job of a Destination (see below) or of whatever sync tool watches that folder.
"""
import hashlib
import json
import logging
import os
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
from datetime import timedelta

import pyrage
from django.conf import settings
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone

from .models import Backup, BackupSettings

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
KINDS = {
    Backup.KIND_DB: {'label': 'Database backup'},
    Backup.KIND_FULL: {'label': 'Full backup'},
}
AGE_MAGIC = b'age-encryption.org/v1'
# A small plain-text summary kept beside the backups, for whatever copies them
# elsewhere (see scripts/pull-backups.sh). It holds no financial data.
STATUS_FILENAME = 'backup-status.json'
# A scheduled backup counts as overdue this long after it was due
OVERDUE_AFTER = timedelta(hours=3)
# A run still marked "running" after this long was interrupted (e.g. a restart)
STALE_AFTER = timedelta(hours=6)
MAX_ATTEMPTS_PER_SLOT = 3


class BackupError(Exception):
    """A backup or restore step failed; the message is safe to show the user."""


def _now():
    # Separate function so tests can move this module's clock on its own
    return timezone.now()


def _worker_id():
    return f'{socket.gethostname()}:{os.getpid()}'


def _worker_is_gone(worker):
    """
    True when the process that was making a backup no longer exists.
    Only knowable for processes in this container; None means "cannot tell".
    """
    if not worker:
        return True  # recorded before workers were tracked; that process is long gone
    host, _, pid = worker.rpartition(':')
    if not host or host != socket.gethostname() or not pid.isdigit():
        return None
    try:
        os.kill(int(pid), 0)  # signal 0 only checks that the process exists
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def filename_prefix(kind):
    """All backups share one folder; the file name says which kind each is."""
    return f'finance-{kind}-'


def path_for(backup):
    return os.path.join(settings.BACKUP_DIR, backup.filename)


# ==========================================
# 1. DESTINATIONS
# ==========================================

class Destination:
    """
    Somewhere a finished backup file is stored. LocalFolder is the only one
    for now; an S3-compatible destination can implement the same two methods.
    """
    name = ''

    def store(self, source_path, kind, filename):
        raise NotImplementedError

    def delete(self, kind, filename):
        raise NotImplementedError


class LocalFolder(Destination):
    name = 'local'

    def store(self, source_path, kind, filename):
        os.makedirs(settings.BACKUP_DIR, exist_ok=True)
        target = os.path.join(settings.BACKUP_DIR, filename)
        # Same filesystem as the work folder, so this is an atomic rename:
        # a half-written file never appears under its final name.
        os.replace(source_path, target)
        return target

    def delete(self, kind, filename):
        try:
            os.remove(os.path.join(settings.BACKUP_DIR, filename))
        except FileNotFoundError:
            pass


def destinations():
    return [LocalFolder()]


# ==========================================
# 2. DATABASE DUMP / RESTORE (PostgreSQL)
# ==========================================

def _run(command, env):
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True)
    except FileNotFoundError:
        raise BackupError(f"'{command[0]}' is not installed in this container.")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip().splitlines()
        raise BackupError(f"{command[0]} failed: {detail[-1] if detail else 'no output'}")
    return result.stdout


def _pg():
    db = settings.DATABASES['default']
    if 'postgresql' not in db['ENGINE']:
        raise BackupError('Backups need a PostgreSQL database.')
    # The password goes in the environment, never on the command line
    env = {**os.environ, 'PGPASSWORD': str(db.get('PASSWORD') or '')}
    target = ['--host', str(db['HOST']), '--port', str(db['PORT']), '--username', str(db['USER'])]
    return db, env, target


def dump_database(path):
    """Writes the database to path and checks the result can be read back."""
    db, env, target = _pg()
    _run(['pg_dump', '--format=custom', '--no-owner', '--no-privileges', '--file', path, *target, db['NAME']], env)
    # A dump that pg_restore cannot list is not a backup
    _run(['pg_restore', '--list', path], env)


def restore_database(path):
    """Replaces the contents of the database with the dump at path."""
    db, env, target = _pg()
    _run(['pg_restore', '--clean', '--if-exists', '--no-owner', '--no-privileges',
          '--single-transaction', *target, '--dbname', db['NAME'], path], env)


# ==========================================
# 3. ENCRYPTION
# ==========================================

def generate_key(user=None):
    """
    Creates a new key pair, stores the public half and returns the private
    half. The private key is not kept anywhere: the caller must show it to the
    user, who has to save it to be able to open backups.
    """
    identity = pyrage.x25519.Identity.generate()
    config = BackupSettings.load()
    config.encryption_public_key = str(identity.to_public())
    config.encryption_disabled = False
    config.updated_by = user
    config.save()
    logger.warning("Backup encryption key generated by %s", user or 'system')
    write_status()
    return str(identity)


def disable_encryption(user=None):
    config = BackupSettings.load()
    config.encryption_public_key = ''
    config.encryption_disabled = True
    config.updated_by = user
    config.save()
    logger.warning("Backup encryption turned OFF by %s", user or 'system')
    write_status()


def _recipient(config):
    if not config.encryption_public_key:
        return None
    try:
        return pyrage.x25519.Recipient.from_str(config.encryption_public_key)
    except Exception:
        raise BackupError('The stored encryption key is not valid. Set up encryption again.')


# ==========================================
# 4. CREATING A BACKUP
# ==========================================

def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _media_files():
    """Yields (absolute path, path inside the archive) for every stored image."""
    root = settings.MEDIA_ROOT
    if not os.path.isdir(root):
        return
    for current, _dirs, files in os.walk(root, followlinks=False):
        for name in sorted(files):
            full = os.path.join(current, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            yield full, 'media/' + os.path.relpath(full, root).replace(os.sep, '/')


def _write_archive(dest_path, members, recipient):
    """Writes members [(path, archive name)] as a tar, encrypted if a recipient is given."""
    def add_all(tar):
        for path, name in members:
            tar.add(path, arcname=name, recursive=False)

    if recipient is None:
        with tarfile.open(dest_path, 'w') as tar:
            add_all(tar)
        return

    # Stream the tar straight into the encryptor through a pipe, so the
    # unencrypted archive never touches the disk and is never held in memory.
    read_fd, write_fd = os.pipe()
    failure = []

    def produce():
        try:
            with os.fdopen(write_fd, 'wb') as sink:
                with tarfile.open(fileobj=sink, mode='w|') as tar:
                    add_all(tar)
        except Exception as exc:
            failure.append(exc)

    producer = threading.Thread(target=produce, daemon=True)
    producer.start()
    try:
        with os.fdopen(read_fd, 'rb') as source, open(dest_path, 'wb') as out:
            pyrage.encrypt_io(source, out, [recipient])
    finally:
        producer.join()
    if failure:
        raise BackupError(f"Could not build the archive: {failure[0]}")


def create_backup(kind, trigger='manual', user=None, scheduled_for=None, record=None):
    """Makes one backup. Always returns the Backup record; check its status."""
    if kind not in KINDS:
        raise ValueError(f'Unknown backup kind: {kind}')
    record = record or Backup.objects.create(
        kind=kind, trigger=trigger, user=user, scheduled_for=scheduled_for, worker=_worker_id()
    )

    work_dir = None
    try:
        config = BackupSettings.load()
        if not config.encryption_ready:
            raise BackupError('Backups are not set up yet: set up encryption, or turn it off, in Settings.')
        recipient = _recipient(config)

        started = _now()
        work_root = os.path.join(settings.BACKUP_DIR, '.work')
        os.makedirs(work_root, exist_ok=True)
        work_dir = tempfile.mkdtemp(dir=work_root)

        dump_path = os.path.join(work_dir, 'db.dump')
        dump_database(dump_path)

        members = [(dump_path, 'db.dump')]
        media = None
        if kind == Backup.KIND_FULL:
            images = list(_media_files())
            members += images
            media = {'files': len(images), 'bytes': sum(os.path.getsize(path) for path, _ in images)}

        latest_migration = (
            MigrationRecorder.Migration.objects.filter(app='finance').order_by('-id').values_list('name', flat=True).first()
        )
        manifest = {
            'format': FORMAT_VERSION,
            'app': 'finance',
            'kind': kind,
            'created_at': started.isoformat(),
            'encrypted': recipient is not None,
            'database': {
                'name': settings.DATABASES['default']['NAME'],
                'dump_bytes': os.path.getsize(dump_path),
                'dump_sha256': _sha256(dump_path),
                'latest_migration': latest_migration,
            },
            'media': media,
        }
        manifest_path = os.path.join(work_dir, 'manifest.json')
        with open(manifest_path, 'w') as handle:
            json.dump(manifest, handle, indent=2)
        members.insert(0, (manifest_path, 'manifest.json'))  # first, so it can be read without unpacking everything

        filename = f"{filename_prefix(kind)}{started:%Y%m%d-%H%M%S}.tar" + ('.age' if recipient else '')
        staged = os.path.join(work_dir, filename)
        _write_archive(staged, members, recipient)

        record.size = os.path.getsize(staged)
        record.sha256 = _sha256(staged)
        record.filename = filename
        record.encrypted = recipient is not None
        for destination in destinations():
            destination.store(staged, kind, filename)

        record.status = Backup.OK
        logger.warning("Backup %s created (%s, %d bytes, encrypted=%s)", filename, trigger, record.size, record.encrypted)
    except Exception as exc:
        record.status = Backup.FAILED
        record.error = str(exc) if isinstance(exc, BackupError) else f'{type(exc).__name__}: {exc}'
        record.filename = ''
        logger.error("Backup (%s, %s) failed: %s", kind, trigger, record.error)
    finally:
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
        record.finished_at = _now()
        record.save()
        write_status()
    return record


def start_backup(kind, user=None):
    """Starts a manual backup in the background and returns its record at once."""
    if kind not in KINDS:
        raise ValueError(f'Unknown backup kind: {kind}')
    reconcile()  # clear out a run that died, so it cannot block new ones
    if Backup.objects.filter(status=Backup.RUNNING).exists():
        raise BackupError('A backup is already running. Wait for it to finish.')
    record = Backup.objects.create(kind=kind, trigger='manual', user=user, worker=_worker_id())

    def work():
        try:
            create_backup(kind, record=record)
        finally:
            connection.close()  # this thread's own database connection

    threading.Thread(target=work, daemon=True).start()
    return record


def delete_backup(backup):
    if backup.filename:
        for destination in destinations():
            destination.delete(backup.kind, backup.filename)
    backup.delete()
    write_status()


def _clear_abandoned_work():
    """
    Removes work folders left by a backup or restore that was killed mid-run.
    They can hold an unencrypted database dump, so they must not linger. The
    age limit keeps this away from anything that might still be in progress.
    """
    work_root = os.path.join(settings.BACKUP_DIR, '.work')
    if not os.path.isdir(work_root):
        return
    cutoff = (_now() - STALE_AFTER).timestamp()
    for entry in os.scandir(work_root):
        try:
            if entry.stat(follow_symlinks=False).st_mtime < cutoff:
                if entry.is_dir(follow_symlinks=False):
                    shutil.rmtree(entry.path, ignore_errors=True)
                else:
                    os.remove(entry.path)
        except OSError:
            pass


def reconcile():
    """Brings the records in line with reality before they are shown."""
    _clear_abandoned_work()
    # A run that never finished, because its process was restarted mid-backup.
    # If that process was in this container we can see it is gone and say so
    # at once; otherwise (the scheduler's container) fall back to a time limit.
    for backup in Backup.objects.filter(status=Backup.RUNNING):
        gone = _worker_is_gone(backup.worker)
        if gone or (gone is None and backup.started_at < _now() - STALE_AFTER):
            backup.status = Backup.FAILED
            backup.error = 'Interrupted before it finished (the app was restarted). No backup file was kept.'
            backup.finished_at = _now()
            backup.save(update_fields=['status', 'error', 'finished_at'])
    # Files removed by a retention policy or by hand
    for backup in Backup.objects.filter(status=Backup.OK):
        if not os.path.exists(path_for(backup)):
            backup.delete()


# ==========================================
# 5. SCHEDULE
# ==========================================

def _schedule(config, kind):
    prefix = 'db' if kind == Backup.KIND_DB else 'full'
    return {name: getattr(config, f'{prefix}_{name}') for name in ('frequency', 'hour', 'weekday', 'monthday')}


def latest_slot(schedule, now):
    """The most recent moment a backup was due under this schedule, or None if it is off."""
    frequency = schedule['frequency']
    if frequency == BackupSettings.OFF:
        return None
    local = timezone.localtime(now)
    slot = local.replace(hour=min(schedule['hour'], 23), minute=0, second=0, microsecond=0)

    if frequency == BackupSettings.DAILY:
        if slot > local:
            slot -= timedelta(days=1)
    elif frequency == BackupSettings.WEEKLY:
        slot -= timedelta(days=(local.weekday() - schedule['weekday']) % 7)
        if slot > local:
            slot -= timedelta(days=7)
    elif frequency == BackupSettings.MONTHLY:
        day = min(max(schedule['monthday'], 1), 28)
        slot = slot.replace(day=day)
        if slot > local:
            # Step back to the same day of the previous month
            previous = slot.replace(day=1) - timedelta(days=1)
            slot = previous.replace(day=day)
    else:
        return None
    return slot


def run_due_backups():
    """Runs each scheduled backup whose time has come. Called hourly by the scheduler."""
    config = BackupSettings.load()
    if not config.encryption_ready:
        write_status()
        return []
    done = []
    for kind in KINDS:
        slot = latest_slot(_schedule(config, kind), _now())
        # Do not reach back before the schedule was last saved
        if slot is None or slot < config.updated_at:
            continue
        attempts = Backup.objects.filter(kind=kind, trigger='scheduled', scheduled_for=slot)
        if attempts.filter(status=Backup.OK).exists() or attempts.count() >= MAX_ATTEMPTS_PER_SLOT:
            continue
        reconcile()
        if Backup.objects.filter(status=Backup.RUNNING).exists():
            continue
        done.append(create_backup(kind, trigger='scheduled', scheduled_for=slot))
    # Hourly heartbeat, whether or not anything was due
    write_status()
    return done


def health(details=True):
    """One overall status for the app to show: setup, off, ok, failed or overdue.

    details: include why the last backup failed. Its error can carry internal
    text (a database message, a file path), so only admins get it."""
    config = BackupSettings.load()
    if not config.encryption_ready:
        return {'state': 'setup', 'message': 'Backups are not set up yet. Set up encryption to start.'}

    now = _now()
    problems, enabled = [], 0
    for kind, info in KINDS.items():
        schedule = _schedule(config, kind)
        slot = latest_slot(schedule, now)
        if slot is None:
            continue
        enabled += 1
        # A slot that has only just passed is still being given its chance
        # to run; judge by the one before it, or a schedule that has been
        # failing for days would look fine for a while after every slot.
        if now - slot <= OVERDUE_AFTER:
            slot = latest_slot(schedule, slot - timedelta(seconds=1))
            if slot is None:
                continue
        last = Backup.objects.filter(kind=kind).exclude(status=Backup.RUNNING).first()
        # The slot is covered by the scheduled run made for it, or by any
        # backup (a manual one, say) made since.
        good = Backup.objects.filter(kind=kind, status=Backup.OK)
        covered = good.filter(scheduled_for__gte=slot).exists() or good.filter(started_at__gte=slot).exists()
        if last and last.status == Backup.FAILED:
            why = f": {last.error}" if details else ". An admin can see why in Settings → Backups."
            problems.append(('failed', f"The last {info['label'].lower()} failed{why}"))
        elif slot >= config.updated_at and not covered:
            problems.append(('overdue', f"The {info['label'].lower()} due {timezone.localtime(slot):%d %b %H:%M} has not run."))

    if problems:
        state = 'failed' if any(kind == 'failed' for kind, _ in problems) else 'overdue'
        return {'state': state, 'message': ' '.join(message for _, message in problems)}
    if not enabled:
        return {'state': 'off', 'message': 'Scheduled backups are turned off.'}
    return {'state': 'ok', 'message': 'Scheduled backups are running normally.'}


def write_status():
    """
    Writes backup-status.json into the backups folder: the schedule, the
    newest good backup of each kind, and this app's own verdict on its health.
    A tool that copies backups elsewhere reads it to know what to expect, so
    it never has to guess the schedule or log in to the app. Refreshed hourly
    by the scheduler, so its age also shows whether the scheduler is alive.
    Never raises: a status note must not be able to break a backup.
    """
    try:
        config = BackupSettings.load()
        kinds = {}
        for kind, info in KINDS.items():
            latest = None
            for backup in Backup.objects.filter(kind=kind, status=Backup.OK)[:5]:
                if os.path.exists(path_for(backup)):
                    latest = {
                        'filename': backup.filename,
                        'made_at': backup.started_at.isoformat(),
                        'bytes': backup.size,
                        'sha256': backup.sha256,
                        'encrypted': backup.encrypted,
                    }
                    break
            kinds[kind] = {'label': info['label'], **_schedule(config, kind), 'latest': latest}

        status = {
            'format': 1,
            'app': 'finance',
            'written_at': _now().isoformat(),
            'timezone': settings.TIME_ZONE,
            'health': health(),
            'kinds': kinds,
        }
        os.makedirs(settings.BACKUP_DIR, exist_ok=True)
        # Write to one side, then swap in, so a reader never sees half a file
        handle, temporary = tempfile.mkstemp(dir=settings.BACKUP_DIR, prefix='.status-')
        with os.fdopen(handle, 'w') as out:
            json.dump(status, out, indent=2)
        os.chmod(temporary, 0o644)
        os.replace(temporary, os.path.join(settings.BACKUP_DIR, STATUS_FILENAME))
    except Exception as exc:
        logger.error("Could not write %s: %s", STATUS_FILENAME, exc)


# ==========================================
# 6. RESTORE
# ==========================================

def _read_identity(identity_path):
    try:
        with open(identity_path) as handle:
            keys = [line.strip() for line in handle if line.strip().startswith('AGE-SECRET-KEY-')]
    except OSError as exc:
        raise BackupError(f'Could not read the key file: {exc}')
    if not keys:
        raise BackupError('The key file contains no private key (a line starting with AGE-SECRET-KEY-).')
    try:
        return pyrage.x25519.Identity.from_str(keys[0])
    except Exception:
        raise BackupError('The private key in the key file is not valid.')


def unpack(backup_path, work_dir, identity_path=None):
    """Decrypts and unpacks a backup into work_dir, checks it, and returns its manifest."""
    with open(backup_path, 'rb') as handle:
        encrypted = handle.read(len(AGE_MAGIC)) == AGE_MAGIC

    tar_path = backup_path
    if encrypted:
        if not identity_path:
            raise BackupError('This backup is encrypted. Give the file holding your private key with --identity.')
        identity = _read_identity(identity_path)
        tar_path = os.path.join(work_dir, 'backup.tar')
        try:
            with open(backup_path, 'rb') as source, open(tar_path, 'wb') as out:
                pyrage.decrypt_io(source, out, [identity])
        except Exception:
            raise BackupError('Could not decrypt the backup. The key does not match, or the file is damaged.')

    content = os.path.join(work_dir, 'content')
    os.makedirs(content)
    try:
        with tarfile.open(tar_path, 'r') as tar:
            for member in tar:
                name = member.name
                allowed = name in ('manifest.json', 'db.dump') or name.startswith('media/')
                unsafe = name.startswith('/') or '..' in name.split('/')
                # Only plain files at the expected names: never links or devices
                if not allowed or unsafe or not member.isfile():
                    raise BackupError(f'Unexpected entry in the backup: {name}')
                target = os.path.join(content, *name.split('/'))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with tar.extractfile(member) as source, open(target, 'wb') as out:
                    shutil.copyfileobj(source, out)
    except tarfile.TarError:
        raise BackupError('The file is not a valid backup archive.')

    try:
        with open(os.path.join(content, 'manifest.json')) as handle:
            manifest = json.load(handle)
    except (OSError, ValueError):
        raise BackupError('The backup has no readable manifest.')
    if manifest.get('app') != 'finance' or manifest.get('format') != FORMAT_VERSION:
        raise BackupError('This backup was made by a different or newer version and cannot be restored here.')

    dump_path = os.path.join(content, 'db.dump')
    if not os.path.exists(dump_path) or _sha256(dump_path) != manifest['database']['dump_sha256']:
        raise BackupError('The database dump in this backup is missing or damaged (checksum mismatch).')
    return manifest, content


def restore(backup_path, identity_path=None, database_only=False):
    """Replaces the database, and for a full backup the receipt images, with the backup's contents."""
    work_root = os.path.join(settings.BACKUP_DIR, '.work')
    os.makedirs(work_root, exist_ok=True)
    work_dir = tempfile.mkdtemp(dir=work_root)
    try:
        manifest, content = unpack(backup_path, work_dir, identity_path)
        restore_database(os.path.join(content, 'db.dump'))

        images = 0
        media_dir = os.path.join(content, 'media')
        if not database_only and os.path.isdir(media_dir):
            for current, _dirs, files in os.walk(media_dir):
                for name in files:
                    source = os.path.join(current, name)
                    target = os.path.join(settings.MEDIA_ROOT, os.path.relpath(source, media_dir))
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    shutil.copy2(source, target)
                    images += 1
        logger.warning("Restored backup %s (%d images)", os.path.basename(backup_path), images)
        return {'manifest': manifest, 'images_restored': images}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
