# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
"""
Tests for storage retention and backups.

Run with:  python manage.py test finance

The two steps that need a real PostgreSQL server (pg_dump and pg_restore) are
replaced with stand-ins here; everything around them runs for real, including
encryption, the archive format and the restore safety checks.
"""
import io
import json
import os
import shutil
import tarfile
import tempfile
import time
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

import pyrage
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from . import backups, retention
from .models import Account, Backup, BackupSettings, Receipt, RetentionRun, Transaction

DUMP = b'PGDMP-stand-in-database-dump ' * 2000


def fake_dump(path):
    with open(path, 'wb') as handle:
        handle.write(DUMP)


class StorageTestCase(TestCase):
    """Gives each test its own data folders, an admin, a regular user, and stand-ins for pg_dump/pg_restore."""

    def setUp(self):
        # The everyday settings (finance/system.py) are remembered between
        # requests: start from the database, and remembered, so query counts are steady
        from finance import system
        system.forget_settings()
        system.current()
        self.addCleanup(system.forget_settings)
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.media = os.path.join(self.root, 'media')
        self.archive = os.path.join(self.root, 'archive')
        self.backup_dir = os.path.join(self.root, 'backups')
        for folder in (self.media, self.backup_dir, *(os.path.join(self.archive, sub) for sub in ('receipts', 'failed', 'qfx'))):
            os.makedirs(folder)
        self.dropzone = os.path.join(self.root, 'dropzone')
        overrides = override_settings(
            MEDIA_ROOT=self.media, ARCHIVE_ROOT=self.archive, BACKUP_DIR=self.backup_dir,
            DROPZONE_DIR=self.dropzone, UPLOAD_MAX_MB=1,
        )
        overrides.enable()
        self.addCleanup(overrides.disable)

        self.restored = []
        for target, replacement in (
            ('finance.backups.dump_database', fake_dump),
            ('finance.backups.restore_database', lambda path: self.restored.append(open(path, 'rb').read())),
        ):
            patcher = mock.patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

        users = get_user_model().objects
        users.create_superuser('admin', 'admin@example.com', 'pw-12345-x')
        users.create_user('member', 'member@example.com', 'pw-12345-x')
        self.client.login(username='admin', password='pw-12345-x')

    def api(self, method, url, body=None):
        if method == 'get':
            return self.client.get(url)
        return getattr(self.client, method)(url, data=json.dumps(body or {}), content_type='application/json')

    def as_member(self):
        self.client.logout()
        self.client.login(username='member', password='pw-12345-x')

    def later(self, module, **delta):
        """Moves one module's clock forward (retention or backups), leaving sessions alone."""
        return mock.patch(f'finance.{module}._now', return_value=timezone.now() + timedelta(**delta))

    def put_file(self, folder, name, size=1000):
        path = os.path.join(folder, name)
        with open(path, 'wb') as handle:
            handle.write(b'x' * size)
        return path

    def backup_names(self, prefix='finance-'):
        return sorted(name for name in os.listdir(self.backup_dir) if name.startswith(prefix))

    def policies(self):
        return {c['key']: c for c in self.api('get', '/api/retention/').json()['categories']}


class RetentionTests(StorageTestCase):

    def test_defaults_keep_everything_except_old_backups(self):
        policies = self.policies()
        for key in ('receipt_images', 'receipt_archive', 'failed_receipts', 'bank_files'):
            self.assertIsNone(policies[key]['retention_days'], key)
        self.assertEqual(policies['db_backups']['retention_days'], 30)
        self.assertEqual(policies['full_backups']['retention_days'], 30)
        for category in policies.values():
            self.assertTrue(category['deletes'] and category['keeps'])

    def test_default_policies_never_delete_archives(self):
        files = [self.put_file(os.path.join(self.archive, sub), 'a.jpg') for sub in ('receipts', 'failed', 'qfx')]
        with self.later('retention', days=36500):
            result = self.api('post', '/api/retention/run/').json()
        self.assertEqual(result['files'], 0)
        self.assertTrue(all(os.path.exists(path) for path in files))
        self.assertEqual(RetentionRun.objects.count(), 0)

    def test_only_staff_can_change_policies_or_run(self):
        self.as_member()
        self.assertFalse(self.api('get', '/api/retention/').json()['can_edit'])
        self.assertEqual(self.api('put', '/api/retention/', {'policies': {'bank_files': 1}}).status_code, 403)
        self.assertEqual(self.api('post', '/api/retention/run/').status_code, 403)

    def test_invalid_policies_are_rejected(self):
        for value in (0, -5, 'soon', True, 1.5):
            response = self.api('put', '/api/retention/', {'policies': {'bank_files': value}})
            self.assertEqual(response.status_code, 400, value)
        self.assertEqual(self.api('put', '/api/retention/', {'policies': {'everything': 30}}).status_code, 400)

    def test_archive_policy_deletes_only_its_own_category(self):
        receipts = os.path.join(self.archive, 'receipts')
        expired = [self.put_file(receipts, f'r{i}.jpg', 2000) for i in range(3)]
        kept = [self.put_file(os.path.join(self.archive, 'qfx'), 'bank.qfx'), self.put_file(os.path.join(self.archive, 'failed'), 'f.jpg')]
        outside = self.put_file(self.root, 'outside.txt')
        os.symlink(outside, os.path.join(receipts, 'link.jpg'))

        self.api('put', '/api/retention/', {'policies': {'receipt_archive': 30}})
        with self.later('retention', days=40):
            preview = self.api('post', '/api/retention/preview/').json()
            self.assertEqual(preview['files'], 3)
            self.assertTrue(all(os.path.exists(path) for path in expired), 'a preview must not delete')
            result = self.api('post', '/api/retention/run/').json()

        self.assertEqual((result['files'], result['bytes']), (3, 6000))
        self.assertFalse(any(os.path.exists(path) for path in expired))
        self.assertTrue(all(os.path.exists(path) for path in kept + [outside]))
        self.assertTrue(os.path.islink(os.path.join(receipts, 'link.jpg')), 'links are never followed or removed')
        self.assertEqual(RetentionRun.objects.get().files_deleted, 3)

    def test_expired_receipt_image_keeps_its_record(self):
        account = Account.objects.create(name='Card')
        transaction = Transaction.objects.create(account=account, fitid='T1', dtposted=date(2015, 3, 14), trnamt=Decimal('-107.00'), name='Hardware')
        receipt = Receipt(original_filename='old.jpg', date=date(2015, 3, 14), total_amount=Decimal('107.00'), tax_amount=Decimal('7.00'), transaction=transaction)
        receipt.image.save('old.jpg', ContentFile(b'i' * 3000))
        recent = Receipt(original_filename='new.jpg', date=timezone.now().date(), total_amount=Decimal('20.00'))
        recent.image.save('new.jpg', ContentFile(b'i' * 3000))
        image_path, recent_path = receipt.image.path, recent.image.path

        self.api('put', '/api/retention/', {'policies': {'receipt_images': 365 * 7}})
        self.assertEqual(self.api('post', '/api/retention/run/').json()['files'], 1)

        receipt.refresh_from_db()
        self.assertFalse(os.path.exists(image_path))
        self.assertFalse(receipt.image)
        self.assertIsNotNone(receipt.image_expired_at)
        self.assertEqual((receipt.total_amount, receipt.tax_amount, receipt.transaction_id), (Decimal('107.00'), Decimal('7.00'), transaction.id))
        self.assertTrue(os.path.exists(recent_path))
        shown = self.api('get', f'/api/transactions/{transaction.id}/').json()
        self.assertIsNone(shown['receipt_image'])
        self.assertEqual(shown['receipt_expired']['total_amount'], '107.00')

    def test_backup_retention_always_keeps_the_two_newest(self):
        """A backup schedule can be slower than the retention period, and a backup can fail."""
        for kind, count in (('db', 4), ('full', 1)):
            for index in range(count):
                self.put_file(self.backup_dir, f'finance-{kind}-2026010{index + 1}-020000.tar.age')
                # distinct ages, oldest first
                stamp = (timezone.now() - timedelta(hours=count - index)).timestamp()
                os.utime(os.path.join(self.backup_dir, f'finance-{kind}-2026010{index + 1}-020000.tar.age'), (stamp, stamp))
        key_file = self.put_file(self.backup_dir, 'my-key.txt')

        with self.later('retention', days=400):
            preview = {c['key']: c for c in self.api('post', '/api/retention/preview/').json()['categories']}
            self.assertEqual(preview['db_backups']['files'], 2)
            self.assertEqual(preview['full_backups']['files'], 0)
            self.api('post', '/api/retention/run/')

        self.assertEqual(self.backup_names('finance-db-'), ['finance-db-20260103-020000.tar.age', 'finance-db-20260104-020000.tar.age'])
        self.assertEqual(len(self.backup_names('finance-full-')), 1, 'a lone backup is never deleted, however old')
        self.assertTrue(os.path.exists(key_file), 'other files in the backups folder are never deleted')

    def test_saved_keep_forever_overrides_the_backup_default(self):
        for index in range(4):
            self.put_file(self.backup_dir, f'finance-db-2026010{index + 1}-020000.tar.age')
        self.api('put', '/api/retention/', {'policies': {'db_backups': None}})
        with self.later('retention', days=400):
            self.api('post', '/api/retention/run/')
        self.assertEqual(len(self.backup_names('finance-db-')), 4)


class BackupTests(StorageTestCase):

    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.media, 'receipts'))
        self.images = {'receipts/a.jpg': b'A' * 5000, 'receipts/b.jpg': b'B' * 7000}
        for name, data in self.images.items():
            with open(os.path.join(self.media, name), 'wb') as handle:
                handle.write(data)

    def set_up_encryption(self):
        response = self.api('post', '/api/backups/encryption/', {'action': 'generate'})
        self.assertEqual(response.status_code, 200)
        return response.json()['private_key']

    def key_file(self, private_key, name='key.txt'):
        path = os.path.join(self.root, name)
        with open(path, 'w') as handle:
            handle.write(f'# a comment line\n{private_key}\n')
        return path

    def contents(self, path, private_key=None):
        with open(path, 'rb') as handle:
            raw = handle.read()
        if private_key:
            out = io.BytesIO()
            pyrage.decrypt_io(io.BytesIO(raw), out, [pyrage.x25519.Identity.from_str(private_key)])
            raw = out.getvalue()
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            return {member.name: tar.extractfile(member).read() for member in tar.getmembers()}

    def test_nothing_runs_until_encryption_is_decided(self):
        state = self.api('get', '/api/backups/').json()
        self.assertEqual(state['health']['state'], 'setup')
        self.assertEqual(self.api('post', '/api/backups/run/', {'kind': 'db'}).status_code, 400)
        with self.later('backups', days=40):
            self.assertEqual(backups.run_due_backups(), [])
        self.assertEqual(backups.create_backup('db').status, Backup.FAILED)
        self.assertEqual(self.backup_names(), [])

    def test_only_staff_can_manage_backups(self):
        self.as_member()
        self.assertFalse(self.api('get', '/api/backups/').json()['can_edit'])
        for method, url, body in (
            ('post', '/api/backups/encryption/', {'action': 'generate'}),
            ('post', '/api/backups/run/', {'kind': 'db'}),
            ('put', '/api/backups/schedule/', {'schedule': {'db': {'frequency': 'off'}}}),
            ('get', '/api/backups/1/', None),
            ('delete', '/api/backups/1/', None),
        ):
            self.assertEqual(self.api(method, url, body).status_code, 403, f'{method} {url}')

    def test_private_key_is_returned_once_and_never_stored(self):
        private_key = self.set_up_encryption()
        self.assertTrue(private_key.startswith('AGE-SECRET-KEY-'))
        config = BackupSettings.load()
        self.assertEqual(str(pyrage.x25519.Identity.from_str(private_key).to_public()), config.encryption_public_key)
        self.assertNotIn(private_key, json.dumps(list(BackupSettings.objects.values()), default=str))
        self.assertNotIn('private_key', self.api('get', '/api/backups/').json())

    def test_database_backup_is_encrypted_verified_and_complete(self):
        private_key = self.set_up_encryption()
        record = backups.create_backup('db')
        self.assertEqual(record.status, Backup.OK, record.error)
        path = backups.path_for(record)

        self.assertEqual(os.path.dirname(path), self.backup_dir)
        self.assertTrue(record.filename.startswith('finance-db-') and record.filename.endswith('.tar.age'))
        with open(path, 'rb') as handle:
            raw = handle.read()
        self.assertTrue(raw.startswith(backups.AGE_MAGIC))
        self.assertNotIn(DUMP[:29], raw, 'the dump must not be readable in the file')
        self.assertEqual((record.size, record.sha256), (len(raw), backups._sha256(path)))
        self.assertEqual(os.listdir(os.path.join(self.backup_dir, '.work')), [])

        contents = self.contents(path, private_key)
        self.assertEqual(sorted(contents), ['db.dump', 'manifest.json'])
        self.assertEqual(contents['db.dump'], DUMP)
        manifest = json.loads(contents['manifest.json'])
        self.assertEqual((manifest['app'], manifest['kind'], manifest['media']), ('finance', 'db', None))
        self.assertEqual(manifest['database']['dump_sha256'], backups.hashlib.sha256(DUMP).hexdigest())

        with self.assertRaises(Exception):
            self.contents(path, str(pyrage.x25519.Identity.generate()))

    def test_full_backup_round_trip_restores_database_and_images(self):
        private_key = self.set_up_encryption()
        record = backups.create_backup('full')
        self.assertEqual(record.status, Backup.OK, record.error)
        path = backups.path_for(record)
        self.assertTrue(record.filename.startswith('finance-full-'))
        contents = self.contents(path, private_key)
        self.assertEqual({name[len('media/'):]: data for name, data in contents.items() if name.startswith('media/')}, self.images)

        for name in self.images:
            os.remove(os.path.join(self.media, name))

        with self.assertRaisesMessage(backups.BackupError, 'encrypted'):
            backups.restore(path)
        with self.assertRaisesMessage(backups.BackupError, 'Could not decrypt'):
            backups.restore(path, identity_path=self.key_file(str(pyrage.x25519.Identity.generate()), 'wrong.txt'))
        self.assertEqual(self.restored, [], 'a refused restore must change nothing')

        result = backups.restore(path, identity_path=self.key_file(private_key))
        self.assertEqual(self.restored, [DUMP])
        self.assertEqual(result['images_restored'], 2)
        for name, data in self.images.items():
            with open(os.path.join(self.media, name), 'rb') as handle:
                self.assertEqual(handle.read(), data)
        self.assertEqual(os.listdir(os.path.join(self.backup_dir, '.work')), [])

    def make_archive(self, name, extra=(), manifest=None, link=False):
        path = os.path.join(self.root, name)
        described = {'format': 1, 'app': 'finance', 'kind': 'db', 'created_at': 'x', 'media': None,
                     'database': {'dump_sha256': backups.hashlib.sha256(DUMP).hexdigest()}}
        described.update(manifest or {})
        with tarfile.open(path, 'w') as tar:
            for member, data in [('manifest.json', json.dumps(described).encode()), ('db.dump', DUMP), *extra]:
                info = tarfile.TarInfo(member)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            if link:
                info = tarfile.TarInfo('media/evil')
                info.type, info.linkname = tarfile.SYMTYPE, '/etc/passwd'
                tar.addfile(info)
        return path

    def test_restore_refuses_unsafe_or_foreign_archives(self):
        cases = {
            'Unexpected entry': [
                self.make_archive('escape.tar', [('media/../../evil.txt', b'x')]),
                self.make_archive('extra.tar', [('etc/passwd', b'x')]),
                self.make_archive('link.tar', link=True),
            ],
            'different or newer version': [
                self.make_archive('other.tar', manifest={'app': 'something-else'}),
                self.make_archive('newer.tar', manifest={'format': 99}),
            ],
            'checksum': [self.make_archive('badsum.tar', manifest={'database': {'dump_sha256': '0' * 64}})],
        }
        for message, paths in cases.items():
            for path in paths:
                with self.assertRaisesMessage(backups.BackupError, message):
                    backups.restore(path)
        self.assertEqual(self.restored, [])
        self.assertFalse(os.path.exists(os.path.join(self.root, 'evil.txt')))
        # and a well-formed plain archive is accepted
        self.assertEqual(backups.restore(self.make_archive('good.tar', [('media/receipts/x.jpg', b'img')]))['images_restored'], 1)

    def test_turning_encryption_off_must_be_confirmed(self):
        self.set_up_encryption()
        self.assertEqual(self.api('post', '/api/backups/encryption/', {'action': 'disable'}).status_code, 400)
        self.assertEqual(self.api('post', '/api/backups/encryption/', {'action': 'disable', 'confirm': True}).status_code, 200)
        record = backups.create_backup('db')
        self.assertTrue(record.filename.endswith('.tar') and not record.encrypted)
        self.assertEqual(self.contents(backups.path_for(record))['db.dump'], DUMP)

    def test_failed_dump_is_recorded_and_leaves_nothing_behind(self):
        self.set_up_encryption()
        with mock.patch('finance.backups.dump_database', side_effect=backups.BackupError('pg_dump failed: connection refused')):
            record = backups.create_backup('db')
        self.assertEqual(record.status, Backup.FAILED)
        self.assertIn('connection refused', record.error)
        self.assertEqual(self.backup_names(), [])
        self.assertEqual(os.listdir(os.path.join(self.backup_dir, '.work')), [])
        self.assertEqual(backups.health()['state'], 'failed')

    def test_why_a_backup_failed_is_shown_to_admins_only(self):
        self.set_up_encryption()
        with mock.patch('finance.backups.dump_database', side_effect=backups.BackupError('pg_dump failed: secret internal detail')):
            backups.create_backup('db')
        admin_view = self.client.get('/api/backups/').json()
        self.assertIn('secret internal detail', admin_view['health']['message'])
        self.assertIn('secret internal detail', admin_view['backups'][0]['error'])
        self.as_member()
        for answer in (self.client.get('/api/backups/').json(), self.client.get('/api/backups/health/').json()):
            self.assertNotIn('secret internal detail', json.dumps(answer))
        member_view = self.client.get('/api/backups/').json()
        self.assertEqual(member_view['health']['state'], 'failed', 'members still learn that it failed')
        self.assertIn('An admin can see why', member_view['health']['message'])

    def test_interrupted_backup_is_detected_and_does_not_block(self):
        self.set_up_encryption()
        import socket
        import subprocess
        import sys
        finished = subprocess.Popen([sys.executable, '-c', 'pass'])
        finished.wait()
        host = socket.gethostname()
        dead = Backup.objects.create(kind='db', status=Backup.RUNNING, worker=f'{host}:{finished.pid}')
        alive = Backup.objects.create(kind='db', status=Backup.RUNNING, worker=f'{host}:{os.getpid()}')
        elsewhere = Backup.objects.create(kind='db', status=Backup.RUNNING, worker='another-container:12')
        legacy = Backup.objects.create(kind='db', status=Backup.RUNNING, worker='')

        backups.reconcile()
        status = lambda record: Backup.objects.get(pk=record.pk).status
        self.assertEqual(status(dead), Backup.FAILED)
        self.assertEqual(status(legacy), Backup.FAILED)
        self.assertEqual(status(alive), Backup.RUNNING)
        self.assertEqual(status(elsewhere), Backup.RUNNING)
        with self.assertRaises(backups.BackupError):
            backups.start_backup('db')  # one really is still running

        Backup.objects.filter(pk=elsewhere.pk).update(started_at=timezone.now() - timedelta(hours=7))
        backups.reconcile()
        self.assertEqual(status(elsewhere), Backup.FAILED, 'falls back to a time limit for other containers')

    def test_abandoned_work_folders_are_removed(self):
        work = os.path.join(self.backup_dir, '.work')
        old, recent = os.path.join(work, 'killed'), os.path.join(work, 'in-progress')
        for folder in (old, recent):
            os.makedirs(folder)
            self.put_file(folder, 'db.dump')
        stamp = (timezone.now() - timedelta(hours=7)).timestamp()
        os.utime(old, (stamp, stamp))
        backups.reconcile()
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(recent))

    def test_schedule_slots(self):
        at = lambda *parts: timezone.make_aware(timezone.datetime(*parts))
        slot = lambda frequency, now: backups.latest_slot({'frequency': frequency, 'hour': 2, 'weekday': 6, 'monthday': 15}, now)
        self.assertEqual(slot('daily', at(2026, 10, 14, 9)), at(2026, 10, 14, 2))
        self.assertEqual(slot('daily', at(2026, 10, 14, 1, 59)), at(2026, 10, 13, 2))
        self.assertEqual(slot('weekly', at(2026, 10, 14, 9)), at(2026, 10, 11, 2))
        self.assertEqual(slot('weekly', at(2026, 10, 11, 1)), at(2026, 10, 4, 2))
        self.assertEqual(slot('monthly', at(2026, 10, 20, 9)), at(2026, 10, 15, 2))
        self.assertEqual(slot('monthly', at(2026, 10, 3, 9)), at(2026, 9, 15, 2))
        self.assertEqual(slot('monthly', at(2026, 1, 3, 9)), at(2025, 12, 15, 2))
        self.assertIsNone(slot('off', at(2026, 10, 14, 9)))

    def test_scheduler_runs_what_is_due_once_and_limits_retries(self):
        self.set_up_encryption()
        response = self.api('put', '/api/backups/schedule/', {'schedule': {'db': {'frequency': 'daily', 'hour': 2}, 'full': {'frequency': 'off'}}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(backups.run_due_backups(), [], 'nothing is run retroactively after saving')

        with self.later('backups', days=2):
            made = backups.run_due_backups()
            self.assertEqual([(b.kind, b.status, b.trigger) for b in made], [('db', Backup.OK, 'scheduled')])
            self.assertEqual(backups.run_due_backups(), [], 'no duplicate for the same slot')
            self.assertEqual(backups.health()['state'], 'ok')

        with self.later('backups', days=5), mock.patch('finance.backups.dump_database', side_effect=backups.BackupError('down')):
            attempts = [len(backups.run_due_backups()) for _ in range(5)]
            self.assertEqual(attempts, [1, 1, 1, 0, 0], 'a failing slot is retried at most three times')
            self.assertEqual(backups.health()['state'], 'failed')

        Backup.objects.filter(status=Backup.FAILED).delete()
        with self.later('backups', days=9):
            self.assertEqual(backups.health()['state'], 'overdue')

    def test_a_long_missed_schedule_is_overdue_even_just_after_a_slot(self):
        # Used to read "ok" for three hours after every slot, whatever had
        # been missed before it (and so failed at certain times of day)
        from datetime import datetime, timezone as dt_timezone
        self.set_up_encryption()
        self.api('put', '/api/backups/schedule/', {'schedule': {'db': {'frequency': 'daily', 'hour': 2}, 'full': {'frequency': 'off'}}})
        BackupSettings.objects.update(updated_at=datetime(2026, 3, 1, tzinfo=dt_timezone.utc))
        for hour, minute in ((2, 10), (4, 0), (1, 0)):
            moment = datetime(2026, 3, 9, hour, minute, tzinfo=dt_timezone.utc)
            with mock.patch('finance.backups._now', return_value=moment):
                self.assertEqual(backups.health()['state'], 'overdue', f'{hour:02}:{minute:02}')
        # Just after a slot, with yesterday's backup made: fine, today's still has time
        Backup.objects.create(kind='db', status=Backup.OK, trigger='scheduled', filename='x.tar',
                              scheduled_for=datetime(2026, 3, 8, 2, 0, tzinfo=dt_timezone.utc))
        with mock.patch('finance.backups._now', return_value=datetime(2026, 3, 9, 2, 10, tzinfo=dt_timezone.utc)):
            self.assertEqual(backups.health()['state'], 'ok')

    def test_invalid_schedules_are_rejected(self):
        for schedule in ({'db': {'frequency': 'hourly'}}, {'db': {'hour': 24}}, {'full': {'weekday': 7}}, {'full': {'monthday': 31}}, {'db': {'hour': '2'}}):
            self.assertEqual(self.api('put', '/api/backups/schedule/', {'schedule': schedule}).status_code, 400, schedule)

    def read_status(self):
        with open(os.path.join(self.backup_dir, backups.STATUS_FILENAME)) as handle:
            return json.load(handle)

    def test_status_note_describes_schedule_latest_backups_and_health(self):
        private_key = self.set_up_encryption()
        status = self.read_status()
        self.assertEqual(status['health']['state'], 'ok')
        self.assertEqual({kind: info['frequency'] for kind, info in status['kinds'].items()}, {'db': 'daily', 'full': 'weekly'})
        self.assertIsNone(status['kinds']['db']['latest'])

        record = backups.create_backup('db')
        latest = self.read_status()['kinds']['db']['latest']
        self.assertEqual((latest['filename'], latest['sha256'], latest['bytes'], latest['encrypted']),
                         (record.filename, record.sha256, record.size, True))
        self.assertIsNone(self.read_status()['kinds']['full']['latest'])

        self.api('put', '/api/backups/schedule/', {'schedule': {'db': {'frequency': 'monthly', 'monthday': 5}, 'full': {'frequency': 'off'}}})
        kinds = self.read_status()['kinds']
        self.assertEqual((kinds['db']['frequency'], kinds['db']['monthday'], kinds['full']['frequency']), ('monthly', 5, 'off'))

        with mock.patch('finance.backups.dump_database', side_effect=backups.BackupError('pg_dump failed: down')):
            backups.create_backup('db')
        status = self.read_status()
        self.assertEqual(status['health']['state'], 'failed')
        self.assertEqual(status['kinds']['db']['latest']['filename'], record.filename, 'the last good backup is still the latest')

        self.api('delete', f'/api/backups/{record.id}/')
        self.assertIsNone(self.read_status()['kinds']['db']['latest'])

        raw = open(os.path.join(self.backup_dir, backups.STATUS_FILENAME)).read()
        self.assertNotIn(private_key, raw)
        self.assertNotIn('AGE-SECRET-KEY', raw)

    def test_status_note_is_refreshed_hourly_and_survives_retention(self):
        self.set_up_encryption()
        with self.later('backups', hours=5):
            backups.run_due_backups()
            written = timezone.datetime.fromisoformat(self.read_status()['written_at'])
        self.assertGreater(written, timezone.now() + timedelta(hours=4), 'the hourly check rewrites the note even when nothing is due')

        with self.later('retention', days=4000):
            self.api('post', '/api/retention/run/')
        self.assertTrue(os.path.exists(os.path.join(self.backup_dir, backups.STATUS_FILENAME)))
        leftovers = [name for name in os.listdir(self.backup_dir) if name.startswith('.status-')]
        self.assertEqual(leftovers, [], 'no half-written status files are left behind')

    def test_status_note_failure_never_breaks_a_backup(self):
        self.set_up_encryption()
        # health() is only used by the status note, so this breaks the note alone
        with mock.patch('finance.backups.health', side_effect=RuntimeError('status writer broke')):
            record = backups.create_backup('db')
        self.assertEqual(record.status, Backup.OK)
        self.assertTrue(os.path.exists(backups.path_for(record)))

    def test_download_and_delete(self):
        self.set_up_encryption()
        record = backups.create_backup('db')
        path = backups.path_for(record)
        response = self.client.get(f'/api/backups/{record.id}/')
        with open(path, 'rb') as handle:
            self.assertEqual(b''.join(response.streaming_content), handle.read())
        self.assertIn(record.filename, response.headers['Content-Disposition'])
        self.assertEqual(self.api('delete', f'/api/backups/{record.id}/').status_code, 200)
        self.assertFalse(os.path.exists(path))
        self.assertFalse(Backup.objects.filter(pk=record.pk).exists())
        self.assertEqual(self.client.get('/api/backups/999999/').status_code, 404)


class UploadTests(StorageTestCase):

    def upload(self, name, content=b'OFXHEADER:100 some bank data', **extra):
        return self.client.post('/api/upload/', {'file': SimpleUploadedFile(name, content)}, **extra)

    def waiting(self):
        return sorted(os.listdir(self.dropzone)) if os.path.isdir(self.dropzone) else []

    def setUp(self):
        super().setUp()
        reader = mock.patch('finance.receipt_reader.available', return_value=True)
        reader.start()
        self.addCleanup(reader.stop)

    def test_supported_files_are_saved_for_the_watcher(self):
        for name in ('statement.qfx', 'bank.OFX', 'History_for_Account_X12345678.csv', 'receipt.jpg', 'scan.JPEG', 'photo.png', 'phone.heic'):
            response = self.upload(name)
            self.assertEqual(response.status_code, 201, f'{name}: {response.content}')
        self.assertEqual(len(self.waiting()), 7)
        with open(os.path.join(self.dropzone, 'statement.qfx'), 'rb') as handle:
            self.assertEqual(handle.read(), b'OFXHEADER:100 some bank data')
        self.assertFalse(any(name.startswith('.upload-') for name in self.waiting()), 'no temporary files are left behind')

    def test_unsupported_types_are_refused_with_the_list_of_supported_ones(self):
        for name in ('script.sh', 'page.html', 'program.exe', 'archive.zip', 'notes.txt', 'noextension', 'sneaky.qfx.php'):
            response = self.upload(name)
            self.assertEqual(response.status_code, 415, name)
            self.assertIn('.qfx', response.json()['error'])
        self.assertEqual(self.waiting(), [])

    def test_oversized_files_are_refused(self):
        response = self.upload('big.jpg', b'x' * (1024 * 1024 + 1))   # limit is 1 MB in these tests
        self.assertEqual(response.status_code, 413)
        self.assertIn('1 MB', response.json()['error'])
        self.assertEqual(self.waiting(), [])
        self.assertEqual(self.upload('ok.jpg', b'x' * (1024 * 1024)).status_code, 201, 'exactly at the limit is allowed')

    def test_oversized_request_is_refused_before_its_body_is_read(self):
        with mock.patch('rest_framework.request.Request.FILES', new_callable=mock.PropertyMock) as files:
            response = self.client.post('/api/upload/', {'file': SimpleUploadedFile('big.jpg', b'x' * (3 * 1024 * 1024))})
            self.assertEqual(response.status_code, 413)
            files.assert_not_called()

    def test_a_waiting_file_is_never_overwritten(self):
        self.assertEqual(self.upload('statement.qfx', b'first').status_code, 201)
        response = self.upload('statement.qfx', b'second')
        self.assertEqual(response.status_code, 409)
        self.assertIn('already waiting', response.json()['error'])
        with open(os.path.join(self.dropzone, 'statement.qfx'), 'rb') as handle:
            self.assertEqual(handle.read(), b'first')

    def test_file_names_cannot_escape_the_dropzone_or_hide(self):
        response = self.upload('../../etc/passwd.csv')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.waiting(), ['passwd.csv'])
        self.assertFalse(os.path.exists(os.path.join(self.root, 'etc')))
        self.assertEqual(self.upload('.hidden.csv').status_code, 400)
        self.assertEqual(self.upload('my bank file (1).qfx').status_code, 201)
        self.assertIn('my_bank_file_copy-1.qfx', self.waiting())

    def test_odd_file_names_are_handled_quickly_and_long_ones_refused(self):
        from finance.views import browser_copy_to_suffix as copy
        self.assertEqual(copy('X123 (1).csv'), 'X123_copy-1.csv')
        self.assertEqual(copy('X123(12).CSV'), 'X123_copy-12.CSV')
        self.assertEqual(copy('statement.qfx'), 'statement.qfx')
        self.assertEqual(copy('notes (a).csv'), 'notes (a).csv', 'only a number in brackets')
        self.assertEqual(copy('no extension (1)'), 'no extension (1)')
        # A name built to make a pattern search crawl: handled at once
        started = time.monotonic()
        copy(' ' * 200_000 + '(1' + '.csv')
        copy('(' * 200_000 + '.csv')
        self.assertLess(time.monotonic() - started, 1)
        # Django shortens a very long name to 255 characters before the app sees it
        self.assertEqual(self.upload('a' * 5000 + ' (1).csv').status_code, 201)
        self.assertTrue(all(len(name) <= 255 for name in self.waiting()))

    def test_missing_file_and_signed_out_requests(self):
        self.assertEqual(self.client.post('/api/upload/', {}).status_code, 400)
        self.client.logout()
        self.assertEqual(self.upload('statement.qfx').status_code, 403)
        self.assertEqual(self.waiting(), [])

    def test_a_save_failure_is_logged_but_not_described_to_the_browser(self):
        with mock.patch('finance.views.os.replace', side_effect=OSError('No space left on device: /data/dropzone/secret-path')):
            with self.assertLogs('finance.views', level='ERROR') as logs:
                response = self.upload('statement.qfx')
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('secret-path', response.content.decode())
        self.assertNotIn('No space left', response.content.decode())
        self.assertIn('No space left', '\n'.join(logs.output), 'the detail is in the server log')
        self.assertEqual(self.waiting(), [], 'the partial file is cleaned up')


class ErrorTextTests(StorageTestCase):
    """Errors tell the user what to do; internal detail goes to the log only."""

    def setUp(self):
        super().setUp()
        self.account = Account.objects.create(name='Checking')
        self.transaction = Transaction.objects.create(account=self.account, fitid='A1', dtposted=date(2026, 1, 5), trnamt=Decimal('-12.50'), name='COFFEE SHOP 123')

    def test_rule_preview_works_and_bad_values_get_a_plain_explanation(self):
        good = self.api('post', '/api/transactions/preview-rule/', {'conditions': [{'field': 'name', 'operator': 'contains', 'value': 'coffee'}]})
        self.assertEqual((good.status_code, len(good.json())), (200, 1))
        bad = self.api('post', '/api/transactions/preview-rule/', {'conditions': [{'field': 'amount', 'operator': 'gt', 'value': 'lots'}]})
        self.assertEqual(bad.status_code, 400)
        self.assertIn('amount condition needs a number', bad.json()['error'])
        self.assertEqual(self.api('post', '/api/transactions/create-rule/', {'rule_name': 'x', 'subcategory': 1, 'conditions': []}).status_code, 400, 'a rule needs a condition')
        self.assertNotIn('decimal', bad.content.decode().lower())

    def test_rule_preview_server_error_is_generic(self):
        with mock.patch('finance.views.TransactionSerializer', side_effect=RuntimeError('relation "finance_transaction" does not exist')):
            with self.assertLogs('finance.views', level='ERROR') as logs:
                response = self.api('post', '/api/transactions/preview-rule/', {'conditions': [{'field': 'name', 'operator': 'contains', 'value': 'coffee'}]})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('relation', response.content.decode())
        self.assertIn('relation', '\n'.join(logs.output))

    def test_create_rule_failure_does_not_expose_database_detail(self):
        with self.assertLogs('finance.views', level='ERROR'):
            response = self.api('post', '/api/transactions/create-rule/', {'rule_name': 'No subcategory', 'conditions': [{'field': 'name', 'operator': 'contains', 'value': 'coffee'}]})
        self.assertEqual(response.status_code, 400)
        body = response.content.decode().lower()
        for leak in ('null', 'constraint', 'integrity', 'subcategory_id', 'finance_'):
            self.assertNotIn(leak, body)
        self.assertIn('name and a valid subcategory', response.json()['error'])

    def test_link_and_unlink_errors(self):
        self.assertEqual(self.api('post', '/api/transactions/link/', {'source_id': self.transaction.id, 'target_id': 999999}).status_code, 404)
        self.assertEqual(self.api('post', '/api/transactions/999999/unlink/').status_code, 404, 'a missing transaction is not a server error')
        with mock.patch('finance.models.Transaction.save', side_effect=RuntimeError('deadlock detected on relation 16402')):
            with self.assertLogs('finance.views', level='ERROR') as logs:
                failed_unlink = self.api('post', f'/api/transactions/{self.transaction.id}/unlink/')
                failed_link = self.api('post', '/api/transactions/link/', {'source_id': self.transaction.id, 'target_id': self.transaction.id})
        for response in (failed_unlink, failed_link):
            self.assertEqual(response.status_code, 500)
            self.assertNotIn('deadlock', response.content.decode())
        self.assertIn('deadlock', '\n'.join(logs.output))


class AdminAndPasswordLoginTests(TestCase):
    """
    Without single sign-on, people sign in on the app's own page; with it,
    only through the identity provider. The Django admin is off unless asked
    for (or in development). These settings are decided at start-up, so each
    case runs in a fresh process.
    """

    PROBE = (
        "import json; from django.conf import settings; from django.urls import resolve, Resolver404\n"
        "def routed(path):\n"
        "    try: resolve(path); return True\n"
        "    except Resolver404: return False\n"
        "print(json.dumps({'admin_enabled': settings.ADMIN_ENABLED, 'backends': settings.AUTHENTICATION_BACKENDS,"
        " 'password_login': settings.PASSWORD_LOGIN,"
        " 'login_url': settings.LOGIN_URL, 'admin_routed': routed('/admin/login/'), 'oidc_routed': routed('/oidc/authenticate/')}))"
    )
    OIDC = {'AUTH_DOMAIN': 'https://auth.example.com', 'OIDC_APP_SLUG': 'finance', 'OIDC_CLIENT_ID': 'abc', 'OIDC_CLIENT_SECRET': 'def'}
    PASSWORD_LOGIN = 'django.contrib.auth.backends.ModelBackend'

    def probe(self, **env):
        import subprocess
        import sys
        from pathlib import Path
        clean = {k: v for k, v in os.environ.items() if k not in (*self.OIDC, 'ENABLE_ADMIN', 'DEBUG')}
        result = subprocess.run(
            [sys.executable, 'manage.py', 'shell', '--settings=config.settings_test', '-c', self.PROBE],
            cwd=Path(__file__).resolve().parent.parent, env={**clean, **env}, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_without_sso_the_app_has_its_own_sign_in_and_no_admin(self):
        state = self.probe()
        self.assertTrue(state['password_login'])
        self.assertEqual(state['backends'], [self.PASSWORD_LOGIN])
        self.assertEqual(state['login_url'], '/sign-in')
        self.assertFalse(state['admin_enabled'] or state['admin_routed'], 'the admin is off unless asked for')
        self.assertFalse(state['oidc_routed'])

    def test_in_development_the_admin_is_on(self):
        state = self.probe(DEBUG='True')
        self.assertTrue(state['admin_enabled'] and state['admin_routed'])
        self.assertFalse(self.probe(DEBUG='True', ENABLE_ADMIN='False')['admin_routed'])

    def test_with_sso_there_is_no_admin_and_no_password_login(self):
        state = self.probe(**self.OIDC)
        self.assertFalse(state['password_login'])
        self.assertFalse(state['admin_enabled'])
        self.assertFalse(state['admin_routed'], 'the admin must not be served once SSO is configured')
        self.assertNotIn(self.PASSWORD_LOGIN, state['backends'])
        self.assertEqual(len(state['backends']), 1)
        self.assertEqual(state['login_url'], '/oidc/authenticate/')
        self.assertTrue(state['oidc_routed'])
        self.assertFalse(self.probe(**self.OIDC, DEBUG='True')['admin_routed'], 'not even in development')

    def test_admin_can_be_kept_alongside_sso_on_request(self):
        state = self.probe(**self.OIDC, ENABLE_ADMIN='True')
        self.assertTrue(state['admin_enabled'] and state['admin_routed'] and state['oidc_routed'])
        self.assertIn(self.PASSWORD_LOGIN, state['backends'])
        self.assertFalse(state['password_login'], "the app's own sign-in stays off")


class SignInAndUsersTests(StorageTestCase):
    """The app's own sign-in, changing your password, and admins managing users."""

    STRONG = 'correct-horse-battery-9'

    def test_signing_in(self):
        from django.test import Client
        browser = Client(enforce_csrf_checks=True)
        me = browser.get('/api/me/').json()
        self.assertEqual((me['authenticated'], me['login_url'], me['password_login']), (False, '/sign-in', True))
        token = browser.cookies['csrftoken'].value
        post = lambda body, **headers: browser.post('/api/sign-in/', data=json.dumps(body), content_type='application/json', **headers)
        self.assertEqual(post({'username': 'member', 'password': 'pw-12345-x'}).status_code, 403, 'CSRF is checked before sign-in')
        wrong = post({'username': 'member', 'password': 'nope'}, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(wrong.status_code, 400)
        self.assertEqual(wrong.json()['error'], "That username and password don't match.")
        self.assertEqual(post({'username': 'member', 'password': 'pw-12345-x'}, HTTP_X_CSRFTOKEN=token).status_code, 200)
        me = browser.get('/api/me/').json()
        self.assertEqual((me['authenticated'], me['username'], me['is_admin']), (True, 'member', False))

        get_user_model().objects.filter(username='member').update(is_active=False)
        browser.post('/api/logout/', HTTP_X_CSRFTOKEN=browser.cookies['csrftoken'].value)
        token = browser.cookies['csrftoken'].value
        self.assertEqual(post({'username': 'member', 'password': 'pw-12345-x'}, HTTP_X_CSRFTOKEN=token).status_code, 400, 'a switched-off account')

    def test_sign_in_attempts_are_limited(self):
        from django.core.cache import cache
        cache.clear()
        self.client.logout()
        with override_settings(API_RATE_LIMITS={'sign_in': '3/minute'}):
            codes = [self.api('post', '/api/sign-in/', {'username': 'admin', 'password': 'guess'}).status_code for _ in range(4)]
        self.assertEqual(codes, [400, 400, 400, 429])

    def test_changing_your_own_password(self):
        self.as_member()
        change = lambda current, new: self.api('post', '/api/account/password/', {'current_password': current, 'new_password': new})
        self.assertEqual(change('wrong', self.STRONG).json()['error'], 'Your current password is not right.')
        weak = change('pw-12345-x', '12345678')
        self.assertEqual(weak.status_code, 400)
        self.assertIn('too common', weak.json()['error'])
        self.assertEqual(change('pw-12345-x', self.STRONG).status_code, 200)
        self.assertTrue(self.client.get('/api/me/').json()['authenticated'], 'still signed in here')
        self.client.logout()
        self.assertTrue(self.client.login(username='member', password=self.STRONG))

    def test_admins_manage_users(self):
        created = self.api('post', '/api/users/', {'username': 'sam', 'name': 'Sam', 'password': self.STRONG})
        self.assertEqual(created.status_code, 201, created.content)
        sam = created.json()
        self.assertNotIn('password', sam)
        self.assertEqual((sam['name'], sam['is_admin'], sam['is_active']), ('Sam', False, True))
        self.assertEqual(self.api('post', '/api/users/', {'username': 'pat', 'password': 'pat'}).status_code, 400, 'too weak')
        self.assertEqual(self.api('post', '/api/users/', {'username': 'pat'}).status_code, 400, 'a password is needed')

        self.assertEqual(self.api('patch', f"/api/users/{sam['id']}/", {'is_admin': True}).json()['is_admin'], True)
        self.assertTrue(get_user_model().objects.get(pk=sam['id']).is_superuser)
        self.assertEqual(self.api('post', f"/api/users/{sam['id']}/set-password/", {'new_password': 'another-strong-pass-4'}).status_code, 200)
        self.assertTrue(get_user_model().objects.get(pk=sam['id']).check_password('another-strong-pass-4'))
        self.assertEqual(self.api('patch', f"/api/users/{sam['id']}/", {'password': 'x'}).status_code, 400, 'passwords only through set-password')

        # Nobody can lock everyone out
        admin = get_user_model().objects.get(username='admin')
        self.assertEqual(self.api('patch', f'/api/users/{admin.id}/', {'is_admin': False}).status_code, 400, 'not your own admin rights')
        self.assertEqual(self.api('patch', f'/api/users/{admin.id}/', {'is_active': False}).status_code, 400)
        self.assertEqual(self.api('delete', f'/api/users/{admin.id}/').status_code, 400)
        self.assertEqual(self.api('patch', f"/api/users/{sam['id']}/", {'is_active': False}).status_code, 200, 'another admin remains')
        self.assertEqual(self.api('delete', f"/api/users/{sam['id']}/").status_code, 204)

        # Only admins
        self.as_member()
        self.assertEqual(self.client.get('/api/users/').status_code, 403)
        member = get_user_model().objects.get(username='member')
        self.assertEqual(self.api('post', f'/api/users/{member.id}/set-password/', {'new_password': self.STRONG}).status_code, 403)

    def test_a_temporary_password_must_be_changed_before_anything_else(self):
        created = self.api('post', '/api/users/', {'username': 'sam', 'password': self.STRONG})
        self.assertEqual(created.status_code, 201)
        self.client.logout()
        self.assertTrue(self.client.login(username='sam', password=self.STRONG))
        self.assertTrue(self.client.get('/api/me/').json()['must_change_password'])
        blocked = self.client.get('/api/transactions/')
        self.assertEqual((blocked.status_code, blocked.json()['must_change_password']), (403, True))
        same = self.api('post', '/api/account/password/', {'current_password': self.STRONG, 'new_password': self.STRONG})
        self.assertEqual(same.json()['error'], 'Choose a password different from the current one.')
        self.assertEqual(self.api('post', '/api/account/password/', {'current_password': self.STRONG, 'new_password': 'sams-own-choice-77'}).status_code, 200)
        self.assertFalse(self.client.get('/api/me/').json()['must_change_password'])
        self.assertEqual(self.client.get('/api/transactions/').status_code, 200)

        # An admin's reset is temporary too; nobody resets their own that way
        self.client.logout()
        self.client.login(username='admin', password='pw-12345-x')
        sam = get_user_model().objects.get(username='sam')
        self.assertEqual(self.api('post', f'/api/users/{sam.id}/set-password/', {'new_password': 'a-temporary-one-55'}).status_code, 200)
        from finance.models import must_change_password
        self.assertTrue(must_change_password(get_user_model().objects.get(pk=sam.pk)))
        admin = get_user_model().objects.get(username='admin')
        self.assertEqual(self.api('post', f'/api/users/{admin.id}/set-password/', {'new_password': 'a-temporary-one-55'}).status_code, 400)

    def test_the_first_admin_and_a_forgotten_password(self):
        from io import StringIO
        from django.core.management import call_command
        from finance.models import must_change_password
        out = StringIO()
        call_command('first_admin', stdout=out)
        self.assertIn('already exists', out.getvalue(), 'never when there are users')
        get_user_model().objects.all().delete()

        out = StringIO()
        call_command('first_admin', stdout=out)
        password = out.getvalue().split('Password: ')[1].strip()
        admin = get_user_model().objects.get(username='admin')
        self.assertTrue(admin.is_staff and admin.is_superuser and admin.check_password(password))
        self.assertTrue(must_change_password(admin))
        self.assertEqual(len(password), 23)

        out = StringIO()
        call_command('reset_password', 'admin', stdout=out)
        new = out.getvalue().strip().splitlines()[-1].strip()
        admin.refresh_from_db()
        self.assertTrue(admin.check_password(new) and new != password)
        from django.core.management.base import CommandError
        with self.assertRaisesMessage(CommandError, 'No user called "nobody". Users: admin.'):
            call_command('reset_password', 'nobody')

    def test_each_user_keeps_their_own_menu_order(self):
        self.assertEqual(self.client.get('/api/account/preferences/').json(), {'menu_order': []}, 'the usual order')
        saved = self.api('put', '/api/account/preferences/', {'menu_order': ['/net-worth', '/transactions', '/net-worth']})
        self.assertEqual(saved.json()['menu_order'], ['/net-worth', '/transactions'], 'each page once')
        self.assertEqual(self.api('put', '/api/account/preferences/', {'menu_order': ['https://elsewhere']}).status_code, 400)
        self.as_member()
        self.assertEqual(self.client.get('/api/account/preferences/').json()['menu_order'], [], "someone else's is their own")

    def test_the_last_admin_stays(self):
        from django.test import RequestFactory
        from finance.auth import UserViewSet
        admin = get_user_model().objects.get(username='admin')
        other = get_user_model().objects.create_user('other', password='x', is_staff=True)
        view = UserViewSet()
        self.assertFalse(view._would_leave_no_admin(other, False, True), 'admin is still an admin')
        get_user_model().objects.filter(pk=admin.pk).update(is_active=False)
        self.assertTrue(view._would_leave_no_admin(other, False, True))
        self.assertFalse(view._would_leave_no_admin(other, True, True))


class QueryEfficiencyTests(StorageTestCase):
    """
    Lists must cost a fixed handful of queries, however many rows they show.
    Each test checks the count stays the same when the data doubles.
    """

    def setUp(self):
        super().setUp()
        self.accounts = [Account.objects.create(name=name) for name in ('Checking', 'Card')]
        from .models import Category, SubCategory
        self.categories = [Category.objects.create(name=f'Cat{i}', type='EXP') for i in range(3)]
        self.subs = [SubCategory.objects.create(name=f'Sub{i}', category=self.categories[i % 3]) for i in range(6)]
        self.made = 0

    def add_transactions(self, count):
        """Transactions with every kind of related data the list shows."""
        from .models import TransactionSplit
        created = []
        for _ in range(count):
            index = self.made
            self.made += 1
            created.append(Transaction.objects.create(
                account=self.accounts[index % 2], fitid=f'F{index}', dtposted=date(2026, 1, 1) + timedelta(days=index % 300),
                trnamt=Decimal('-10.00') - index, name=f'Merchant {index}', subcategory=self.subs[index % 6], status='C',
            ))
        for index, tx in enumerate(created):
            if index % 2 == 0:
                TransactionSplit.objects.create(transaction=tx, subcategory=self.subs[(index + 1) % 6], amount=Decimal('-5'))
            if index % 3 == 0:
                Receipt.objects.create(original_filename=f'r{tx.id}.jpg', image=f'receipts/r{tx.id}.jpg', date=tx.dtposted, total_amount=Decimal('10'), transaction=tx)
        for first, second in zip(created[0::4], created[1::4]):
            Transaction.objects.filter(pk=first.pk).update(linked_transaction=second)
            Transaction.objects.filter(pk=second.pk).update(linked_transaction=first)
        return created

    def queries_for(self, url):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.content[:200])
        return len(captured), response.json()

    def assert_flat(self, url, add_rows, limit):
        add_rows()
        before, _ = self.queries_for(url)
        add_rows()
        after, body = self.queries_for(url)
        self.assertEqual(before, after, f'{url}: query count grew with the data ({before} -> {after})')
        self.assertLessEqual(after, limit, f'{url}: {after} queries')
        return body

    def test_transaction_list_does_not_query_per_row(self):
        body = self.assert_flat('/api/transactions/', lambda: self.add_transactions(40), limit=8)
        self.assertEqual(len(body), 80)
        linked = next(row for row in body if row['linked_transaction'])
        self.assertEqual(linked['type'], 'Transfer')
        self.assertTrue(linked['linked_transaction_account'])
        self.assertTrue(any(row['splits'] for row in body) and any(row['receipt_image'] for row in body))

    def test_transaction_page_does_not_query_per_row(self):
        self.assert_flat('/api/transactions/?page=1&page_size=50', lambda: self.add_transactions(60), limit=9)

    def test_categories_rules_and_recurring_do_not_query_per_row(self):
        from .models import Category, SubCategory, TransactionRule, RecurringTransaction
        def more():
            for _ in range(5):
                category = Category.objects.create(name=f'New{Category.objects.count()}', type='EXP')
                sub = SubCategory.objects.create(name='Child', category=category)
                TransactionRule.objects.create(rule_name=f'Rule {sub.id}', subcategory=sub, conditions=[])
                RecurringTransaction.objects.create(name=f'Bill {sub.id}', amount=Decimal('-9'), account=self.accounts[0], start_date=date(2026, 1, 1), subcategory=sub)
        for url in ('/api/categories/', '/api/transaction-rules/', '/api/recurring/'):
            self.assert_flat(url, more, limit=6)

    def test_import_inbox_does_not_query_per_row(self):
        from .models import BankImport, StagedTransaction
        batch = BankImport.objects.create(filename='x.qfx', status='Success')
        def more():
            start = StagedTransaction.objects.count()
            for index in range(start, start + 30):
                StagedTransaction.objects.create(batch=batch, account=self.accounts[1], fitid=f'S{index}', date=date(2026, 10, 1), amount=Decimal('-9.99') - index, name=f'staged {index}')
        body = self.assert_flat('/api/import/inbox/', more, limit=9)
        self.assertEqual(len(body), 60)
        self.assertEqual(body[0]['account_name'], 'Card')

    def test_search_matches_text_and_amounts_on_the_server(self):
        self.add_transactions(30)
        Transaction.objects.create(account=self.accounts[0], fitid='H', dtposted=date(2026, 6, 1), trnamt=Decimal('-107.25'), name='HARDWARE STORE', status='C')
        Transaction.objects.create(account=self.accounts[0], fitid='M', dtposted=date(2026, 6, 2), trnamt=Decimal('-3.00'), name='Coffee', personal_memo='reimbursable 107', status='C')
        names = lambda url: [row['description'] for row in self.queries_for(url)[1]]
        self.assertEqual(names('/api/transactions/?search=hardware'), ['HARDWARE STORE'])
        self.assertEqual(sorted(names('/api/transactions/?search=107')), ['Coffee', 'HARDWARE STORE'], 'a number matches amounts and text')
        self.assertEqual(names('/api/transactions/?search=107.25'), ['HARDWARE STORE'])
        self.assertEqual(names('/api/transactions/?search=no-such-merchant'), [])
        page = self.client.get('/api/transactions/?search=merchant&page=1&page_size=5').json()
        self.assertEqual((len(page['results']), page['count']), (5, 30), 'the receipt search asks for 5 rows, not the whole ledger')

    def test_month_and_year_filters_and_bad_values(self):
        for day, name in ((date(2026, 1, 31), 'jan-last'), (date(2026, 2, 1), 'feb-first'), (date(2026, 2, 28), 'feb-last'), (date(2026, 3, 1), 'mar-first'), (date(2025, 2, 15), 'feb-2025'), (date(2026, 12, 31), 'dec-last'), (date(2027, 1, 1), 'jan-2027')):
            Transaction.objects.create(account=self.accounts[0], fitid=name, dtposted=day, trnamt=Decimal('-1'), name=name, status='C', subcategory=self.subs[0])
        names = lambda url: sorted(row['description'] for row in self.client.get(url).json())
        self.assertEqual(names('/api/transactions/?year=2026&month=2'), ['feb-first', 'feb-last'])
        self.assertEqual(names('/api/transactions/?year=2026&month=12'), ['dec-last'], 'December does not spill into January')
        self.assertEqual(names('/api/transactions/?month=2'), ['feb-2025', 'feb-first', 'feb-last'])
        self.assertEqual(len(names('/api/transactions/?year=2026')), 5)
        february = {row['name']: row for row in self.client.get('/api/budgets/summary/?year=2026&month=2').json()['rows']}
        self.assertEqual(Decimal(str(february['Sub0']['actual'])), Decimal('-2'))
        for url in ('/api/transactions/?year=abc', '/api/transactions/?month=13', '/api/transactions/?account=x',
                    '/api/budgets/summary/?year=abc', '/api/budgets/summary/?year=nope&period=year'):
            self.assertEqual(self.client.get(url).status_code, 400, url)
        bad = self.api('post', '/api/budgets/set-target/', {'subcategory_id': self.subs[0].id, 'year': 2026, 'month': 'x', 'amount': 5})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.api('post', '/api/budgets/set-target/', {'subcategory_id': self.subs[0].id, 'year': 2026, 'month': 2, 'amount': 'lots'}).status_code, 400)
        self.assertEqual(self.api('post', '/api/budgets/set-target/', {'subcategory_id': self.subs[0].id, 'year': 2026, 'month': 2, 'amount': 50}).status_code, 200)


class ImportReviewTests(StorageTestCase):
    """Files from an unknown account are parked; mapping them must re-check each row against the ledger."""

    def setUp(self):
        super().setUp()
        from .models import BankImport, StagedTransaction
        self.card = Account.objects.create(name='CREDIT_CARD', account_number='OLD-1111')
        self.day = date(2026, 9, 20)
        for index in range(3):
            Transaction.objects.create(account=self.card, fitid=f'F{index}', dtposted=self.day, trnamt=Decimal('-10.00') - index, name=f'Old {index}', status='C')
        self.pending = Transaction.objects.create(account=self.card, fitid='', dtposted=self.day, trnamt=Decimal('-55.55'), name='Manual pending', status='P', is_manual=True)
        batch = BankImport.objects.create(filename='new-card.qfx', status='Success')
        park = lambda fitid, amount: StagedTransaction.objects.create(batch=batch, account=None, unregistered_account_id='NEW-2222', fitid=fitid, date=self.day, amount=Decimal(amount), name=fitid)
        self.duplicates = [park(f'F{index}', str(Decimal('-10.00') - index)) for index in range(3)]
        self.fresh = park('F-NEW', '-42.00')
        self.match = park('F-PEND', '-55.55')

    def flags(self):
        from .models import StagedTransaction
        return {row.fitid: row.match_type for row in StagedTransaction.objects.all()}

    def test_mapping_a_parked_account_flags_duplicates_and_matches(self):
        from .models import StagedTransaction
        response = self.api('post', '/api/import/resolve/', {'unregistered_id': 'NEW-2222', 'real_account_id': self.card.account_id})
        self.assertEqual((response.status_code, response.json()['mapped'], response.json()['duplicates']), (200, 5, 3))
        self.assertEqual(self.flags(), {'F0': 'DUPE', 'F1': 'DUPE', 'F2': 'DUPE', 'F-NEW': 'NEW', 'F-PEND': 'MATCH'})
        self.assertEqual(StagedTransaction.objects.get(fitid='F-PEND').match_proposal_id, self.pending.id)

    def test_two_staged_rows_never_claim_the_same_pending_transaction(self):
        from .models import StagedTransaction
        twin = StagedTransaction.objects.create(account=None, unregistered_account_id='NEW-2222', fitid='F-PEND-2', date=self.day, amount=Decimal('-55.55'), name='twin')
        self.api('post', '/api/import/resolve/', {'unregistered_id': 'NEW-2222', 'real_account_id': self.card.account_id})
        claimed = [row.match_proposal_id for row in StagedTransaction.objects.filter(fitid__in=['F-PEND', 'F-PEND-2'])]
        self.assertEqual(sorted(claimed, key=str), sorted([self.pending.id, None], key=str))

    def test_how_far_apart_the_dates_may_be(self):
        # Days from the entry to the bank's date: a match, a near match, or nothing
        for days, expected in ((3, 'MATCH'), (-3, 'MATCH'), (15, 'MATCH'), (-5, 'NEAR'), (20, 'NEAR'), (-30, 'NEAR'), (31, 'NEW')):
            from .models import StagedTransaction
            StagedTransaction.objects.update(account=None, unregistered_account_id='NEW-2222', match_type='NEW', match_proposal=None, match_reason='')
            Transaction.objects.filter(pk=self.pending.pk).update(dtposted=self.day - timedelta(days=days))
            self.api('post', '/api/import/resolve/', {'unregistered_id': 'NEW-2222', 'real_account_id': self.card.account_id})
            self.assertEqual(self.flags()['F-PEND'], expected, days)

    def test_inbox_load_repairs_rows_mapped_earlier_and_approve_never_duplicates(self):
        from .models import StagedTransaction
        StagedTransaction.objects.all().update(account=self.card, unregistered_account_id='')   # mapped, never re-checked
        stale = self.duplicates[0]
        response = self.api('post', f'/api/import/inbox/{stale.id}/approve/')
        self.assertEqual(response.status_code, 409)
        self.assertIn('already in the ledger', response.json()['error'])
        self.assertEqual(Transaction.objects.filter(account=self.card, fitid='F0').count(), 1)

        self.assertEqual(self.client.get('/api/import/inbox/').status_code, 200)
        self.assertEqual(self.flags(), {'F0': 'DUPE', 'F1': 'DUPE', 'F2': 'DUPE', 'F-NEW': 'NEW', 'F-PEND': 'MATCH'})

        self.assertEqual(self.api('post', f'/api/import/inbox/{self.fresh.id}/approve/').status_code, 200)
        self.assertEqual(self.api('post', f'/api/import/inbox/{self.match.id}/approve/').status_code, 200)
        self.pending.refresh_from_db()
        self.assertEqual((self.pending.status, self.pending.fitid), ('C', 'F-PEND'))
        self.assertEqual(Transaction.objects.filter(account=self.card).count(), 5, 'one new row, the pending one updated in place')
        self.assertEqual(self.api('post', '/api/import/inbox/clear-duplicates/').json()['deleted'], 3)
        self.assertEqual(StagedTransaction.objects.count(), 0)


class WatcherTests(TestCase):
    """The dropzone watcher imports a file once, and only when it has finished arriving."""

    def setUp(self):
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location('watcher', Path(__file__).resolve().parent.parent / 'watcher.py')
        self.watcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.watcher)
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        # Short waits so the tests are quick; the logic is the same
        for name, value in (('SETTLE_SECONDS', 0.3), ('MAX_WAIT_SECONDS', 5)):
            patcher = mock.patch.object(self.watcher, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def path(self, name):
        return os.path.join(self.folder, name)

    def write(self, name, data=b'complete file'):
        with open(self.path(name), 'wb') as handle:
            handle.write(data)
        return self.path(name)

    def test_waits_for_a_file_that_is_still_being_copied(self):
        import threading
        import time
        target = self.path('big.qfx')
        open(target, 'wb').close()   # appears empty first, as a real copy does

        def slow_copy():
            for _ in range(8):
                time.sleep(0.15)
                with open(target, 'ab') as handle:
                    handle.write(b'x' * 1000)
        copier = threading.Thread(target=slow_copy)
        started = time.monotonic()
        copier.start()
        finished = self.watcher.wait_until_complete(target)
        size_when_released = os.path.getsize(target)
        waited = time.monotonic() - started
        copier.join()

        self.assertTrue(finished)
        self.assertEqual(size_when_released, 8000, 'released only after the last byte arrived')
        self.assertGreater(waited, 1.2, 'it kept waiting while the file was growing')

    def test_a_finished_file_is_released_after_one_short_pause(self):
        import time
        target = self.write('small.qfx')
        started = time.monotonic()
        self.assertTrue(self.watcher.wait_until_complete(target))
        self.assertLess(time.monotonic() - started, 1.5, 'no fixed multi-second wait for a file that is already complete')

    def test_gives_up_on_a_missing_or_never_ending_file(self):
        import threading
        import time
        started = time.monotonic()
        self.assertFalse(self.watcher.wait_until_complete(self.path('gone.qfx')))
        self.assertLess(time.monotonic() - started, 0.5, 'a file that is not there is not waited for')

        target = self.write('endless.qfx', b'x')
        stop = threading.Event()
        def keep_writing():
            while not stop.is_set():
                with open(target, 'ab') as handle:
                    handle.write(b'x')
                time.sleep(0.05)
        writer = threading.Thread(target=keep_writing)
        writer.start()
        try:
            self.assertFalse(self.watcher.wait_until_complete(target, max_wait=1))
        finally:
            stop.set()
            writer.join()

    def run_handler(self, events, importer=None):
        """Feeds file names to the handler as events; returns the importer commands it ran."""
        handler = self.watcher.FileHandler()
        ran = []
        def fake_run(command, check):
            ran.append(command[-1])
            (importer or (lambda name: None))(command[-1])
        with mock.patch.object(self.watcher.subprocess, 'run', side_effect=fake_run), mock.patch('builtins.print'):
            for name in events:
                handler.process(self.path(name))
        return ran

    def test_several_events_for_one_file_import_it_once(self):
        self.write('statement.qfx')
        archive = lambda command: os.remove(self.path('statement.qfx'))   # the importer moves the file away
        ran = self.run_handler(['statement.qfx', 'statement.qfx', 'statement.qfx'], importer=archive)
        self.assertEqual(ran, ['stage_import'])

    def test_each_kind_of_file_goes_to_its_importer_and_others_are_ignored(self):
        for name in ('a.QFX', 'b.ofx', 'c.csv'):
            self.write(name)
            self.assertEqual(self.run_handler([name]), ['stage_import'], name)
        for name in ('r.jpg', 'r.JPEG', 'r.png', 'r.heic'):
            self.write(name)
            self.assertEqual(self.run_handler([name]), ['ingest_receipts'], name)
        for name in ('notes.txt', 'archive.zip', '.upload-abc123.part', '.hidden.qfx'):
            self.write(name)
            self.assertEqual(self.run_handler([name]), [], name)

    def test_files_already_waiting_at_start_up_are_handled_once_per_kind(self):
        for name in ('one.qfx', 'two.csv', 'r1.jpg', 'r2.png', 'ignore.txt', '.upload-x.part'):
            self.write(name)
        handler = self.watcher.FileHandler()
        ran = []
        with mock.patch.object(self.watcher.subprocess, 'run', side_effect=lambda command, check: ran.append(command[-1])), mock.patch('builtins.print'):
            self.watcher.process_waiting_files(handler, self.folder)
        self.assertEqual(sorted(ran), ['ingest_receipts', 'stage_import'])
        with mock.patch('builtins.print'):
            self.watcher.process_waiting_files(handler, os.path.join(self.folder, 'no-such-folder'))   # must not raise

    def test_waiting_receipt_photos_are_read_once_the_reader_answers(self):
        self.write('lunch.jpg')
        self.write('statement.qfx')
        self.write('.upload-x.part')
        handler = self.watcher.FileHandler()
        ran = []
        retry = lambda answering: self.watcher.retry_waiting_receipts(handler, self.folder, answering=lambda: answering)
        with mock.patch.object(self.watcher.subprocess, 'run', side_effect=lambda command, check: ran.append(command[-1])), \
                mock.patch('builtins.print'), mock.patch.dict(os.environ, {'COMPOSE_PROFILES': 'receipts'}):
            self.assertFalse(retry(False), 'nothing while the reader is down')
            self.assertTrue(retry(True))
            self.assertEqual(ran, ['ingest_receipts'], 'one run reads every waiting photo')
            with mock.patch.dict(os.environ, {'COMPOSE_PROFILES': ''}):
                self.assertFalse(retry(True), 'not without the add-on')
            os.remove(self.path('lunch.jpg'))
            self.assertFalse(retry(True), 'nothing waiting, nothing to do')
        self.assertEqual(ran, ['ingest_receipts'])


class SecuritySettingsTests(TestCase):
    """Settings that differ between a dev machine and a production site. Decided at start-up, so each case runs in a fresh process."""

    PROBE = (
        "import json; from django.conf import settings\n"
        "print(json.dumps({'debug': settings.DEBUG, 'csrf_origins': settings.CSRF_TRUSTED_ORIGINS, 'hsts': settings.SECURE_HSTS_SECONDS,"
        " 'secure_cookies': [settings.SESSION_COOKIE_SECURE, settings.CSRF_COOKIE_SECURE]}))"
    )

    def probe(self, **env):
        import subprocess
        import sys
        from pathlib import Path
        clean = {k: v for k, v in os.environ.items() if k not in ('DEBUG', 'SITE_URL', 'DOMAIN_NAME', 'HSTS_SECONDS')}
        result = subprocess.run(
            [sys.executable, 'manage.py', 'shell', '--settings=config.settings_test', '-c', self.PROBE],
            cwd=Path(__file__).resolve().parent.parent, env={**clean, **env}, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_production_trusts_only_its_own_address_and_insists_on_https(self):
        state = self.probe(DOMAIN_NAME='finance.example.com')
        self.assertFalse(state['debug'])
        self.assertEqual(state['csrf_origins'], ['https://finance.example.com'], 'no dev addresses on a production site')
        self.assertEqual(state['hsts'], 31536000)
        self.assertEqual(state['secure_cookies'], [True, True])

    def test_dev_trusts_the_vite_server_and_does_not_force_https(self):
        state = self.probe(DEBUG='True', DOMAIN_NAME='localhost', SITE_URL='http://localhost:5173')
        self.assertTrue(state['debug'])
        self.assertIn('http://localhost:5173', state['csrf_origins'])
        self.assertIn('http://127.0.0.1:5173', state['csrf_origins'])
        self.assertEqual(state['hsts'], 0, 'HSTS on plain http would lock a dev machine out')
        self.assertEqual(state['secure_cookies'], [False, False])

    def test_hsts_can_be_turned_off(self):
        self.assertEqual(self.probe(DOMAIN_NAME='finance.example.com', HSTS_SECONDS='0')['hsts'], 0)


# What the Florence-2 worker really returns for a (synthetic) till receipt:
# each piece of text followed by eight position tags, the corners of its box.
RECEIPT_OCR_OUTPUT = (
    'HARDWARE STORE<loc_249><loc_50><loc_753><loc_50><loc_753><loc_83><loc_249><loc_83>123 Main Street<loc_284><loc_110><loc_598><loc_110><loc_598><loc_137><loc_284><loc_137>Date: 10/01/2026<loc_65><loc_182><loc_406><loc_182><loc_406><loc_209><loc_65><loc_209>Hammer<loc_65><loc_272><loc_236><loc_273><loc_236><loc_298><loc_65><loc_297>24.99<loc_765><loc_272><loc_874><loc_272><loc_874><loc_297><loc_765><loc_297>Wood screws x100<loc_65><loc_326><loc_439><loc_326><loc_439><loc_354><loc_65><loc_354>12.50<loc_768><loc_327><loc_874><loc_327><loc_874><loc_352><loc_768><loc_352>Paint 1 gal<loc_65><loc_382><loc_276><loc_382><loc_276><loc_411><loc_65><loc_411>45.00<loc_765><loc_383><loc_874><loc_383><loc_874><loc_408><loc_765><loc_408>Brush set<loc_65><loc_438><loc_254><loc_438><loc_254><loc_465><loc_65><loc_465>17.51<loc_768><loc_438><loc_871><loc_438><loc_871><loc_464><loc_768><loc_464>Subtotal<loc_65><loc_516><loc_236><loc_516><loc_236><loc_542><loc_65><loc_542>100.00<loc_768><loc_516><loc_899><loc_516><loc_899><loc_541><loc_768><loc_541>Tax<loc_65><loc_572><loc_139><loc_573><loc_139><loc_597><loc_65><loc_597>7.00<loc_763><loc_572><loc_849><loc_572><loc_849><loc_597><loc_763><loc_597>TOTAL<loc_65><loc_627><loc_206><loc_627><loc_206><loc_653><loc_65><loc_653>107.00<loc_766><loc_627><loc_899><loc_627><loc_899><loc_653><loc_766><loc_653>Thank you for shopping<loc_197><loc_726><loc_674><loc_727><loc_674><loc_759><loc_197><loc_757>'
)


class ReceiptReadingTests(StorageTestCase):
    """Turning the OCR worker's output into a date, a total and a tax amount."""

    def setUp(self):
        super().setUp()
        from finance.management.commands.ingest_receipts import Command
        self.reader = Command()
        self.reader.stdout = io.StringIO()

    def tagged(self, *rows):
        """Builds worker-style output from (text, left, top) rows on the 0-1000 grid."""
        box = lambda x, y: ''.join(f'<loc_{n}>' for n in (x, y, x + 90, y, x + 90, y + 25, x, y + 25))
        return ''.join(text + box(x, y) for text, x, y in rows)

    def test_a_photo_waits_when_the_reader_cant_be_reached(self):
        import requests
        dropzone, failed, archive = (os.path.join(self.root, d) for d in ('rr-drop', 'rr-failed', 'rr-archive'))
        for d in (dropzone, failed, archive):
            os.makedirs(d)
        photo = os.path.join(dropzone, 'lunch.jpg')
        with open(photo, 'wb') as f:
            f.write(b'x' * 10)
        for error in (requests.ConnectionError('Connection refused'), requests.ConnectionError('Failed to resolve ai-worker')):
            with mock.patch('finance.management.commands.ingest_receipts.requests.post', side_effect=error):
                self.reader.process_image(photo, 'lunch.jpg', archive, failed)
            self.assertTrue(os.path.exists(photo), f'still waiting after: {error}')
            self.assertEqual(os.listdir(failed), [], 'never treated as an unreadable photo')
        # A real failure to read it still moves it to failed
        with mock.patch('finance.management.commands.ingest_receipts.requests.post', return_value=mock.Mock(status_code=500, text='boom')):
            self.reader.process_image(photo, 'lunch.jpg', archive, failed)
        self.assertEqual(os.listdir(failed), ['lunch.jpg'])

    def test_real_worker_output_is_read_correctly(self):
        clean = self.reader.strip_location_tags(RECEIPT_OCR_OUTPUT)
        self.assertNotIn('<loc', clean, 'position tags must not end up in the stored text')
        self.assertIn('TOTAL 107.00', clean)
        self.assertEqual(self.reader.extract_date_smart(clean, 'x.jpg'), date(2026, 10, 1))
        self.assertEqual(self.reader.extract_tax_spatial(RECEIPT_OCR_OUTPUT), Decimal('7.00'))
        self.assertEqual(self.reader.extract_total_spatial(RECEIPT_OCR_OUTPUT), Decimal('107.00'))

    def dense(self, *rows):
        """Like tagged(), for a long receipt: text only 10 high, lines 16 apart."""
        box = lambda x, y, w: ''.join(f'<loc_{n}>' for n in (x, y, x + w, y, x + w, y + 10, x, y + 10))
        return ''.join(text + box(x, y, 8 * len(text)) for text, x, y in rows)

    LONG_RECEIPT_TAIL = (
        ('REWARDS MEMBER STATEMENT', 250, 580), ('2026 REWARDS SPEND 08/29:', 60, 660), ('$5,432.10', 800, 660),
        ('Spend 567.90 more in', 400, 700),
    )

    def test_closely_spaced_lines_on_a_long_receipt_are_not_mixed_up(self):
        receipt = self.dense(
            ('Member Discount', 100, 468), ('-4.14', 820, 468),
            ('SUBTOTAL', 400, 500), ('37.29', 820, 500),
            ('SALES TAX', 400, 516), ('3.08', 828, 516),
            ('TOTAL', 400, 532), ('$40.37', 812, 532),
            ('XXXXXXXX0000', 60, 548), ('STORE CREDIT', 380, 548), ('40.37', 820, 548),
            ('CARD BALANCE', 60, 564), ('9.40', 480, 564),
            *self.LONG_RECEIPT_TAIL,
        )
        self.assertEqual(self.reader.extract_tax_spatial(receipt), Decimal('3.08'))
        self.assertEqual(self.reader.extract_total_spatial(receipt), Decimal('40.37'))

    def test_a_label_and_its_amount_returned_as_one_piece_of_text(self):
        receipt = self.dense(
            ('SUBTOTAL 37.29', 400, 500), ('SALES TAX 3.08', 400, 516), ('TOTAL $40.37', 400, 532),
            ('Tax ID 12-3456789', 60, 300), ('Total savings 4.14', 60, 548),
            *self.LONG_RECEIPT_TAIL,
        )
        self.assertEqual(self.reader.extract_tax_spatial(receipt), Decimal('3.08'))
        self.assertEqual(self.reader.extract_total_spatial(receipt), Decimal('40.37'))

    def test_text_fallback_finds_the_total_after_a_tax_line_and_never_guesses(self):
        text = 'SUBTOTAL 37.29 SALES TAX 3.08 TOTAL $40.37 STORE CREDIT 40.37 2026 REWARDS SPEND 08/29: $5,432.10'
        self.assertEqual(self.reader.extract_amount_smart(text), Decimal('40.37'))
        self.assertEqual(self.reader.extract_amount_smart('SUB TOTAL 83.03 Tax 6.85 Total 1,089.88'), Decimal('1089.88'))
        # No total anywhere: leave it blank for the user, not the biggest number in sight
        self.assertIsNone(self.reader.extract_amount_smart('REWARDS SPEND 08/29: $5,432.10 Spend 567.90 more'))

    def test_total_is_read_from_its_own_line_not_guessed(self):
        receipt = self.tagged(
            ('Subtotal', 60, 500), ('100.00', 760, 500),
            ('Tax', 60, 550), ('7.00', 760, 550),
            ('TOTAL', 60, 600), ('107.00', 760, 600),
            ('Cash tendered', 60, 650), ('120.00', 760, 650),     # larger than the total
            ('Change', 60, 700), ('13.00', 760, 700),
        )
        self.assertEqual(self.reader.extract_total_spatial(receipt), Decimal('107.00'))
        self.assertEqual(self.reader.extract_tax_spatial(receipt), Decimal('7.00'))

    def test_tax_and_total_variants(self):
        with_rate = self.tagged(('Sales Tax:', 60, 550), ('8.25%', 400, 550), ('1,234.56', 760, 550), ('Total Due', 60, 600), ('16,198.56', 760, 600))
        self.assertEqual(self.reader.extract_tax_spatial(with_rate), None, 'a tax above the sanity limit is not believed')
        self.assertEqual(self.reader.extract_total_spatial(with_rate), Decimal('16198.56'))
        small = self.tagged(('GST', 60, 550), ('5.0%', 400, 550), ('2.15', 760, 550), ('Grand Total', 60, 600), ('45.15', 760, 600))
        self.assertEqual(self.reader.extract_tax_spatial(small), Decimal('2.15'), 'the rate is ignored, the amount is taken')
        self.assertEqual(self.reader.extract_total_spatial(small), Decimal('45.15'))
        self.assertIsNone(self.reader.extract_tax_spatial(self.tagged(('TOTAL', 60, 600), ('9.99', 760, 600))))
        self.assertIsNone(self.reader.extract_total_spatial(self.tagged(('Thank you', 60, 600))))
        self.assertEqual(self.reader.text_blocks('no tags at all'), [])

    def test_a_scanned_receipt_is_matched_and_split_by_tax(self):
        from finance.models import Category, SubCategory
        account = Account.objects.create(name='Card')
        sub = SubCategory.objects.create(name='Hardware', category=Category.objects.create(name='Home', type='EXP'))
        purchase = Transaction.objects.create(account=account, fitid='T', dtposted=date(2026, 10, 2), trnamt=Decimal('-107.00'), name='HARDWARE STORE', subcategory=sub, status='C')
        folders = {name: os.path.join(self.root, name) for name in ('incoming', 'archived', 'failed')}
        for folder in folders.values():
            os.makedirs(folder)
        image = os.path.join(folders['incoming'], 'receipt.jpg')
        with open(image, 'wb') as handle:
            handle.write(b'\xff\xd8\xff not a real photo')

        class Reply:
            status_code = 200
            def json(self):
                return {'text': RECEIPT_OCR_OUTPUT}
        with mock.patch('requests.post', return_value=Reply()):
            self.reader.process_image(image, 'receipt.jpg', folders['archived'], folders['failed'])

        receipt = Receipt.objects.get()
        self.assertEqual((receipt.status, receipt.transaction_id), ('MATCHED', purchase.id))
        self.assertEqual((receipt.date, receipt.total_amount, receipt.tax_amount), (date(2026, 10, 1), Decimal('107.00'), Decimal('7.00')))
        self.assertNotIn('<loc', receipt.raw_text)
        self.assertEqual(purchase.splits.count(), 0, 'reading a receipt must never split a transaction by itself')
        self.assertFalse(receipt.approved)
        self.assertEqual(len(os.listdir(folders['archived'])), 1)


class ReceiptMatchSuggestionTests(StorageTestCase):
    """
    The receipt's total only helps to find the bank's transaction, which may be
    for more than the receipt shows. Likely transactions are offered; none is linked.
    """

    def setUp(self):
        super().setUp()
        from finance.management.commands.ingest_receipts import Command
        self.reader = Command()
        self.reader.stdout = io.StringIO()
        self.account = Account.objects.create(name='Card')
        self.receipt = Receipt.objects.create(original_filename='r.png', image='receipts/r.png', date=date(2026, 5, 25), total_amount=Decimal('89.88'), tax_amount=Decimal('6.85'), status='ORPHANED')

    def purchase(self, fitid, amount, day=date(2026, 5, 26), name='RESTAURANT'):
        return Transaction.objects.create(account=self.account, fitid=fitid, dtposted=day, trnamt=Decimal(amount), name=name, status='C')

    def suggested(self):
        response = self.client.get(f'/api/receipts/{self.receipt.id}/suggestions/')
        self.assertEqual(response.status_code, 200)
        return [row['amount'] for row in response.json()]

    def test_a_line_saying_what_was_paid_beats_the_printed_total(self):
        box = lambda x, y: ''.join(f'<loc_{n}>' for n in (x, y, x + 90, y, x + 90, y + 25, x, y + 25))
        rows = [('Sub Total', 600, 500), ('83.03', 800, 500), ('Tax', 600, 550), ('6.85', 800, 550),
                ('Total', 600, 600), ('89.88', 800, 600), ('Tip', 600, 650), ('17.98', 800, 650),
                ('Amount Paid', 600, 700), ('107.86', 800, 700)]
        text = ''.join(label + box(x, y) for label, x, y in rows)
        self.assertEqual(self.reader.extract_total_spatial(text), Decimal('107.86'))
        self.assertEqual(self.reader.extract_tax_spatial(text), Decimal('6.85'))
        # Cash handed over is not what the purchase cost
        cash = ''.join(label + box(x, y) for label, x, y in [('Total', 600, 600), ('89.88', 800, 600), ('Amount Tendered', 600, 650), ('100.00', 800, 650)])
        self.assertEqual(self.reader.extract_total_spatial(cash), Decimal('89.88'))

    def test_offers_purchases_for_the_total_or_somewhat_more_closest_first(self):
        self.purchase('A', '-107.86')
        self.purchase('B', '-95.00')
        self.purchase('LESS', '-80.00')                             # less than the receipt
        self.purchase('FAR', '-140.00')                             # too far above it
        self.purchase('LATE', '-100.00', day=date(2026, 6, 15))     # outside the dates
        self.purchase('REFUND', '95.00')                            # money in, not a purchase
        self.assertEqual(self.suggested(), ['-95.00', '-107.86'])
        untouched = Receipt.objects.get(pk=self.receipt.pk)
        self.assertEqual((untouched.transaction_id, untouched.status), (None, 'ORPHANED'), 'suggesting links nothing')

    def test_a_transaction_that_already_has_a_receipt_is_not_offered(self):
        taken = self.purchase('A', '-107.86')
        Receipt.objects.create(original_filename='other.png', image='receipts/other.png', date=date(2026, 5, 25), total_amount=Decimal('107.86'), transaction=taken, status='MATCHED', approved=True)
        self.assertEqual(self.suggested(), [])

    def test_nothing_is_offered_without_a_date_or_a_total(self):
        self.purchase('A', '-107.86')
        for missing in ({'date': None}, {'total_amount': None}, {'total_amount': Decimal('0')}):
            Receipt.objects.filter(pk=self.receipt.pk).update(**{'date': date(2026, 5, 25), 'total_amount': Decimal('89.88'), **missing})
            self.assertEqual(self.suggested(), [], missing)

    def test_linking_a_suggestion_keeps_the_banks_amount(self):
        charged = self.purchase('A', '-107.86')
        response = self.api('post', f'/api/receipts/{self.receipt.id}/link_transaction/', {'transaction_id': charged.id})
        self.assertEqual(response.status_code, 200)
        linked = self.client.get('/api/receipts/').json()[0]['linked_transaction']
        self.assertEqual(linked['amount'], '-107.86')
        self.assertEqual(Transaction.objects.get(pk=charged.pk).trnamt, Decimal('-107.86'))


class ReceiptApprovalTests(StorageTestCase):
    """The amounts read from a receipt are suggestions. Nothing is applied until the user approves."""

    def setUp(self):
        super().setUp()
        from finance.models import Category, SubCategory
        self.account = Account.objects.create(name='Card')
        self.sub = SubCategory.objects.create(name='Hardware', category=Category.objects.create(name='Home', type='EXP'))
        self.purchase = Transaction.objects.create(account=self.account, fitid='T', dtposted=date(2026, 10, 2), trnamt=Decimal('-107.00'), name='HARDWARE STORE', subcategory=self.sub, status='C')
        self.receipt = Receipt.objects.create(original_filename='r.jpg', image='receipts/r.jpg', date=date(2026, 10, 1), total_amount=Decimal('107.00'), tax_amount=Decimal('7.00'), transaction=self.purchase, status='MATCHED')

    def approve(self, **body):
        return self.api('post', f'/api/receipts/{self.receipt.id}/approve/', body)

    def splits(self):
        return sorted((split.subcategory.name, split.amount) for split in self.purchase.splits.all())

    def test_inbox_shows_the_linked_transaction_and_that_a_split_is_possible(self):
        listed = self.client.get('/api/receipts/').json()[0]
        linked = listed['linked_transaction']
        self.assertEqual((linked['description'], linked['amount'], linked['subcategory_name'], linked['account_name']), ('HARDWARE STORE', '-107.00', 'Hardware', 'Card'))
        self.assertFalse(linked['already_split'])
        self.assertIsNone(linked['split_blocked_by'])
        self.assertEqual((listed['total_amount'], listed['tax_amount']), ('107.00', '7.00'))

    def test_linking_a_receipt_does_not_split_anything(self):
        Receipt.objects.filter(pk=self.receipt.pk).update(transaction=None, status='ORPHANED')
        response = self.api('post', f'/api/receipts/{self.receipt.id}/link_transaction/', {'transaction_id': self.purchase.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.splits(), [])

    def test_approving_without_a_split_changes_nothing_in_the_ledger(self):
        response = self.approve(split_tax=False)
        self.assertEqual((response.status_code, response.json()['tax_split']), (200, False))
        self.assertEqual(self.splits(), [])
        self.assertTrue(Receipt.objects.get(pk=self.receipt.pk).approved)
        self.assertEqual(self.approve().status_code, 404, 'an approved receipt has left the inbox')

    def test_approving_with_a_split_uses_the_reviewed_tax(self):
        response = self.approve(split_tax=True)
        self.assertEqual((response.status_code, response.json()['tax_split']), (200, True))
        self.assertEqual(self.splits(), [('Hardware', Decimal('-100.00')), ('Sales Tax', Decimal('-7.00'))])
        self.assertEqual(sum(amount for _, amount in self.splits()), self.purchase.trnamt, 'the parts add up to the whole')

    def test_corrected_amounts_are_what_gets_approved_and_saved(self):
        response = self.approve(split_tax=True, tax_amount=6.42, total_amount=107)
        self.assertEqual(response.status_code, 200)
        saved = Receipt.objects.get(pk=self.receipt.pk)
        self.assertEqual((saved.tax_amount, saved.total_amount), (Decimal('6.42'), Decimal('107')))
        self.assertEqual(self.splits(), [('Hardware', Decimal('-100.58')), ('Sales Tax', Decimal('-6.42'))])

    def test_a_refund_is_split_in_its_own_direction(self):
        Transaction.objects.filter(pk=self.purchase.pk).update(trnamt=Decimal('107.00'))
        self.assertEqual(self.approve(split_tax=True).status_code, 200)
        self.assertEqual(self.splits(), [('Hardware', Decimal('100.00')), ('Sales Tax', Decimal('7.00'))])

    def test_a_split_that_cannot_be_made_leaves_the_receipt_unapproved_and_says_why(self):
        from finance.models import TransactionSplit
        cases = []
        cases.append(('as large as the whole transaction', dict(split_tax=True, tax_amount=107)))
        cases.append(('as large as the whole transaction', dict(split_tax=True, tax_amount=500)))
        cases.append(('no tax amount', dict(split_tax=True, tax_amount=0)))
        for message, body in cases:
            response = self.approve(**body)
            self.assertEqual(response.status_code, 409, body)
            self.assertIn(message, response.json()['error'])
            saved = Receipt.objects.get(pk=self.receipt.pk)
            self.assertFalse(saved.approved)
            self.assertEqual(saved.tax_amount, Decimal('7.00'), 'a refused approval saves nothing')
            self.assertEqual(self.splits(), [])

        Transaction.objects.filter(pk=self.purchase.pk).update(subcategory=None)
        self.assertIn('no category', self.approve(split_tax=True).json()['error'])
        Transaction.objects.filter(pk=self.purchase.pk).update(subcategory=self.sub)

        TransactionSplit.objects.create(transaction=self.purchase, subcategory=self.sub, amount=Decimal('-107.00'))
        response = self.approve(split_tax=True)
        self.assertEqual(response.status_code, 409)
        self.assertIn('already split', response.json()['error'])
        self.assertEqual(self.purchase.splits.count(), 1)
        blocked = self.client.get('/api/receipts/').json()[0]['linked_transaction']
        self.assertTrue(blocked['already_split'] and 'already split' in blocked['split_blocked_by'])
        self.assertEqual(self.approve(split_tax=False).status_code, 200, 'it can still be approved without a split')

    def test_unlinked_receipt_and_bad_amounts(self):
        Receipt.objects.filter(pk=self.receipt.pk).update(transaction=None, status='ORPHANED')
        self.assertIn('not linked', self.approve(split_tax=True).json()['error'])
        self.assertIsNone(self.client.get('/api/receipts/').json()[0]['linked_transaction'])
        for bad in ({'tax_amount': 'lots'}, {'tax_amount': -1}, {'total_amount': 'x'}):
            response = self.approve(**bad)
            self.assertEqual(response.status_code, 400, bad)
            self.assertIn('zero or more', response.json()['error'], 'the page shows the reason, not a generic failure')
        self.assertFalse(Receipt.objects.get(pk=self.receipt.pk).approved)


class ReceiptDeleteTests(StorageTestCase):
    """A receipt stuck in the inbox can be deleted; doing so never touches the ledger."""

    def setUp(self):
        super().setUp()
        from finance.models import Category, SubCategory, TransactionSplit
        account = Account.objects.create(name='Card')
        sub = SubCategory.objects.create(name='Hardware', category=Category.objects.create(name='Home', type='EXP'))
        self.purchase = Transaction.objects.create(account=account, fitid='T', dtposted=date(2026, 10, 2), trnamt=Decimal('-107.00'), name='HARDWARE STORE', subcategory=sub, status='C')
        TransactionSplit.objects.create(transaction=self.purchase, subcategory=sub, amount=Decimal('-107.00'), memo='existing split')

    def receipt(self, name, **fields):
        receipt = Receipt(original_filename=name, date=date(2026, 10, 1), total_amount=Decimal('107.00'), **fields)
        receipt.image.save(name, ContentFile(b'image bytes'))
        receipt.save()
        return receipt

    def test_deleting_removes_the_record_and_its_image_only(self):
        stuck = self.receipt('stuck.jpg', status='PROCESSING')
        linked = self.receipt('linked.jpg', status='MATCHED', transaction=self.purchase)
        other = self.receipt('keep.jpg', status='ORPHANED')
        stuck_path, linked_path, other_path = stuck.image.path, linked.image.path, other.image.path

        self.assertEqual(self.client.delete(f'/api/receipts/{stuck.id}/').status_code, 204)
        self.assertFalse(Receipt.objects.filter(pk=stuck.pk).exists())
        self.assertFalse(os.path.exists(stuck_path))

        self.assertEqual(self.client.delete(f'/api/receipts/{linked.id}/').status_code, 204)
        self.assertFalse(os.path.exists(linked_path))
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.trnamt, Decimal('-107.00'))
        self.assertEqual([split.memo for split in self.purchase.splits.all()], ['existing split'], 'the linked transaction is untouched')

        self.assertTrue(Receipt.objects.filter(pk=other.pk).exists() and os.path.exists(other_path), 'other receipts are untouched')
        self.assertEqual([row['id'] for row in self.client.get('/api/receipts/').json()], [other.id])

    def test_a_deleted_receipt_can_be_scanned_again(self):
        stuck = self.receipt('again.jpg', status='PROCESSING')
        self.client.delete(f'/api/receipts/{stuck.id}/')
        self.assertFalse(Receipt.objects.filter(original_filename='again.jpg').exists(), 'the importer skips file names it already has')

    def test_approved_receipts_cannot_be_deleted_from_the_inbox(self):
        filed = self.receipt('filed.jpg', status='MATCHED', transaction=self.purchase, approved=True)
        self.assertEqual(self.client.delete(f'/api/receipts/{filed.id}/').status_code, 404)
        self.assertTrue(Receipt.objects.filter(pk=filed.pk).exists() and os.path.exists(filed.image.path))

    def test_missing_image_file_and_expired_image_do_not_block_deletion(self):
        gone = self.receipt('gone.jpg', status='PROCESSING')
        os.remove(gone.image.path)
        self.assertEqual(self.client.delete(f'/api/receipts/{gone.id}/').status_code, 204)

        expired = Receipt.objects.create(original_filename='old.jpg', image='', image_expired_at=timezone.now(), status='ORPHANED')
        self.assertEqual(self.client.delete(f'/api/receipts/{expired.id}/').status_code, 204)
        self.assertEqual(Receipt.objects.count(), 0)

    def test_signed_out_and_unknown(self):
        stuck = self.receipt('stuck.jpg', status='PROCESSING')
        self.assertEqual(self.client.delete('/api/receipts/999999/').status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.delete(f'/api/receipts/{stuck.id}/').status_code, 403)
        self.assertTrue(Receipt.objects.filter(pk=stuck.pk).exists())


class RateLimitTests(StorageTestCase):
    """A backstop against scripts: too many requests in a short time are refused with 429."""

    def setUp(self):
        super().setUp()
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)

    def limits(self, **limits):
        from django.conf import settings
        overrides = override_settings(API_RATE_LIMITS={**settings.API_RATE_LIMITS, **limits})
        overrides.enable()
        self.addCleanup(overrides.disable)

    def test_the_real_settings_set_a_limit_for_everything_and_share_counts_between_workers(self):
        from config import settings as real
        self.assertEqual(set(real.API_RATE_LIMITS), {'anon', 'user', 'uploads', 'heavy', 'sign_in'})
        self.assertTrue(all(real.API_RATE_LIMITS.values()), 'every limit is on by default')
        self.assertIn('filebased', real.CACHES['default']['BACKEND'], 'in-memory counts would be per worker process')
        self.assertEqual(real.REST_FRAMEWORK['DEFAULT_THROTTLE_CLASSES'], ['finance.throttles.VisitorLimit', 'finance.throttles.UserLimit'])

    def test_a_signed_in_user_is_limited_and_told_how_long_to_wait(self):
        self.limits(user='3/minute')
        self.assertEqual([self.client.get('/api/accounts/').status_code for _ in range(4)], [200, 200, 200, 429])
        refused = self.client.get('/api/accounts/')
        self.assertEqual(refused.status_code, 429)
        self.assertGreater(int(refused['Retry-After']), 0)

    def test_each_user_has_their_own_count(self):
        self.limits(user='2/minute')
        self.assertEqual([self.client.get('/api/accounts/').status_code for _ in range(3)], [200, 200, 429])
        self.as_member()
        self.assertEqual(self.client.get('/api/accounts/').status_code, 200)

    def test_visitors_are_limited_by_address_and_signed_in_users_are_not_caught_by_it(self):
        self.limits(anon='2/minute')
        self.assertEqual([self.client.get('/api/accounts/').status_code for _ in range(4)], [200] * 4, 'signed in: not a visitor')
        self.client.logout()
        self.assertEqual([self.client.get('/api/me/').status_code for _ in range(3)], [200, 200, 429])
        # A different visitor, as reported by the one proxy in front of the server
        self.assertEqual(self.client.get('/api/me/', HTTP_X_FORWARDED_FOR='203.0.113.9').status_code, 200)
        # A made-up address in front of the real one changes nothing: the proxy's entry is the last
        self.assertEqual(self.client.get('/api/me/', HTTP_X_FORWARDED_FOR='198.51.100.1, 127.0.0.1').status_code, 429)

    def test_uploads_and_heavy_work_have_their_own_tighter_limits(self):
        self.limits(uploads='2/minute', heavy='1/hour')
        send = lambda name: self.client.post('/api/upload/', {'file': SimpleUploadedFile(name, b'OFXHEADER:100')})
        self.assertEqual([send(f'bank{n}.qfx').status_code for n in range(3)], [201, 201, 429])
        self.assertEqual(len(os.listdir(self.dropzone)), 2, 'a refused upload is not saved')
        self.assertEqual(self.api('post', '/api/retention/run/').status_code, 200)
        self.assertEqual(self.api('post', '/api/retention/run/').status_code, 429)
        self.assertEqual(self.api('post', '/api/backups/run/', {'kind': 'db'}).status_code, 429, 'backups share the heavy limit')
        self.assertEqual(self.client.get('/api/accounts/').status_code, 200, 'ordinary use is unaffected')

    def test_sign_in_attempts_are_limited_by_address(self):
        self.limits(sign_in='2/minute')
        self.client.logout()
        attempts = [self.client.get('/admin/login/').status_code for _ in range(3)]
        self.assertEqual(attempts[2], 429)
        self.assertNotIn(429, attempts[:2])
        self.assertEqual(self.client.get('/api/me/').status_code, 200, 'the rest of the site still answers')

    def test_a_limit_can_be_switched_off(self):
        self.limits(user=None)
        self.assertEqual({self.client.get('/api/accounts/').status_code for _ in range(30)}, {200})


class ReceiptReaderTests(StorageTestCase):
    """When the receipt reader is not answering, photos are refused with what to do, not lost."""

    def setUp(self):
        super().setUp()
        from django.core.cache import cache
        cache.delete('receipt-reader-available')
        self.addCleanup(cache.delete, 'receipt-reader-available')

    def test_off_or_not_answering(self):
        import requests
        from django.core.files.uploadedfile import SimpleUploadedFile
        with mock.patch('finance.receipt_reader.requests.get', side_effect=requests.ConnectionError):
            state = self.client.get('/api/receipts/reader/').json()
            self.assertFalse(state['available'])
            self.assertIn('docker compose up -d ai-worker', state['how_to_turn_on'])
            refused = self.client.post('/api/upload/', {'file': SimpleUploadedFile('lunch.jpg', b'x' * 10)})
            self.assertEqual(refused.status_code, 503)
            self.assertIn("receipt reader isn't answering", refused.json()['error'])
            self.assertEqual(self.client.post('/api/upload/', {'file': SimpleUploadedFile('bank.qfx', b'x' * 10)}).status_code, 201, 'bank files still go in')
        self.assertFalse(os.path.exists(os.path.join(self.dropzone, 'lunch.jpg')))

    def test_on_and_ready(self):
        ready = mock.Mock(status_code=200, json=lambda: {'status': 'ready'})
        with mock.patch('finance.receipt_reader.requests.get', return_value=ready) as asked:
            state = self.client.get('/api/receipts/reader/').json()
            self.assertEqual((state['installed'], state['available'], state['how_to_turn_on']), (True, True, None))
            self.client.get('/api/receipts/reader/')
        self.assertEqual(asked.call_count, 1, 'remembered for a while, not asked on every request')
        self.assertEqual(asked.call_args.args[0], 'http://ai-worker:5000/health')

    def test_counts_photos_waiting_in_the_dropzone(self):
        os.makedirs(self.dropzone, exist_ok=True)
        for name in ('a.jpg', 'b.PNG', 'statement.qfx', '.upload-x.part'):
            with open(os.path.join(self.dropzone, name), 'wb') as f:
                f.write(b'x')
        with mock.patch('finance.receipt_reader.requests.get', return_value=mock.Mock(status_code=200, json=lambda: {'status': 'ready'})):
            self.assertEqual(self.client.get('/api/receipts/reader/').json()['waiting'], 2)

    @override_settings(RECEIPT_READER_INSTALLED=False)
    def test_not_installed(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.core.management import call_command
        with mock.patch('finance.receipt_reader.requests.get') as asked:
            state = self.client.get('/api/receipts/reader/').json()
            self.assertEqual((state['installed'], state['available'], state['waiting']), (False, False, 0))
            self.assertIn('receipt-reader.sh on', state['how_to_turn_on'])
            with override_settings(INSTALL_DIR='/srv/finance'):
                commands = self.client.get('/api/receipts/reader/').json()
            self.assertEqual((commands['command_on'], commands['command_off']),
                             ('cd /srv/finance && ./scripts/receipt-reader.sh on', 'cd /srv/finance && ./scripts/receipt-reader.sh off'))
            refused = self.client.post('/api/upload/', {'file': SimpleUploadedFile('lunch.jpg', b'x' * 10)})
            self.assertEqual(refused.status_code, 503)
            self.assertIn("receipt reader isn't installed", refused.json()['error'])
            self.assertEqual(self.client.post('/api/upload/', {'file': SimpleUploadedFile('bank.qfx', b'x' * 10)}).status_code, 201, 'bank files still go in')
        asked.assert_not_called()
        # A photo copied straight into the dropzone waits there, untouched
        with mock.patch('finance.management.commands.ingest_receipts.os.listdir') as looked:
            call_command('ingest_receipts', stdout=io.StringIO())
        looked.assert_not_called()


class CsvImportTests(StorageTestCase):
    """Bank CSVs are read by their column headings, whichever bank they come from."""

    def write(self, name, text):
        path = os.path.join(self.root, name)
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text)
        return path

    def read(self, name, text):
        from finance.csv_import import read_csv
        return read_csv(self.write(name, text))

    def test_common_layouts(self):
        # A checking account: newest first, a running balance
        checking = self.read('Chase1234_Activity_20261031.CSV',
            'Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #,\n'
            'DEBIT,10/28/2026,"COFFEE SHOP  123",-4.50,DEBIT_CARD,1995.50,,\n'
            'CREDIT,10/28/2026,PAYROLL,2000.00,ACH_CREDIT,2000.00,,\n'
            'DEBIT,10/20/2026,GROCER,-50.25,DEBIT_CARD,0.00,,\n')
        self.assertEqual(checking.account_id, 'csv:chase1234-activity', 'the file name, without its date')
        self.assertEqual([(t.day, t.amount, t.name) for t in checking.transactions], [
            (date(2026, 10, 28), Decimal('-4.50'), 'COFFEE SHOP 123'),
            (date(2026, 10, 28), Decimal('2000.00'), 'PAYROLL'),
            (date(2026, 10, 20), Decimal('-50.25'), 'GROCER')])
        self.assertEqual((checking.balance, checking.balance_date), (Decimal('1995.50'), date(2026, 10, 28)), 'the last of the newest day')

        # A card with both dates: the posted date is used, as in QFX files
        card = self.read('card.csv',
            'Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n'
            '10/25/2026,10/27/2026,BOOKSHOP,Shopping,Sale,-12.99,gift\n')
        self.assertEqual((card.transactions[0].day, card.transactions[0].memo), (date(2026, 10, 27), 'gift'))
        self.assertIsNone(card.balance)

        # Separate money-out and money-in columns
        split = self.read('2026-10-31_transaction_download.csv',
            'Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n'
            '2026-10-02,2026-10-03,1111,FUEL,Gas,25.00,\n'
            '2026-10-05,2026-10-06,1111,PAYMENT THANK YOU,Payment,,100.00\n')
        self.assertEqual([t.amount for t in split.transactions], [Decimal('-25.00'), Decimal('100.00')])
        self.assertEqual(split.account_id, 'csv:transaction-download')
        self.assertTrue(split.sign_facts['certain'], 'money out and money in columns settle the direction')

        # Title lines above the headings, semicolons, brackets for minus, a totals line at the end
        messy = self.read('export.csv',
            'Account activity\n\nDate;Payee;Amount;Reference\n'
            '03.10.2026;RENT;(1200.00);R-1\n'
            '04.10.2026;REFUND;$15.00;R-2\n'
            'Total;;(1185.00);\n')
        self.assertEqual([(t.day, t.amount, t.fitid) for t in messy.transactions], [
            (date(2026, 10, 3), Decimal('-1200.00'), 'R-1'), (date(2026, 10, 4), Decimal('15.00'), 'R-2')])

    def test_a_second_download_is_the_same_account(self):
        import os
        from django.core.files.uploadedfile import SimpleUploadedFile
        from finance.csv_import import account_id_for
        with mock.patch('finance.receipt_reader.available', return_value=True):
            for name in ('History_for_Account_X00000000(1).csv', 'History_for_Account_X00000000 (2).csv', 'Activity(1).csv'):
                self.assertEqual(self.client.post('/api/upload/', {'file': SimpleUploadedFile(name, b'x')}).status_code, 201, name)
        saved = sorted(os.listdir(self.dropzone))
        self.assertEqual(saved, ['Activity_copy-1.csv', 'History_for_Account_X00000000_copy-1.csv', 'History_for_Account_X00000000_copy-2.csv'],
                         "the copy number kept apart from the account number")
        self.assertEqual({account_id_for(name) for name in saved if name.startswith('History')}, {'X00000000'})
        self.assertEqual({account_id_for(name) for name in ('activity.csv', 'Activity_copy-1.csv', 'activity (3).csv', 'activity copy.csv')},
                         {'csv:activity'})

    def test_the_same_row_gets_the_same_id_every_download(self):
        text = 'Date,Description,Amount\n10/01/2026,TOLL,-2.00\n10/01/2026,TOLL,-2.00\n'
        first, again = self.read('a.csv', text), self.read('b.csv', text)
        self.assertEqual([t.fitid for t in first.transactions], [t.fitid for t in again.transactions])
        self.assertEqual(len({t.fitid for t in first.transactions}), 2, 'two identical rows stay two')

    def test_fidelity_keeps_its_ids(self):
        import hashlib
        statement = self.read('History_for_Account_X00000000.csv',
            '\n\nRun Date,Action,Symbol,Description,Type,Quantity,Price ($),Commission ($),Fees ($),Accrued Interest ($),Amount ($),Cash Balance ($),Settlement Date\n'
            '10/30/2026,DIRECT DEBIT ACME UTILITY,,No Description,Cash,0,,,,,-80.5,919.50,\n'
            '10/29/2026,TRANSFER IN,,Cash,Cash,0,,,,,1000,1000.00,\n'
            '\n"The data and information in this spreadsheet is provided to you solely for your use"\n')
        self.assertEqual(statement.account_id, 'X00000000')
        self.assertEqual([t.name for t in statement.transactions], ['DIRECT DEBIT ACME UTILITY', 'TRANSFER IN Cash'])
        legacy = hashlib.md5('10/30/2026DIRECT DEBIT ACME UTILITY-80.5_0'.encode()).hexdigest()
        self.assertEqual(statement.transactions[0].fitid, legacy, 'the id the importer has always given it')
        self.assertEqual((statement.balance, statement.balance_date), (Decimal('919.50'), date(2026, 10, 30)))

    def test_a_file_it_cannot_read_says_why(self):
        from finance.csv_import import CsvLayoutError
        with self.assertRaisesMessage(CsvLayoutError, 'Headings found: Foo, Bar'):
            self.read('odd.csv', 'Foo,Bar\n1,2\n')

    def stage(self, name, text):
        from io import StringIO
        from pathlib import Path
        from finance.management.commands.stage_import import Command
        from finance.rules import active_rules
        command = Command(stdout=StringIO())
        command.rules = active_rules()
        os.makedirs(os.path.join(self.archive, 'qfx'), exist_ok=True)
        command.process_file(Path(self.write(name, text)), Path(self.archive) / 'qfx')

    def test_the_account_picked_for_a_csv_is_remembered(self):
        from finance.models import StagedTransaction
        checking = Account.objects.create(name='Checking', account_type='CHECKING')
        text = 'Date,Description,Amount\n10/01/2026,CAFE,-4.00\n10/02/2026,PAY,100.00\n'
        self.stage('activity.csv', text)
        parked = StagedTransaction.objects.filter(account__isnull=True)
        self.assertEqual(set(parked.values_list('unregistered_account_id', flat=True)), {'csv:activity'})
        self.assertEqual(self.api('post', '/api/import/resolve/', {'unregistered_id': 'csv:activity', 'real_account_id': checking.pk}).status_code, 200)
        StagedTransaction.objects.all().delete()
        self.stage('activity (1).csv', text.replace('10/0', '11/0'))
        staged = StagedTransaction.objects.all()
        self.assertTrue(staged.exists() and all(row.account_id == checking.pk for row in staged), 'straight to the remembered account')

    def card_file(self):
        # This issuer writes purchases as positive and payments as negative
        return ('Date,Description,Amount\n10/01/2026,CAFE,4.00\n10/02/2026,BOOKS,20.00\n'
                '10/03/2026,GROCER,30.00\n10/04/2026,PAYMENT THANK YOU,-54.00\n')

    def test_which_way_the_amounts_go_is_asked_once_with_an_example(self):
        from finance.models import StagedTransaction
        card = Account.objects.create(name='Card', account_type='CREDIT CARD')
        self.stage('card.csv', self.card_file())
        self.api('post', '/api/import/resolve/', {'unregistered_id': 'csv:card', 'real_account_id': card.pk})
        amounts = lambda: sorted(StagedTransaction.objects.values_list('amount', flat=True))
        self.assertEqual(amounts(), [Decimal('-30.00'), Decimal('-20.00'), Decimal('-4.00'), Decimal('54.00')],
                         'best guess: the payment is money in, so the file was the other way round')
        [waiting] = self.client.get('/api/import/files/').json()
        self.assertTrue(waiting['ask'])
        self.assertEqual((waiting['example']['name'], Decimal(waiting['example']['amount'])), ('CAFE', Decimal('-4.00')))
        self.assertEqual(self.api('post', f"/api/import/files/{waiting['id']}/signs/", {'action': 'right'}).status_code, 200)
        card.refresh_from_db()
        self.assertTrue(card.import_amounts_reversed and card.import_sign_confirmed, 'kept with the account')

        # The next file from this card: reversed without asking
        StagedTransaction.objects.all().delete()
        self.stage('card (1).csv', self.card_file().replace('10/0', '11/0'))
        self.assertEqual(amounts()[0], Decimal('-30.00'))
        self.assertFalse(self.client.get('/api/import/files/').json()[0]['ask'])

    def test_a_wrong_guess_is_one_click_to_put_right(self):
        from finance.models import StagedTransaction
        card = Account.objects.create(name='Card', account_type='CREDIT CARD', account_number='')
        self.stage('card.csv', self.card_file().replace('PAYMENT THANK YOU', 'ADJUSTMENT'))
        self.api('post', '/api/import/resolve/', {'unregistered_id': 'csv:card', 'real_account_id': card.pk})
        [waiting] = self.client.get('/api/import/files/').json()
        self.assertTrue(waiting['ask'] and waiting['card_mostly_money_in'], 'no payment to go on: a card file that is mostly money in')
        self.api('post', f"/api/import/files/{waiting['id']}/signs/", {'action': 'reverse'})
        self.assertEqual(sorted(StagedTransaction.objects.values_list('amount', flat=True))[0], Decimal('-30.00'))
        card.refresh_from_db()
        self.assertTrue(card.import_amounts_reversed and card.import_sign_confirmed)
        self.assertFalse(self.client.get('/api/import/files/').json()[0]['card_mostly_money_in'])

    def test_what_the_file_itself_settles(self):
        # Unsigned amounts with a direction column
        typed = self.read('typed.csv', 'Date,Description,Amount,Type\n10/01/2026,CAFE,4.00,Debit\n10/02/2026,PAY,100.00,Credit\n'
                                       '10/03/2026,FEE,2.00,DR\n')
        self.assertEqual([t.amount for t in typed.transactions], [Decimal('-4.00'), Decimal('100.00'), Decimal('-2.00')])
        self.assertTrue(typed.sign_facts['certain'])
        # Signed amounts the direction column contradicts throughout: reversed
        contrary = self.read('contrary.csv', 'Date,Description,Amount,Transaction Type\n10/01/2026,CAFE,4.00,Sale\n'
                                             '10/02/2026,BOOKS,9.00,Sale\n10/03/2026,PAYMENT,-13.00,Payment\n')
        self.assertEqual((contrary.sign_facts['certain'], contrary.sign_facts['reversed']), (True, True))
        # A running balance that moves the other way from the amounts
        balance = self.read('bal.csv', 'Date,Description,Amount,Balance\n10/01/2026,A,10.00,90.00\n10/02/2026,B,5.00,85.00\n'
                                       '10/03/2026,C,-20.00,105.00\n10/04/2026,D,1.00,104.00\n10/05/2026,E,2.00,102.00\n')
        self.assertEqual(balance.sign_facts['balance_says'], 'reversed')

    def test_how_the_file_and_the_account_decide(self):
        from finance.csv_import import decide_signs
        checking = Account(name='Checking', account_type='CHECKING')
        card = Account(name='Card', account_type='CREDIT CARD')
        unsure = {'certain': False, 'reversed': False, 'balance_says': 'reversed', 'payments_negative': True}
        self.assertEqual(decide_signs(None, checking), (False, True), 'QFX: the standard')
        self.assertEqual(decide_signs({'certain': True, 'reversed': True}, checking), (True, True))
        self.assertEqual(decide_signs(unsure, None), (False, False), 'waits for the account')
        self.assertEqual(decide_signs(unsure, checking), (True, True), 'the balance settles it for a bank account')
        self.assertEqual(decide_signs(unsure, card), (True, False), "a card's balance may count what's owed: a guess, to confirm")
        card.import_sign_confirmed, card.import_amounts_reversed = True, False
        self.assertEqual(decide_signs(unsure, card), (False, True), 'the answer kept with the account wins')
        checking.import_amounts_reversed = True
        self.assertEqual(decide_signs(None, checking), (True, True), 'set by hand for a bank whose QFX files are backwards')

    def test_rows_from_a_file_are_in_the_balance_it_set(self):
        from finance.models import BankImport, StagedTransaction
        from finance.networth import history
        bank = Account.objects.create(name='Checking', account_type='CHECKING', account_number='')
        from finance.models import AccountAlias
        AccountAlias.objects.create(account=bank, identifier='csv:statement')
        # Entered by hand, still pending: the import matches it
        typed = Transaction.objects.create(account=bank, fitid='', dtposted=date(2026, 11, 2), trnamt=Decimal('-40.00'),
                                           name='GAS', status='P', is_manual=True)
        self.stage('statement.csv', 'Date,Description,Amount,Balance\n11/01/2026,PAY,1000.00,1000.00\n'
                                    '11/02/2026,GAS,-40.00,960.00\n11/03/2026,FOOD,-10.00,950.00\n')
        bank.refresh_from_db()
        self.assertEqual((bank.last_balance, bank.balance_date), (Decimal('950.00'), date(2026, 11, 3)))
        self.assertEqual(BankImport.objects.get(filename='statement.csv').balance_as_of, date(2026, 11, 3))
        for row in StagedTransaction.objects.all():
            self.assertEqual(self.api('post', f'/api/import/inbox/{row.id}/approve/').status_code, 200, row.name)
        self.assertEqual(set(Transaction.objects.filter(account=bank).values_list('in_balance_as_of', flat=True)), {date(2026, 11, 3)})
        typed.refresh_from_db()
        self.assertEqual((typed.status, typed.in_balance_as_of), ('C', date(2026, 11, 3)), 'the matched entry too')
        today = history(today=date(2026, 11, 6))['points'][-1]
        self.assertEqual(today['accounts'][str(bank.pk)], Decimal('950.00'), "exactly the bank's balance")

    def test_two_identical_charges_without_bank_ids(self):
        from finance.models import StagedTransaction
        bank = Account.objects.create(name='Card', account_type='CREDIT CARD', account_number='9999')
        qfx = ('<OFX><BANKACCTFROM><ACCTID>9999</ACCTID></BANKACCTFROM>'
               '<STMTTRN><DTPOSTED>20261102<TRNAMT>-4.50<NAME>COFFEE</STMTTRN>'
               '<STMTTRN><DTPOSTED>20261102<TRNAMT>-4.50<NAME>COFFEE</STMTTRN>'
               '<STMTTRN><DTPOSTED>20261103<TRNAMT>-9.00<NAME>LUNCH</STMTTRN></OFX>')
        self.stage('card.qfx', qfx)
        rows = list(StagedTransaction.objects.order_by('id'))
        self.assertEqual(len({row.fitid for row in rows}), 3, 'each has an id of its own')
        self.assertTrue(all(row.fitid.startswith('gen-') for row in rows))
        self.assertEqual([row.match_type for row in rows], ['NEW', 'NEW', 'NEW'])
        for row in rows:
            self.assertEqual(self.api('post', f'/api/import/inbox/{row.id}/approve/').status_code, 200, 'both coffees go in')
        self.assertEqual(Transaction.objects.filter(account=bank, name='COFFEE').count(), 2)

        # The same file again: all three recognised as already imported
        self.stage('card (1).qfx', qfx)
        self.assertEqual(set(StagedTransaction.objects.values_list('match_type', flat=True)), {'DUPE'})

    def test_the_same_file_twice_before_approving(self):
        from finance.models import StagedTransaction
        bank = Account.objects.create(name='Everyday', account_type='CHECKING', account_number='5555')
        # Two pending entries for two identical transfers
        for name in ('College fund A', 'College fund B'):
            Transaction.objects.create(account=bank, fitid='', dtposted=date(2026, 11, 3), trnamt=Decimal('-150.00'), name=name, status='P')
        qfx = ('<OFX><BANKACCTFROM><ACCTID>5555</ACCTID></BANKACCTFROM>'
               '<STMTTRN><DTPOSTED>20261103<TRNAMT>-150.00<FITID>A1<NAME>TRANSFERRED TO VS</STMTTRN>'
               '<STMTTRN><DTPOSTED>20261103<TRNAMT>-150.00<FITID>A2<NAME>TRANSFERRED TO VS</STMTTRN></OFX>')
        self.stage('cash.qfx', qfx)
        self.stage('cash (1).qfx', qfx)
        self.assertEqual(StagedTransaction.objects.count(), 2, 'rows already waiting are not staged again')
        self.assertEqual(sorted(StagedTransaction.objects.values_list('match_type', flat=True)), ['MATCH', 'MATCH'])

    def test_a_match_whose_bank_transaction_came_in_another_way(self):
        from finance.models import StagedTransaction
        bank = Account.objects.create(name='Everyday')
        entry = Transaction.objects.create(account=bank, fitid='', dtposted=date(2026, 11, 3), trnamt=Decimal('-150.00'), name='College fund A', status='P')
        stale = StagedTransaction.objects.create(account=bank, fitid='A2', date=date(2026, 11, 3), amount=Decimal('-150.00'),
                                                 name='College fund A', match_type='MATCH', match_proposal=entry)
        # The same bank transaction, approved from another copy as a new one
        Transaction.objects.create(account=bank, fitid='A2', dtposted=date(2026, 11, 3), trnamt=Decimal('-150.00'), name='TRANSFERRED', status='C')
        self.client.get('/api/import/inbox/')
        stale.refresh_from_db()
        self.assertEqual(stale.match_type, 'DUPE', 'already in the ledger, so a duplicate, not a match')

    def test_entries_without_a_bank_id_can_match_in_every_detail(self):
        bank = Account.objects.create(name='Cash')
        for _ in range(2):
            Transaction.objects.create(account=bank, fitid='', dtposted=date(2026, 11, 2), trnamt=Decimal('-5.00'), name='PARKING', status='C')
        self.assertEqual(Transaction.objects.filter(account=bank).count(), 2)

    def test_a_crash_while_importing_is_set_aside_not_retried(self):
        from io import StringIO
        from pathlib import Path
        from finance.management.commands.stage_import import Command
        os.makedirs(self.dropzone, exist_ok=True)
        path = os.path.join(self.dropzone, 'bank.qfx')
        with open(path, 'w') as handle:
            handle.write('<OFX><STMTTRN><TRNAMT>-1.00</STMTTRN></OFX>')
        with mock.patch.object(Command, 'process_file', side_effect=RuntimeError('boom')), \
                mock.patch('finance.management.commands.stage_import.Path', side_effect=lambda p: Path(self.dropzone) if p == '/data/dropzone' else Path(p)), \
                self.assertLogs('finance.management.commands.stage_import', 'ERROR'):
            Command(stdout=StringIO()).handle()
        [problem] = self.client.get('/api/import/problems/').json()
        self.assertIn("watcher's log", problem['problem'])
        self.assertNotIn('boom', problem['problem'], 'the details stay in the log')
        self.assertFalse(os.path.exists(path), 'set aside, so it is not tried again')

    def test_a_file_that_cannot_be_imported_is_shown_and_put_aside(self):
        self.stage('notes.csv', 'Foo,Bar\n1,2\n')
        problems = self.client.get('/api/import/problems/').json()
        self.assertEqual(len(problems), 1)
        self.assertIn('Headings found: Foo, Bar', problems[0]['problem'])
        self.assertFalse(os.path.exists(os.path.join(self.root, 'notes.csv')), 'moved aside')
        self.assertTrue(any(name.endswith('notes.csv') for name in os.listdir(os.path.join(self.archive, 'failed'))))
        self.api('post', f"/api/import/problems/{problems[0]['id']}/dismiss/")
        self.assertEqual(self.client.get('/api/import/problems/').json(), [])

        self.stage('not-a-statement.qfx', 'hello')
        self.assertIn('no bank statement found', self.client.get('/api/import/problems/').json()[0]['problem'])


class CategoryChangesTests(StorageTestCase):
    """Renaming, moving and deleting categories without losing anything."""

    def setUp(self):
        super().setUp()
        from finance.models import Category, SubCategory
        self.food = Category.objects.create(name='Food', type='EXP')
        self.home = Category.objects.create(name='Home', type='EXP')
        self.transfers = Category.objects.create(name='Transfers', type='XFR')
        self.grocery = SubCategory.objects.create(name='Grocery', category=self.food)
        self.groceries = SubCategory.objects.create(name='Groceries', category=self.food)
        self.account = Account.objects.create(name='Bank')

    def everything_using(self, sub):
        """One of each kind of record that can use a subcategory."""
        from finance.models import (Budget, LoanDetail, RecurringTransaction, StagedTransaction, TransactionRule,
                                    TransactionSplit)
        day = date(2026, 10, 1)
        tx = Transaction.objects.create(account=self.account, fitid='T', dtposted=day, trnamt=Decimal('-10'), name='SHOP', status='C', subcategory=sub)
        split_tx = Transaction.objects.create(account=self.account, fitid='S', dtposted=day, trnamt=Decimal('-10'), name='SPLIT', status='C')
        TransactionSplit.objects.create(transaction=split_tx, subcategory=sub, amount=Decimal('-10'))
        TransactionRule.objects.create(rule_name='shop', subcategory=sub, conditions=[{'field': 'name', 'operator': 'contains', 'value': 'SHOP'}])
        Budget.objects.create(subcategory=sub, year=2026, month=10, amount=Decimal('100'))
        Budget.objects.create(subcategory=sub, year=2026, month=11, amount=Decimal('50'))
        RecurringTransaction.objects.create(name='Box', amount=Decimal('-20'), account=self.account, subcategory=sub,
                                            frequency='MONTHLY', start_date=day)
        StagedTransaction.objects.create(account=self.account, fitid='A', date=day, amount=Decimal('-5'), name='X', subcategory=sub)
        StagedTransaction.objects.create(account=self.account, fitid='B', date=day, amount=Decimal('-5'), name='Y',
                                         splits=[{'subcategory': sub.pk, 'amount': '-5.00', 'memo': ''}])
        loan_account = Account.objects.create(name='Loan')
        LoanDetail.objects.create(liability_account=loan_account, original_balance=Decimal('1000'), interest_rate=Decimal('5'),
                                  term_months=12, first_payment_date=day, pmi_category=sub)
        return tx

    def test_rename_and_move(self):
        response = self.api('patch', f'/api/subcategories/{self.grocery.pk}/', {'name': 'Supermarket'})
        self.assertEqual((response.status_code, response.json()['name']), (200, 'Supermarket'))
        clash = self.api('patch', f'/api/subcategories/{self.grocery.pk}/', {'name': 'groceries'})
        self.assertEqual(clash.status_code, 400)
        self.assertIn('already has a subcategory called', clash.json()['name'][0])
        self.assertEqual(self.api('patch', f'/api/subcategories/{self.grocery.pk}/', {'category': self.home.pk}).status_code, 200)
        self.assertEqual(self.api('patch', f'/api/categories/{self.food.pk}/', {'name': 'Food & Dining'}).json()['name'], 'Food & Dining')
        self.assertEqual(self.api('patch', f'/api/categories/{self.food.pk}/', {'name': 'home'}).status_code, 400)

    def test_a_subcategory_with_a_job_keeps_its_kind(self):
        from finance.starter import subcategory_for
        principal = subcategory_for('loan_principal')
        renamed = self.api('patch', f'/api/subcategories/{principal.pk}/', {'name': 'Principal paid'})
        self.assertEqual((renamed.status_code, renamed.json()['role_label']), (200, 'loan principal'))
        moved = self.api('patch', f'/api/subcategories/{principal.pk}/', {'category': self.food.pk})
        self.assertEqual(moved.status_code, 400)
        self.assertIn('must stay in a Transfer category', moved.json()['category'][0])
        self.assertEqual(self.api('patch', f'/api/categories/{principal.category_id}/', {'type': 'EXP'}).status_code, 400)
        self.assertEqual(subcategory_for('loan_principal').name, 'Principal paid', 'found by its job after the rename')

    def test_merging_moves_everything(self):
        from finance.models import (Budget, LoanDetail, RecurringTransaction, StagedTransaction, SubCategory, TransactionRule,
                                    TransactionSplit)
        from finance.models import Budget as B
        B.objects.create(subcategory=self.groceries, year=2026, month=10, amount=Decimal('200'))
        tx = self.everything_using(self.grocery)
        used = self.client.get(f'/api/subcategories/{self.grocery.pk}/usage/').json()
        self.assertEqual({k: used[k] for k in ('transactions', 'split_lines', 'rules', 'budget_months', 'recurring', 'inbox_rows', 'loans')},
                         {'transactions': 1, 'split_lines': 1, 'rules': 1, 'budget_months': 2, 'recurring': 1, 'inbox_rows': 2, 'loans': 1})
        self.assertIsNotNone(used['cannot_leave_uncategorized'], 'split lines need a category')
        self.assertEqual(self.api('delete', f'/api/subcategories/{self.grocery.pk}/').status_code, 400, 'not without saying where')

        self.assertEqual(self.api('post', f'/api/subcategories/{self.grocery.pk}/remove/', {'replacement': self.groceries.pk}).status_code, 200)
        self.assertFalse(SubCategory.objects.filter(pk=self.grocery.pk).exists())
        tx.refresh_from_db()
        self.assertEqual(tx.subcategory_id, self.groceries.pk)
        self.assertEqual(TransactionSplit.objects.get().subcategory_id, self.groceries.pk)
        self.assertEqual(TransactionRule.objects.get().subcategory_id, self.groceries.pk)
        self.assertEqual(RecurringTransaction.objects.get().subcategory_id, self.groceries.pk)
        self.assertEqual(StagedTransaction.objects.get(fitid='A').subcategory_id, self.groceries.pk)
        self.assertEqual(StagedTransaction.objects.get(fitid='B').splits[0]['subcategory'], self.groceries.pk)
        self.assertEqual(LoanDetail.objects.get().pmi_category_id, self.groceries.pk)
        self.assertEqual(dict(Budget.objects.filter(subcategory=self.groceries).values_list('month', 'amount')),
                         {10: Decimal('300.00'), 11: Decimal('50.00')}, 'budgets added together')

    def test_leaving_it_uncategorized(self):
        from finance.models import Budget, RecurringTransaction, SubCategory, TransactionRule, TransactionSplit
        self.everything_using(self.grocery)
        refused = self.api('post', f'/api/subcategories/{self.grocery.pk}/remove/', {'replacement': None})
        self.assertIn('every split line needs a category', refused.json()['error'])
        TransactionSplit.objects.all().delete()
        from finance.models import StagedTransaction
        StagedTransaction.objects.filter(fitid='B').delete()
        self.assertEqual(self.api('post', f'/api/subcategories/{self.grocery.pk}/remove/', {'replacement': None}).status_code, 200)
        self.assertFalse(SubCategory.objects.filter(pk=self.grocery.pk).exists())
        self.assertEqual(Transaction.objects.get(fitid='T').subcategory_id, None)
        self.assertEqual(RecurringTransaction.objects.get().subcategory_id, None)
        self.assertFalse(TransactionRule.objects.exists() or Budget.objects.exists(), 'its rules and budgets go with it')

    def test_a_job_moves_to_the_replacement(self):
        from finance.models import SubCategory
        from finance.starter import subcategory_for
        tax = subcategory_for('sales_tax')
        mine = SubCategory.objects.create(name='Tax on purchases', category=tax.category)
        self.assertIn('choose where that should go', self.api('post', f'/api/subcategories/{tax.pk}/remove/', {}).json()['error'])
        self.assertEqual(self.api('post', f'/api/subcategories/{tax.pk}/remove/', {'replacement': mine.pk}).status_code, 200)
        self.assertEqual(subcategory_for('sales_tax').pk, mine.pk)
        principal = subcategory_for('loan_principal')
        wrong_kind = self.api('post', f'/api/subcategories/{principal.pk}/remove/', {'replacement': self.groceries.pk})
        self.assertIn('must stay in a Transfer category', wrong_kind.json()['error'])
        self.assertEqual(self.api('post', f'/api/subcategories/{principal.pk}/remove/', {'replacement': mine.pk}).status_code, 400,
                         'both have jobs')

    def test_deleting_a_category(self):
        from finance.models import Category, SubCategory
        snacks = SubCategory.objects.create(name='Snacks', category=self.home)
        same_name = SubCategory.objects.create(name='Grocery', category=self.home)
        self.everything_using(same_name)
        self.assertEqual(self.api('delete', f'/api/categories/{self.home.pk}/').status_code, 400)
        self.assertEqual(self.client.get(f'/api/categories/{self.home.pk}/usage/').json()['transactions'], 1)
        self.assertEqual(self.api('post', f'/api/categories/{self.home.pk}/remove/', {'move_to': self.food.pk}).status_code, 200)
        self.assertFalse(Category.objects.filter(pk=self.home.pk).exists())
        self.assertEqual(SubCategory.objects.get(pk=snacks.pk).category_id, self.food.pk, 'moved')
        self.assertFalse(SubCategory.objects.filter(pk=same_name.pk).exists(), 'merged into the Grocery already there')
        self.assertEqual(Transaction.objects.get(fitid='T').subcategory_id, self.grocery.pk)

        # Or everything to one replacement
        travel = Category.objects.create(name='Travel', type='EXP')
        hotel = SubCategory.objects.create(name='Hotel', category=travel)
        Transaction.objects.create(account=self.account, fitid='H', dtposted=date(2026, 10, 2), trnamt=Decimal('-90'), name='INN', status='C', subcategory=hotel)
        self.assertEqual(self.api('post', f'/api/categories/{travel.pk}/remove/', {'replacement': hotel.pk}).status_code, 400, 'not one inside it')
        self.assertEqual(self.api('post', f'/api/categories/{travel.pk}/remove/', {'replacement': self.groceries.pk}).status_code, 200)
        self.assertEqual(Transaction.objects.get(fitid='H').subcategory_id, self.groceries.pk)

    def test_nothing_disappears_by_accident(self):
        from django.db.models import ProtectedError
        self.everything_using(self.grocery)
        with self.assertRaises(ProtectedError):
            self.grocery.delete()


class SystemStatusTests(StorageTestCase):
    """Free disk space and whether a newer release is out; nothing is ever installed by the app."""

    def setUp(self):
        super().setUp()
        from django.core.cache import cache
        cache.delete('latest-release')
        self.addCleanup(cache.delete, 'latest-release')

    def test_low_disk_space(self):
        from collections import namedtuple
        usage = namedtuple('usage', 'total used free')
        gb = 1024 ** 3
        with mock.patch('finance.system.shutil.disk_usage', return_value=usage(100 * gb, 97 * gb, 3 * gb)):
            disk = self.client.get('/api/system/status/').json()['disk']
        self.assertTrue(disk['low'])
        self.assertIn('3.0 GB of disk space left', disk['message'])
        with mock.patch('finance.system.shutil.disk_usage', return_value=usage(100 * gb, 50 * gb, 50 * gb)):
            self.assertFalse(self.client.get('/api/system/status/').json()['disk']['low'])

    def test_no_check_until_a_repository_is_set(self):
        with mock.patch('finance.system.requests.get') as asked:
            update = self.client.get('/api/system/status/').json()['update']
        self.assertFalse(update['enabled'])
        asked.assert_not_called()

    def test_a_newer_release_and_the_command_to_paste(self):
        release = mock.Mock(status_code=200, json=lambda: {'tag_name': 'v1.5.0', 'html_url': 'https://github.com/o/r/releases/v1.5.0',
                                                           'body': 'Budget planning'})
        release.raise_for_status = lambda: None
        with override_settings(UPDATE_CHECK_REPO='o/r', APP_VERSION='1.4.0', INSTALL_DIR='/home/me/finance'), \
                mock.patch('finance.system.requests.get', return_value=release) as asked:
            update = self.client.get('/api/system/status/').json()['update']
            self.client.get('/api/system/status/')
        self.assertEqual((update['latest'], update['newer'], update['notes']), ('1.5.0', True, 'Budget planning'))
        self.assertEqual(update['command'], 'cd /home/me/finance && ./scripts/update.sh')
        self.assertEqual(asked.call_count, 1, 'once a day, not on every page')

        from django.core.cache import cache
        cache.delete('latest-release')
        with override_settings(UPDATE_CHECK_REPO='o/r', APP_VERSION='1.5.0'), mock.patch('finance.system.requests.get', return_value=release):
            self.assertFalse(self.client.get('/api/system/status/').json()['update']['newer'], 'already on it')

    def test_github_unreachable_and_members(self):
        import requests
        with override_settings(UPDATE_CHECK_REPO='o/r'), mock.patch('finance.system.requests.get', side_effect=requests.ConnectionError):
            update = self.client.get('/api/system/status/').json()['update']
        self.assertNotIn('newer', update, 'nothing claimed when it could not check')
        self.as_member()
        self.assertIsNone(self.client.get('/api/system/status/').json()['update'], 'updates are for admins')

    def test_admins_change_the_everyday_settings_and_restore_defaults(self):
        from collections import namedtuple
        from django.core.files.uploadedfile import SimpleUploadedFile
        base = self.client.get('/api/system/settings/').json()
        self.assertEqual(base['current'], base['defaults'])
        self.assertEqual(base['defaults']['update_checks'], True)

        saved = self.api('put', '/api/system/settings/', {'disk_warn_gb': 50, 'idle_minutes': 30, 'upload_max_mb': 2, 'update_checks': False})
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(saved.json()['current'], {'update_checks': False, 'disk_warn_gb': 50.0, 'idle_minutes': 30, 'upload_max_mb': 2})

        # Each takes effect at once
        usage = namedtuple('usage', 'total used free')
        with mock.patch('finance.system.shutil.disk_usage', return_value=usage(100 * 1024 ** 3, 60 * 1024 ** 3, 40 * 1024 ** 3)):
            self.assertTrue(self.client.get('/api/system/status/').json()['disk']['low'], '40 GB free is below 50')
        self.assertEqual(self.client.get('/api/me/').json()['idle_timeout_seconds'], 30 * 60)
        with mock.patch('finance.receipt_reader.available', return_value=True):
            big = self.client.post('/api/upload/', {'file': SimpleUploadedFile('big.qfx', b'x' * (1024 * 1024 + 1))})
        self.assertEqual(big.status_code, 201, 'the limit is now 2 MB (the test settings default is 1)')
        with override_settings(UPDATE_CHECK_REPO='o/r'), mock.patch('finance.system.requests.get') as asked:
            self.assertFalse(self.client.get('/api/system/status/').json()['update']['enabled'])
        asked.assert_not_called()

        # Out of range, unknown, wrong kind
        for body in ({'upload_max_mb': 500}, {'idle_minutes': -1}, {'colour': 'red'}, {'update_checks': 'yes'}, {'disk_warn_gb': 'lots'}):
            self.assertEqual(self.api('put', '/api/system/settings/', body).status_code, 400, body)

        # Restore defaults
        restored = self.api('put', '/api/system/settings/', {key: None for key in base['defaults']}).json()
        self.assertEqual(restored['current'], restored['defaults'])

        self.as_member()
        self.assertEqual(self.api('put', '/api/system/settings/', {'idle_minutes': 5}).status_code, 403, 'admins only')
        self.assertFalse(self.client.get('/api/system/settings/').json()['can_edit'])

    def test_settings_table_missing_falls_back_to_defaults(self):
        from django.db import ProgrammingError
        from finance import system
        system.forget_settings()
        with mock.patch('finance.models.SystemSettings.objects') as table:
            table.filter.side_effect = ProgrammingError('relation "finance_systemsettings" does not exist')
            self.assertEqual(system.current(), system.defaults())
            self.assertEqual(self.client.get('/api/me/').status_code, 200, 'the app still answers')

    def test_version_numbers(self):
        from finance.system import version_tuple
        self.assertEqual([version_tuple(v) for v in ('v1.4.0', '1.4', '1.4.0-3-gabc123', 'abc123', '')],
                         [(1, 4, 0), (1, 4, 0), (1, 4, 0), None, None])


class SavingsTests(StorageTestCase):
    """Savings is income less expenses; money moved between your own accounts isn't either."""

    def test_savings_on_the_budget_and_the_yearly_pages(self):
        from finance.models import Budget, Category, SubCategory
        sub = lambda kind, group, name: SubCategory.objects.create(name=name, category=Category.objects.get_or_create(name=group, type=kind)[0])
        salary, groceries = sub('INC', 'Income', 'Salary'), sub('EXP', 'Food & Dining', 'Groceries')
        named_savings = sub('EXP', 'Misc Expenses', 'Savings')     # just a name: no special meaning
        to_savings = sub('XFR', 'Transfers', 'To Savings')
        checking, savings = Account.objects.create(name='Checking'), Account.objects.create(name='Savings account')
        day = date(2025, 3, 10)
        add = lambda account, amount, subcategory, fitid: Transaction.objects.create(
            account=account, fitid=fitid, dtposted=day, trnamt=Decimal(amount), name=fitid, status='C', subcategory=subcategory)
        add(checking, '5000.00', salary, 'PAY')
        add(checking, '-1200.00', groceries, 'FOOD')
        add(checking, '-100.00', named_savings, 'ODD')
        add(checking, '-2000.00', to_savings, 'MOVE OUT')
        add(savings, '2000.00', to_savings, 'MOVE IN')
        Budget.objects.create(subcategory=salary, year=2025, month=3, amount=Decimal('5000'))
        Budget.objects.create(subcategory=groceries, year=2025, month=3, amount=Decimal('1000'))

        for url in ('/api/budgets/summary/?year=2025&month=3', '/api/budgets/summary/?year=2025&period=year'):
            summary = {key: Decimal(str(value)) for key, value in self.client.get(url).json()['summary'].items()}
            self.assertEqual(summary['income_actual'], Decimal('5000.00'), url)
            self.assertEqual(summary['expense_actual'], Decimal('-1300.00'), url)
            self.assertEqual(summary['savings_actual'], Decimal('3700.00'), f'{url}: the transfers are not counted')
            self.assertEqual(summary['savings_budget'], Decimal('4000.00'), url)
        rows = {row['name']: Decimal(str(row['actual'])) for row in self.client.get('/api/budgets/summary/?year=2025&period=year').json()['rows']}
        self.assertEqual(rows['Savings'], Decimal('-100.00'), 'a subcategory called Savings is like any other')


class BudgetYearToDateTests(StorageTestCase):
    """The Budget page's year view: this year up to this month, or all of a past year."""

    def test_the_year_so_far(self):
        from datetime import date as real_date
        from finance.models import Budget, Category, SubCategory

        class March15(real_date):
            @classmethod
            def today(cls):
                return cls(2026, 3, 15)

        food = SubCategory.objects.create(name='Groceries', category=Category.objects.create(name='Food', type='EXP'))
        account = Account.objects.create(name='Bank')
        for fitid, day in (('JAN', date(2026, 1, 5)), ('MAR', date(2026, 3, 20)), ('MAY', date(2026, 5, 1))):
            Transaction.objects.create(account=account, fitid=fitid, dtposted=day, trnamt=Decimal('-10'), name=fitid, status='P', subcategory=food)
        for month in (1, 2, 3, 4):
            Budget.objects.create(subcategory=food, year=2026, month=month, amount=Decimal('100'))
        with mock.patch('finance.views.date', March15):
            year = self.client.get('/api/budgets/summary/?year=2026&period=year').json()
            drill = self.client.get(f'/api/transactions/?subcategory={food.pk}&year=2026&until_month=3').json()
        self.assertEqual((year['first_month'], year['last_month']), (1, 3))
        self.assertEqual(Decimal(str(year['summary']['expense_actual'])), Decimal('-20.00'), "May's scheduled bill isn't counted yet")
        self.assertEqual(Decimal(str(year['summary']['expense_budget'])), Decimal('300.00'), 'three months of budget')
        self.assertEqual(sorted(row['description'] for row in drill), ['JAN', 'MAR'], 'the drill-down covers the same months')
        past = self.client.get('/api/budgets/summary/?year=2025&period=year').json()
        self.assertEqual((past['first_month'], past['last_month']), (1, 12))
        month = self.client.get('/api/budgets/summary/?year=2026&month=5').json()
        self.assertEqual(Decimal(str(month['summary']['expense_actual'])), Decimal('-10.00'))


class BudgetPlanningTests(StorageTestCase):
    """Next year's budget, set up from this year's budget or what was actually spent."""

    def setUp(self):
        super().setUp()
        from finance.models import Budget, Category, SubCategory
        self.food = SubCategory.objects.create(name='Groceries', category=Category.objects.create(name='Food', type='EXP'))
        self.pay = SubCategory.objects.create(name='Salary', category=Category.objects.create(name='Income', type='INC'))
        self.move = SubCategory.objects.create(name='To Savings', category=Category.objects.create(name='Transfers', type='XFR'))
        self.bank = Account.objects.create(name='Bank')
        Budget.objects.create(subcategory=self.food, year=2026, month=1, amount=Decimal('400'))
        Budget.objects.create(subcategory=self.food, year=2026, month=12, amount=Decimal('600'))
        Budget.objects.create(subcategory=self.pay, year=2026, month=1, amount=Decimal('5000'))
        add = lambda sub, day, amount: Transaction.objects.create(account=self.bank, fitid=f'{sub.pk}{day}{amount}', dtposted=day,
                                                                  trnamt=Decimal(amount), name='x', status='C', subcategory=sub)
        add(self.food, date(2026, 1, 10), '-300.00')
        add(self.food, date(2026, 2, 10), '-500.00')
        add(self.food, date(2025, 12, 10), '-700.00')       # last December, for the month not over yet
        add(self.pay, date(2026, 1, 31), '5000.00')
        add(self.move, date(2026, 1, 31), '-1000.00')       # a transfer: never budgeted

    def plan(self, **body):
        from finance import budget_planning
        return budget_planning.plan_year(2027, today=date(2026, 3, 15), **body)

    def budgets(self, sub):
        from finance.models import Budget
        return dict(Budget.objects.filter(subcategory=sub, year=2027).values_list('month', 'amount'))

    def test_from_this_years_budget(self):
        result = self.plan(source='budget', percent=Decimal('3'), rounding=10)
        self.assertEqual(self.budgets(self.food), {1: Decimal('410'), 12: Decimal('620')}, '+3%, to the nearest $10')
        self.assertEqual(self.budgets(self.pay), {1: Decimal('5150')})
        self.assertEqual((result['expense_budget'], result['income_budget'], result['months_set']), (Decimal('1030'), Decimal('5150'), 3))

    def test_from_actual_spending_month_by_month(self):
        self.plan(source='actual_monthly')
        # January and February 2026 are over; December comes from last December
        self.assertEqual(self.budgets(self.food), {1: Decimal('300.00'), 2: Decimal('500.00'), 12: Decimal('700.00')})
        self.assertEqual(self.budgets(self.move), {}, 'no budget for transfers')

    def test_from_the_average_month(self):
        self.plan(source='actual_average', kinds=['EXP'])
        food = self.budgets(self.food)
        self.assertEqual((len(food), food[1], food[12]), (12, Decimal('400.00'), Decimal('400.00')), '(300 + 500) / 2 each month')
        self.assertEqual(self.budgets(self.pay), {}, 'expenses only')

    def test_months_already_budgeted(self):
        from finance.models import Budget
        Budget.objects.create(subcategory=self.food, year=2027, month=1, amount=Decimal('999'))
        preview = self.plan(source='budget', save=False)
        self.assertEqual((preview['months_set'], preview['months_kept']), (2, 1))
        self.assertEqual(self.budgets(self.food), {1: Decimal('999')}, 'a preview changes nothing')
        self.plan(source='budget')
        self.assertEqual(self.budgets(self.food), {1: Decimal('999'), 12: Decimal('600')}, 'kept')
        self.plan(source='budget', replace=True)
        self.assertEqual(self.budgets(self.food), {1: Decimal('400'), 12: Decimal('600')}, 'replaced')

    def test_through_the_api_and_the_years_offered(self):
        response = self.api('post', '/api/budgets/plan/', {'year': 2027, 'source': 'budget', 'preview': True})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.api('post', '/api/budgets/plan/', {'year': 2027, 'source': 'guess'}).status_code, 400)
        years = self.client.get('/api/budgets/summary/?year=2026&month=1').json()['years']
        self.assertEqual(years[0], 2025, 'from the first year with transactions')
        self.assertIn(date.today().year + 1, years)


class SplitTransactionsInBudgetTests(StorageTestCase):
    """A split transaction counts toward each category by its split lines, everywhere the budget looks."""

    def setUp(self):
        super().setUp()
        from finance.models import Category, SubCategory, TransactionSplit
        food = Category.objects.create(name='Food', type='EXP')
        taxes = Category.objects.create(name='Taxes', type='EXP')
        self.dining = SubCategory.objects.create(name='Dining Out', category=food)
        self.groceries = SubCategory.objects.create(name='Groceries', category=food)
        self.sales_tax = SubCategory.objects.create(name='Sales Tax', category=taxes)
        account = Account.objects.create(name='Card')
        day = date(2026, 5, 25)
        # Split: 107.86 = 101.01 dining + 6.85 tax; its own category is still Dining Out
        self.split = Transaction.objects.create(account=account, fitid='S', dtposted=day, trnamt=Decimal('-107.86'), name='RESTAURANT', subcategory=self.dining, status='C')
        TransactionSplit.objects.create(transaction=self.split, subcategory=self.dining, amount=Decimal('-101.01'))
        TransactionSplit.objects.create(transaction=self.split, subcategory=self.sales_tax, amount=Decimal('-6.85'))
        # Split whose own category is Groceries, with a part in Dining Out
        self.mixed = Transaction.objects.create(account=account, fitid='M', dtposted=day, trnamt=Decimal('-50.00'), name='SUPERMARKET', subcategory=self.groceries, status='C')
        TransactionSplit.objects.create(transaction=self.mixed, subcategory=self.groceries, amount=Decimal('-40.00'))
        TransactionSplit.objects.create(transaction=self.mixed, subcategory=self.dining, amount=Decimal('-10.00'))
        # Plain transaction
        self.plain = Transaction.objects.create(account=account, fitid='P', dtposted=day, trnamt=Decimal('-20.00'), name='CAFE', subcategory=self.dining, status='C')

    def drill_down(self, subcategory):
        response = self.client.get(f'/api/transactions/?subcategory={subcategory.id}&year=2026&month=5')
        self.assertEqual(response.status_code, 200)
        return {row['description']: (row['subcategory_amount'], row['amount']) for row in response.json()}

    def budget_actual(self, subcategory):
        rows = self.client.get('/api/budgets/summary/?year=2026&month=5').json()['rows']
        return next(Decimal(str(row['actual'])) for row in rows if row['id'] == subcategory.id)

    def test_the_drill_down_shows_only_the_part_in_this_category(self):
        self.assertEqual(self.drill_down(self.dining), {
            'RESTAURANT': ('-101.01', '-107.86'),
            'SUPERMARKET': ('-10.00', '-50.00'),
            'CAFE': ('-20.00', '-20.00'),
        })
        self.assertEqual(self.drill_down(self.sales_tax), {'RESTAURANT': ('-6.85', '-107.86')})
        self.assertEqual(self.drill_down(self.groceries), {'SUPERMARKET': ('-40.00', '-50.00')})

    def test_the_drill_down_adds_up_to_the_budget_figure(self):
        for subcategory in (self.dining, self.groceries, self.sales_tax):
            shown = sum(Decimal(share) for share, _whole in self.drill_down(subcategory).values())
            self.assertEqual(shown, self.budget_actual(subcategory), subcategory.name)

    def test_a_split_transaction_is_counted_once_in_the_yearly_figures(self):
        rows = self.client.get('/api/budgets/summary/?year=2026&period=year').json()['rows']
        actual = {row['name']: Decimal(str(row['actual'])) for row in rows}
        self.assertEqual(actual['Dining Out'], Decimal('-131.01'))   # 101.01 + 10.00 + 20.00
        self.assertEqual(actual['Groceries'], Decimal('-40.00'))
        self.assertEqual(actual['Sales Tax'], Decimal('-6.85'))

    def test_an_unfiltered_list_has_no_share_and_stays_unchanged(self):
        rows = self.client.get('/api/transactions/?year=2026&month=5').json()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row['subcategory_amount'] is None for row in rows))
class PendingFilterTests(StorageTestCase):
    """The Transactions page can list only pending transactions, however far back they are."""

    def test_only_pending_transactions_are_listed_with_a_count(self):
        account = Account.objects.create(name='Card')
        for n in range(60):  # more than one page of cleared ones, newest first
            Transaction.objects.create(account=account, fitid=f'C{n}', dtposted=date(2026, 9, 1) + timedelta(days=n % 28), trnamt=Decimal('-1.00'), name=f'CLEARED {n}', status='C')
        Transaction.objects.create(account=account, fitid='OLD', dtposted=date(2025, 1, 5), trnamt=Decimal('-42.00'), name='OLD PENDING', status='P')
        Transaction.objects.create(account=account, fitid='NEW', dtposted=date(2026, 9, 30), trnamt=Decimal('-9.00'), name='NEW PENDING', status='P')
        page = self.client.get('/api/transactions/?page=1&status=P').json()
        self.assertEqual(page['count'], 2)
        self.assertEqual([row['description'] for row in page['results']], ['NEW PENDING', 'OLD PENDING'])
        self.assertEqual(self.client.get('/api/transactions/?page=1&status=P&search=old').json()['count'], 1, 'combines with the other filters')
        self.assertEqual(self.client.get('/api/transactions/?page=1').json()['count'], 62)


class TransactionStatusTests(StorageTestCase):
    """A transaction's status can be changed between pending, cleared and reconciled."""

    def test_changing_the_status(self):
        account = Account.objects.create(name='Bank')
        tx = Transaction.objects.create(account=account, fitid='S', dtposted=date(2026, 10, 20), trnamt=Decimal('-12.00'), name='Shop', status='P')
        for status in ['C', 'R', 'P']:
            response = self.api('patch', f'/api/transactions/{tx.id}/', {'status': status})
            self.assertEqual(response.status_code, 200, response.content)
            tx.refresh_from_db()
            self.assertEqual(tx.status, status)
        self.assertEqual(self.client.get('/api/transactions/?page=1&status=R').json()['count'], 0)

    def test_an_unknown_status_is_refused(self):
        tx = Transaction.objects.create(account=Account.objects.create(name='Bank'), fitid='S', dtposted=date(2026, 10, 20), trnamt=Decimal('-12.00'), name='Shop', status='P')
        self.assertEqual(self.api('patch', f'/api/transactions/{tx.id}/', {'status': 'X'}).status_code, 400)
        tx.refresh_from_db()
        self.assertEqual(tx.status, 'P')


class UncategorizedFilterTests(StorageTestCase):
    """The Transactions page can list the transactions that still need a category."""

    def test_only_transactions_without_a_category_split_or_transfer_are_listed(self):
        from finance.models import Category, SubCategory, TransactionSplit
        card, bank = Account.objects.create(name='Card'), Account.objects.create(name='Bank')
        sub = SubCategory.objects.create(name='Groceries', category=Category.objects.create(name='Food', type='EXP'))
        day = date(2026, 9, 20)
        make = lambda fitid, **extra: Transaction.objects.create(account=card, fitid=fitid, dtposted=day, trnamt=Decimal('-5.00'), name=fitid, status='C', **extra)
        make('NEEDS ONE')
        make('HAS ONE', subcategory=sub)
        split = make('SPLIT')
        TransactionSplit.objects.create(transaction=split, subcategory=sub, amount=Decimal('-5.00'))
        payment = make('CARD PAYMENT')
        transfer = Transaction.objects.create(account=bank, fitid='T', dtposted=day, trnamt=Decimal('5.00'), name='TRANSFER IN', status='C', linked_transaction=payment)
        Transaction.objects.filter(pk=payment.pk).update(linked_transaction=transfer)
        page = self.client.get('/api/transactions/?page=1&subcategory=none').json()
        self.assertEqual([row['description'] for row in page['results']], ['NEEDS ONE'])
        self.assertEqual(page['count'], 1)
        self.assertEqual(self.client.get(f'/api/transactions/?subcategory={sub.id}').status_code, 200, 'ids still work')
        self.assertEqual(self.client.get('/api/transactions/?subcategory=other').status_code, 400)


class ClearCategoryTests(StorageTestCase):
    """A transaction's category can be removed again, leaving it uncategorized."""

    def test_clearing_the_category(self):
        from finance.models import Category, SubCategory
        sub = SubCategory.objects.create(name='Pay', category=Category.objects.create(name='Income', type='INC'))
        tx = Transaction.objects.create(account=Account.objects.create(name='Bank'), fitid='P', dtposted=date(2026, 10, 14), trnamt=Decimal('6449.90'), name='Deposit', status='P', subcategory=sub)
        response = self.api('patch', f'/api/transactions/{tx.id}/', {'subcategory': None})
        self.assertEqual(response.status_code, 200, response.content)
        tx.refresh_from_db()
        self.assertIsNone(tx.subcategory)
        self.assertEqual([row['description'] for row in self.client.get('/api/transactions/?subcategory=none').json()], ['Deposit'])


class NearMatchTests(StorageTestCase):
    """
    Imported transactions are matched to ones entered by hand the way Quicken
    does it; something that is probably the same but differs is a near match,
    shown to the user to decide.
    """

    def setUp(self):
        super().setUp()
        from finance.models import Category, SubCategory
        self.card = Account.objects.create(name='Card')
        self.groceries = SubCategory.objects.create(name='Groceries', category=Category.objects.create(name='Food', type='EXP'))
        self.day = date(2026, 9, 20)

    def entry(self, amount, name='Trader Joes', days_before=0, **extra):
        return Transaction.objects.create(account=self.card, fitid='', dtposted=self.day - timedelta(days=days_before), trnamt=Decimal(amount),
                                          name=name, status='P', is_manual=True, subcategory=self.groceries, **extra)

    def match(self, amount, name="TRADER JOE'S #101", fitid='BANK-1'):
        from finance.services import LedgerMatcher
        matcher = LedgerMatcher([(self.card.pk, fitid, self.day)])
        match_type, proposal, reason = matcher.match(self.card.pk, fitid, self.day, Decimal(amount), name)
        return match_type, proposal.id if proposal else None, reason

    def stage(self, amount, name="TRADER JOE'S #101", fitid='BANK-1'):
        from finance.models import BankImport, StagedTransaction
        batch = BankImport.objects.create(filename='x.qfx', status='Success')
        match_type, proposal, reason = self.match(amount, name, fitid)
        return StagedTransaction.objects.create(batch=batch, account=self.card, fitid=fitid, date=self.day, amount=Decimal(amount), name=name,
                                                match_type=match_type, match_proposal_id=proposal, match_reason=reason)

    def test_payee_names_are_compared_by_their_identifying_words(self):
        from finance.services import similar_payee
        self.assertTrue(similar_payee("TRADER JOE'S #101", 'Trader Joes'))
        self.assertTrue(similar_payee('COSTCO WHSE #1234 ANYTOWN', 'Costco'))
        self.assertTrue(similar_payee('SQ *THE CORNER BAKERY US Anytown', 'Corner Bakery'))
        self.assertFalse(similar_payee('SQ *COFFEE SHOP', 'TST* TEA HOUSE'), 'noise words like SQ and TST do not count')
        self.assertFalse(similar_payee('AMAZON MKTPL', 'Costco'))

    def test_a_similar_payee_widens_the_window_for_an_exact_amount(self):
        entry = self.entry('-50.00', days_before=9)
        self.assertEqual(self.match('-50.00')[:2], ('MATCH', entry.id))
        self.assertEqual(self.match('-50.00', name='SOMEWHERE ELSE')[0], 'MATCH', '9 days after the entry is inside the 15-day window anyway')
        Transaction.objects.filter(pk=entry.pk).update(dtposted=self.day + timedelta(days=9))   # entry dated after the bank's
        self.assertEqual(self.match('-50.00')[0], 'MATCH')
        self.assertEqual(self.match('-50.00', name='SOMEWHERE ELSE')[0], 'NEAR')

    def test_a_different_amount_is_a_near_match_only_with_a_similar_payee(self):
        entry = self.entry('-50.00', days_before=2)
        match_type, proposal, reason = self.match('-58.00')
        self.assertEqual((match_type, proposal), ('NEAR', entry.id))
        self.assertIn('8.00', reason)
        self.assertEqual(self.match('-58.00', name='SHELL OIL')[0], 'NEW', 'more than 5% apart and a different payee')
        self.assertEqual(self.match('-70.00')[0], 'NEW', 'more than 25% apart')
        self.assertEqual(self.match('58.00')[0], 'NEW', 'a refund is not a purchase')

    def test_a_close_amount_is_a_near_match_whatever_the_payee(self):
        # Real cases: an ATM withdrawal entered as -99.95 that the bank calls a
        # cash advance of -100.00, and a transfer entered as -299 that was -300
        atm = self.entry('-99.95', name='ATM')
        match_type, proposal, reason = self.match('-100.00', name='CASH ADVANCE COSTCO #0100-AB1 ANYTOWN ST (Cash)')
        self.assertEqual((match_type, proposal), ('NEAR', atm.id))
        self.assertIn('0.05', reason)
        transfer = self.entry('-299.00', name='BROKERAGE TRANSFER', days_before=-1)
        self.assertEqual(self.match('-300.00', name='TRANSFERRED TO VS X00-000000 REDEMPTION (Cash)', fitid='BANK-2')[:2], ('NEAR', transfer.id))
        transfer.delete()
        self.assertEqual(self.match('-106.00', name='CASH ADVANCE', fitid='BANK-3')[0], 'NEW', 'more than 5% apart')
        Transaction.objects.filter(pk=atm.pk).update(dtposted=self.day - timedelta(days=6))
        self.assertEqual(self.match('-100.00', name='CASH ADVANCE', fitid='BANK-4')[0], 'NEW', 'more than 5 days apart')

    def test_an_entry_goes_to_the_row_it_fits_best_not_the_first_one_looked_at(self):
        # Seen in real data: a -95.00 cheque five days earlier is just within
        # the limits for the ATM entry, and was looked at first
        from finance.models import BankImport, StagedTransaction
        from finance.services import LedgerMatcher
        atm = self.entry('-99.95', name='ATM')
        rows = [(self.card.pk, 'CHK', self.day - timedelta(days=5), Decimal('-95.00'), 'Check Paid # 1204 (Cash)'),
                (self.card.pk, 'ADV', self.day, Decimal('-100.00'), 'CASH ADVANCE COSTCO #0100 (Cash)')]
        results = LedgerMatcher([row[:3] for row in rows]).match_all(rows)
        self.assertEqual([(match_type, proposal.id if proposal else None) for match_type, proposal, _reason in results],
                         [('NEW', None), ('NEAR', atm.id)])

        # The same rows already in the inbox with the worse pairing are re-weighed when it is opened
        batch = BankImport.objects.create(filename='x.qfx', status='Success')
        cheque = StagedTransaction.objects.create(batch=batch, account=self.card, fitid='CHK', date=rows[0][2], amount=rows[0][3], name=rows[0][4],
                                                  match_type='NEAR', match_proposal=atm, match_reason='old')
        advance = StagedTransaction.objects.create(batch=batch, account=self.card, fitid='ADV', date=rows[1][2], amount=rows[1][3], name=rows[1][4])
        self.client.get('/api/import/inbox/')
        cheque.refresh_from_db()
        advance.refresh_from_db()
        self.assertEqual((cheque.match_type, cheque.match_proposal_id), ('NEW', None))
        self.assertEqual((advance.match_type, advance.match_proposal_id), ('NEAR', atm.id))

    def test_the_strictest_rule_and_the_closest_entry_win(self):
        near = self.entry('-58.00', days_before=1)
        exact = self.entry('-60.00', name='Groceries run', days_before=2)
        self.assertEqual(self.match('-60.00')[:2], ('MATCH', exact.id), 'an exact amount beats a similar payee')
        self.assertNotEqual(near.id, exact.id)

    def test_a_bank_id_already_in_the_ledger_is_a_duplicate_not_a_near_match(self):
        Transaction.objects.create(account=self.card, fitid='BANK-1', dtposted=self.day, trnamt=Decimal('-58.00'), name='TRADER JOES', status='C')
        self.entry('-50.00', days_before=1)
        self.assertEqual(self.match('-58.00')[0], 'DUPE')

    def test_the_inbox_shows_both_sides_of_a_near_match(self):
        entry = self.entry('-50.00', days_before=2, personal_memo='weekly shop')
        staged = self.stage('-58.00')
        row = next(r for r in self.client.get('/api/import/inbox/').json() if r['id'] == staged.id)
        self.assertEqual(row['match_type'], 'NEAR')
        self.assertEqual(row['match_proposal_name'], 'Trader Joes')
        self.assertEqual(row['match_proposal_details'], {
            'id': entry.id, 'date': '2026-09-18', 'description': 'Trader Joes', 'amount': '-50.00',
            'subcategory': self.groceries.id, 'subcategory_name': 'Groceries', 'personal_memo': 'weekly shop', 'is_split': False, 'splits': [],
        })

    def test_matching_takes_the_banks_details_and_keeps_the_entrys_own(self):
        entry = self.entry('-50.00', days_before=2, personal_memo='weekly shop')
        staged = self.stage('-58.00')
        self.assertEqual(self.api('post', f'/api/import/inbox/{staged.id}/approve/').status_code, 200)
        entry.refresh_from_db()
        self.assertEqual((entry.trnamt, entry.dtposted, entry.fitid, entry.status), (Decimal('-58.00'), self.day, 'BANK-1', 'C'))
        self.assertEqual((entry.name, entry.subcategory, entry.personal_memo), ('Trader Joes', self.groceries, 'weekly shop'), 'the payee you typed is kept')
        self.assertEqual(entry.bank_memo, "TRADER JOE'S #101", "the bank's description is kept in the bank memo")
        self.assertEqual(Transaction.objects.count(), 1)

    def test_the_user_picks_which_payee_a_match_keeps(self):
        from finance.models import StagedTransaction
        # A full match keeps the payee you typed too, unless you pick the bank's
        entry = self.entry('-58.00', days_before=1)
        staged = self.stage('-58.00')
        self.assertEqual(StagedTransaction.objects.get(pk=staged.pk).match_type, 'MATCH')
        self.assertEqual(self.api('post', f'/api/import/inbox/{staged.id}/approve/').status_code, 200)
        entry.refresh_from_db()
        self.assertEqual((entry.name, entry.bank_memo), ('Trader Joes', "TRADER JOE'S #101"))

        other = self.entry('-30.00', name='Coffee', days_before=1)
        staged = self.stage('-30.00', name='STARBUCKS #1', fitid='BANK-2')
        self.assertEqual(self.api('post', f'/api/import/inbox/{staged.id}/approve/', {'payee': 'bank'}).status_code, 200)
        other.refresh_from_db()
        self.assertEqual(other.name, 'STARBUCKS #1')

        # Linked by hand: the same choice
        manual = self.entry('-12.00', name='Lunch', days_before=20)
        staged = self.stage('-12.50', name='DELI 42', fitid='BANK-3')
        self.assertEqual(self.api('post', f'/api/import/inbox/{staged.id}/approve/', {'manual_match_id': manual.id}).status_code, 200)
        manual.refresh_from_db()
        self.assertEqual((manual.name, manual.trnamt, manual.status), ('Lunch', Decimal('-12.50'), 'C'))
        staged = self.stage('-1.00', name='X', fitid='BANK-4')
        self.assertEqual(self.api('post', f'/api/import/inbox/{staged.id}/approve/', {'payee': 'nobody'}).status_code, 400)

    def test_not_the_same_adds_the_banks_transaction_and_leaves_the_entry(self):
        entry = self.entry('-50.00', days_before=2)
        staged = self.stage('-58.00')
        self.assertEqual(self.api('post', f'/api/import/inbox/{staged.id}/approve/', {'as_new': True}).status_code, 200)
        entry.refresh_from_db()
        self.assertEqual((entry.trnamt, entry.status, entry.fitid), (Decimal('-50.00'), 'P', ''))
        self.assertTrue(Transaction.objects.filter(fitid='BANK-1', trnamt=Decimal('-58.00'), status='C').exists())

    def test_a_split_entry_is_not_changed_to_an_amount_its_lines_do_not_add_up_to(self):
        from finance.models import StagedTransaction, TransactionSplit
        entry = self.entry('-50.00', days_before=2)
        TransactionSplit.objects.create(transaction=entry, subcategory=self.groceries, amount=Decimal('-50.00'))
        staged = self.stage('-58.00')
        response = self.api('post', f'/api/import/inbox/{staged.id}/approve/')
        self.assertEqual(response.status_code, 409)
        self.assertIn('split', response.json()['error'])
        entry.refresh_from_db()
        self.assertEqual((entry.trnamt, entry.status), (Decimal('-50.00'), 'P'))
        self.assertTrue(StagedTransaction.objects.filter(pk=staged.pk).exists(), 'it stays in the inbox')

    def test_the_importer_uses_the_same_rules(self):
        from pathlib import Path
        from finance.management.commands.stage_import import Command
        from finance.models import StagedTransaction
        Account.objects.filter(pk=self.card.pk).update(account_number='9999')
        exact = self.entry('-20.00', name='Gas', days_before=1)
        near = self.entry('-50.00', days_before=2)
        Transaction.objects.create(account=self.card, fitid='OLD-1', dtposted=self.day, trnamt=Decimal('-5.00'), name='OLD', status='C')

        def ofx_row(fitid, amount, name):
            return (f'<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260920120000<TRNAMT>{amount}<FITID>{fitid}<NAME>{name}</STMTTRN>')
        rows = ofx_row('B1', '-20.00', 'SHELL OIL') + ofx_row('B2', '-58.00', "TRADER JOE'S #101") + ofx_row('OLD-1', '-5.00', 'OLD') + ofx_row('B4', '-7.77', 'NEW PLACE')
        statement = ('OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\n\n<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>USD'
                     '<BANKACCTFROM><BANKID>1<ACCTID>9999<ACCTTYPE>CHECKING</BANKACCTFROM>'
                     f'<BANKTRANLIST><DTSTART>20260901<DTEND>20260930{rows}</BANKTRANLIST>'
                     '<LEDGERBAL><BALAMT>100.00<DTASOF>20260930</LEDGERBAL></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>')
        path = Path(self.dropzone)
        path.mkdir(exist_ok=True)
        (path / 'card.qfx').write_text(statement)
        archive = Path(self.archive) / 'qfx'
        command = Command()
        command.stdout = io.StringIO()
        command.rules = []
        command.process_file(path / 'card.qfx', archive)

        staged = {row.fitid: (row.match_type, row.match_proposal_id) for row in StagedTransaction.objects.all()}
        self.assertEqual(staged, {'B1': ('MATCH', exact.id), 'B2': ('NEAR', near.id), 'OLD-1': ('DUPE', None), 'B4': ('NEW', None)})
        self.assertIn('1 Near matches', command.stdout.getvalue())


class DuplicatesWithNewBankIdsTests(StorageTestCase):
    """
    Some banks issue new IDs when a statement is downloaded again (Fidelity
    did). Rows already imported are still recognised as duplicates by their
    amount, date and payee.
    """

    def setUp(self):
        super().setUp()
        self.card = Account.objects.create(name='CARD_ONE')
        self.day = date(2026, 10, 15)

    def imported(self, amount, name, days=0, fitid=None):
        return Transaction.objects.create(account=self.card, fitid=fitid or f'OLD-{name}-{amount}-{days}', dtposted=self.day + timedelta(days=days),
                                          trnamt=Decimal(amount), name=name, status='C')

    def results(self, *rows):
        from finance.services import LedgerMatcher
        rows = [(self.card.pk, fitid, self.day + timedelta(days=days), Decimal(amount), name) for fitid, amount, name, days in rows]
        return [match_type for match_type, _proposal, _reason in LedgerMatcher([row[:3] for row in rows]).match_all(rows)]

    def test_a_redownloaded_statement_with_new_ids_is_all_duplicates(self):
        self.imported('-104.63', "TRADER JOE'S")
        self.imported('-74.15', 'MACYS', days=1)
        self.imported('74.15', 'MACYS', days=2)
        self.assertEqual(self.results(
            ('NEW-1', '-104.63', "TRADER JOE'S", 0),
            ('NEW-2', '-74.15', 'MACYS', 1),
            ('NEW-3', '74.15', 'MACYS', 2),
            ('NEW-4', '-12.00', 'SOMEWHERE NEW', 2),
        ), ['DUPE', 'DUPE', 'DUPE', 'NEW'])

    def test_a_renamed_transaction_is_still_caught_on_the_same_date(self):
        self.imported('-104.63', 'Groceries run')       # renamed by a rule
        self.assertEqual(self.results(('NEW-1', '-104.63', "TRADER JOE'S #101", 0)), ['DUPE'])

    def test_a_shifted_date_needs_a_similar_payee(self):
        self.imported('-20.00', 'SHELL OIL', days=-2)
        self.assertEqual(self.results(('NEW-1', '-20.00', 'SHELL OIL 1001', 0)), ['DUPE'])
        self.assertEqual(self.results(('NEW-1', '-20.00', 'CVS PHARMACY', 0)), ['NEW'], 'two days apart and a different payee')
        self.assertEqual(self.results(('NEW-1', '-20.00', 'SHELL OIL 1001', 4)), ['NEW'], 'too far apart')

    def test_names_are_compared_as_the_renaming_rules_leave_them(self):
        from finance.models import BankImport, Category, StagedTransaction, SubCategory, TransactionRule
        sub = SubCategory.objects.create(name='Groceries', category=Category.objects.create(name='Food', type='EXP'))
        TransactionRule.objects.create(rule_name='TJ', clean_merchant_name='Weekly Shop', subcategory=sub,
                                       conditions=[{'field': 'name', 'operator': 'contains', 'value': 'trader joe'}])
        # Renamed in the ledger by the rule, and two days off, so neither the
        # date nor the words of the names agree
        self.imported('-104.63', 'Weekly Shop', days=-2)
        self.assertEqual(self.results(('NEW-1', '-104.63', "TRADER JOE'S #101", 0)), ['DUPE'])
        # The same in the inbox, where the row was staged before the rule existed
        batch = BankImport.objects.create(filename='x.qfx', status='Success')
        StagedTransaction.objects.create(batch=batch, account=self.card, fitid='NEW-2', date=self.day, amount=Decimal('-104.63'), name="TRADER JOE'S #101")
        self.assertEqual([row['match_type'] for row in self.client.get('/api/import/inbox/').json()], ['DUPE'])
        # The ledger's copy kept its old wording; the rule renames both sides alike
        Transaction.objects.all().delete()
        StagedTransaction.objects.all().delete()
        TransactionRule.objects.create(rule_name='TJ2', clean_merchant_name='Weekly Shop', subcategory=sub,
                                       conditions=[{'field': 'name', 'operator': 'contains', 'value': 'tjs'}])
        self.imported('-50.00', 'TJS MARKET 101', days=-2)
        self.assertEqual(self.results(('NEW-3', '-50.00', "TRADER JOE'S #101", 0)), ['DUPE'])
        self.assertEqual(self.results(('NEW-4', '-50.00', 'SHELL OIL', 0)), ['NEW'])

    def test_the_same_bank_memo_identifies_a_duplicate(self):
        tx = self.imported('-45.00', 'Drugstore', days=-2)   # renamed, and two days off
        Transaction.objects.filter(pk=tx.pk).update(bank_memo='REF 7731902')
        from finance.services import LedgerMatcher
        row = (self.card.pk, 'NEW-1', self.day, Decimal('-45.00'), 'CVS/PHARMACY #0100', 'REF 7731902')
        self.assertEqual(LedgerMatcher([row[:3]]).match_all([row])[0][0], 'DUPE')
        row = row[:5] + ('REF 9999999',)
        self.assertEqual(LedgerMatcher([row[:3]]).match_all([row])[0][0], 'NEW', 'a different memo, payee and date')
        # A generic memo, shared by unrelated transactions, proves nothing
        Transaction.objects.filter(pk=tx.pk).update(bank_memo='DIRECT DEBIT')
        row = row[:5] + ('DIRECT DEBIT',)
        self.assertEqual(LedgerMatcher([row[:3]]).match_all([row])[0][0], 'NEW')

    def test_one_imported_transaction_accounts_for_one_row_only(self):
        self.imported('-5.00', 'COFFEE SHOP')
        self.assertEqual(self.results(('A', '-5.00', 'COFFEE SHOP', 0), ('B', '-5.00', 'COFFEE SHOP', 0)), ['DUPE', 'NEW'])
        # A row caught by its own ID takes that transaction, so its twin is new
        self.imported('-6.00', 'TEA HOUSE', fitid='SAME-ID')
        self.assertEqual(self.results(('SAME-ID', '-6.00', 'TEA HOUSE', 0), ('OTHER', '-6.00', 'TEA HOUSE', 0)), ['DUPE', 'NEW'])

    def test_a_pending_entry_is_not_offered_to_a_row_that_was_already_imported(self):
        self.imported('-30.00', 'GAS STATION')
        Transaction.objects.create(account=self.card, fitid='', dtposted=self.day, trnamt=Decimal('-30.00'), name='Gas', status='P', is_manual=True)
        self.assertEqual(self.results(('NEW-1', '-30.00', 'GAS STATION', 0), ('NEW-2', '-30.00', 'GAS STATION', 0)), ['DUPE', 'MATCH'])

    def test_the_inbox_shows_why_and_stays_consistent_when_reopened(self):
        from finance.models import BankImport, StagedTransaction
        self.imported('-5.00', 'COFFEE SHOP')
        batch = BankImport.objects.create(filename='x.qfx', status='Success')
        for fitid in ('A', 'B'):
            StagedTransaction.objects.create(batch=batch, account=self.card, fitid=fitid, date=self.day, amount=Decimal('-5.00'), name='COFFEE SHOP')
        for _ in range(2):
            rows = sorted((r['match_type'], r['match_reason']) for r in self.client.get('/api/import/inbox/').json())
            self.assertEqual([t for t, _ in rows], ['DUPE', 'NEW'])
        self.assertIn('new ID', rows[0][1])


class StagedEditAndSplitTests(StorageTestCase):
    """An imported transaction can be renamed, categorised or split in the Import Inbox before it is approved."""

    def setUp(self):
        super().setUp()
        from finance.models import BankImport, Category, StagedTransaction, SubCategory
        food = Category.objects.create(name='Food', type='EXP')
        self.groceries = SubCategory.objects.create(name='Groceries', category=food)
        self.household = SubCategory.objects.create(name='Household', category=food)
        self.card = Account.objects.create(name='Card')
        batch = BankImport.objects.create(filename='x.qfx', status='Success')
        self.staged = StagedTransaction.objects.create(batch=batch, account=self.card, fitid='B1', date=date(2026, 10, 15),
                                                       amount=Decimal('-120.00'), name='COSTCO WHSE #100', bank_memo='POS')

    def edit(self, body):
        return self.api('patch', f'/api/import/inbox/{self.staged.id}/', body)

    def test_payee_category_and_memo_can_be_changed_but_not_the_banks_details(self):
        response = self.edit({'name': 'Costco', 'subcategory': self.groceries.id, 'personal_memo': 'monthly run',
                              'amount': '-1.00', 'date': '2020-01-01', 'fitid': 'X', 'match_type': 'DUPE'})
        self.assertEqual(response.status_code, 200, response.content)
        self.staged.refresh_from_db()
        self.assertEqual((self.staged.name, self.staged.subcategory, self.staged.personal_memo), ('Costco', self.groceries, 'monthly run'))
        self.assertEqual((self.staged.amount, self.staged.date, self.staged.fitid, self.staged.match_type), (Decimal('-120.00'), date(2026, 10, 15), 'B1', 'NEW'))
        self.assertEqual(response.json()['subcategory_name'], 'Groceries')

    def test_split_lines_must_add_up_and_become_the_transactions_splits(self):
        lines = lambda a, b: [{'subcategory': self.groceries.id, 'amount': a, 'memo': 'food'}, {'subcategory': self.household.id, 'amount': b}]
        self.assertEqual(self.edit({'splits': lines('-100.00', '-10.00')}).status_code, 400, 'adds up to -110')
        self.assertEqual(self.edit({'splits': lines('-100.00', '-20.00')[:1]}).status_code, 400, 'one line is not a split')
        response = self.edit({'splits': lines('-100.00', '-20.00')})
        self.assertEqual(response.status_code, 200, response.content)
        shown = self.client.get('/api/import/inbox/').json()[0]['splits']
        self.assertEqual([(line['subcategory_name'], line['amount']) for line in shown], [('Groceries', '-100.00'), ('Household', '-20.00')])

        self.assertEqual(self.api('post', f'/api/import/inbox/{self.staged.id}/approve/').status_code, 200)
        tx = Transaction.objects.get(fitid='B1')
        self.assertEqual(sorted((s.subcategory.name, s.amount, s.memo) for s in tx.splits.all()),
                         [('Groceries', Decimal('-100.00'), 'food'), ('Household', Decimal('-20.00'), '')])
        self.assertEqual(tx.trnamt, Decimal('-120.00'))

    def test_clearing_the_split(self):
        self.edit({'splits': [{'subcategory': self.groceries.id, 'amount': '-60.00'}, {'subcategory': self.household.id, 'amount': '-60.00'}]})
        self.assertEqual(self.edit({'splits': [], 'subcategory': self.groceries.id}).status_code, 200)
        self.api('post', f'/api/import/inbox/{self.staged.id}/approve/')
        tx = Transaction.objects.get(fitid='B1')
        self.assertEqual((tx.subcategory, tx.splits.count()), (self.groceries, 0))

    def test_editing_a_matched_row_updates_the_entry_when_approved(self):
        from finance.models import StagedTransaction, TransactionSplit
        entry = Transaction.objects.create(account=self.card, fitid='', dtposted=date(2026, 10, 14), trnamt=Decimal('-110.00'), name='Costco run',
                                           status='P', is_manual=True, subcategory=self.groceries, personal_memo='old memo')
        TransactionSplit.objects.create(transaction=entry, subcategory=self.groceries, amount=Decimal('-110.00'))
        StagedTransaction.objects.filter(pk=self.staged.pk).update(match_type='NEAR', match_proposal=entry)
        details = self.client.get('/api/import/inbox/').json()[0]['match_proposal_details']
        self.assertEqual((details['subcategory'], details['splits']), (self.groceries.id, [{'subcategory': self.groceries.id, 'amount': '-110.00', 'memo': ''}]),
                         'the edit window can start from the entry as it is')
        # Unedited, the split entry (-110) cannot take the bank's -120
        self.assertEqual(self.api('post', f'/api/import/inbox/{self.staged.id}/approve/').status_code, 409)
        # Re-split to the bank's amount in the inbox, then approve
        self.assertEqual(self.edit({'name': 'Costco', 'personal_memo': 'new memo', 'subcategory': None, 'splits': [
            {'subcategory': self.groceries.id, 'amount': '-100.00'}, {'subcategory': self.household.id, 'amount': '-20.00'}]}).status_code, 200)
        self.assertEqual(self.api('post', f'/api/import/inbox/{self.staged.id}/approve/').status_code, 200)
        entry.refresh_from_db()
        self.assertEqual((entry.name, entry.personal_memo, entry.trnamt, entry.status, entry.fitid), ('Costco', 'new memo', Decimal('-120.00'), 'C', 'B1'))
        self.assertEqual(sorted((s.subcategory.name, s.amount) for s in entry.splits.all()), [('Groceries', Decimal('-100.00')), ('Household', Decimal('-20.00'))])
        self.assertEqual(entry.bank_memo, 'POS')
        self.assertEqual(Transaction.objects.count(), 1)

    def test_renaming_keeps_the_banks_description(self):
        from finance.models import StagedTransaction
        StagedTransaction.objects.filter(pk=self.staged.pk).update(bank_memo='')
        self.edit({'name': 'Costco'})
        self.staged.refresh_from_db()
        self.assertEqual((self.staged.name, self.staged.bank_memo, self.staged.edited), ('Costco', 'COSTCO WHSE #100', True))


class RuleEngineTests(StorageTestCase):
    """One rule engine for import, preview and the Rules tab; cases taken from real rules that misbehaved."""

    def setUp(self):
        super().setUp()
        from finance.models import Category, SubCategory
        self.shop = SubCategory.objects.create(name='Household', category=Category.objects.create(name='Home', type='EXP'))
        self.card = Account.objects.create(name='CREDIT_CARD')

    def rule(self, name, *conditions, rename='', logic='AND', active=True):
        """Adds a rule at the bottom of the list, as written (no automatic placement)."""
        from finance.models import TransactionRule
        return TransactionRule.objects.create(
            rule_name=name, clean_merchant_name=rename, subcategory=self.shop, position=TransactionRule.objects.count() + 1,
            logic_operator=logic, is_active=active,
            conditions=[{'field': field, 'operator': operator, 'value': value} for field, operator, value in conditions])

    def names_in_order(self):
        return [row['rule_name'] for row in self.client.get('/api/transaction-rules/').json()]

    def renamed(self, name, amount='-10.00', memo=''):
        from finance.rules import active_rules, apply_rules
        return apply_rules(active_rules(), name, Decimal(amount), memo, self.card)['name']

    def test_the_list_order_decides_and_rules_can_be_moved(self):
        self.rule('AMAZON', ('name', 'contains', 'AMAZON'), rename='Amazon')
        prime = self.rule('AMAZON PRIME', ('name', 'contains', 'AMAZON PRIME'), rename='Amazon Prime')
        self.assertEqual(self.renamed('AMAZON PRIME*2K4'), 'Amazon', 'AMAZON is above it, so it wins')
        rows = {row['rule_name']: row for row in self.client.get('/api/transaction-rules/').json()}
        self.assertEqual(rows['AMAZON PRIME']['shadowed_by']['rule_name'], 'AMAZON')
        # The fix offered on the warning: move it above
        listed = self.api('post', f'/api/transaction-rules/{prime.id}/move/', {'before': rows['AMAZON']['id']})
        self.assertEqual([row['rule_name'] for row in listed.json()], ['AMAZON PRIME', 'AMAZON'], 'the move returns the new list')
        self.assertEqual(self.renamed('AMAZON PRIME*2K4'), 'Amazon Prime')
        self.assertEqual(self.renamed('AMAZON MKTPL*99'), 'Amazon')

    def test_moving_by_place_renumbers_the_whole_list(self):
        from finance.models import TransactionRule
        for name in 'ABCDE':
            self.rule(name, ('name', 'contains', name * 3))
        e = TransactionRule.objects.get(rule_name='E')
        self.api('post', f'/api/transaction-rules/{e.id}/move/', {'to': 2})
        self.assertEqual(self.names_in_order(), ['A', 'E', 'B', 'C', 'D'])
        self.api('post', f'/api/transaction-rules/{e.id}/move/', {'to': 99})
        self.assertEqual(self.names_in_order(), ['A', 'B', 'C', 'D', 'E'], 'past the end goes to the bottom')
        self.api('post', f'/api/transaction-rules/{e.id}/move/', {'to': -4})
        self.assertEqual(self.names_in_order(), ['E', 'A', 'B', 'C', 'D'])
        self.assertEqual(sorted(TransactionRule.objects.values_list('position', flat=True)), [1, 2, 3, 4, 5], 'no gaps')
        for bad in ({}, {'to': 'top'}):
            self.assertEqual(self.api('post', f'/api/transaction-rules/{e.id}/move/', bad).status_code, 400)
        self.assertEqual(self.api('post', f'/api/transaction-rules/{e.id}/move/', {'before': 9999}).status_code, 404)
        self.assertEqual(self.api('patch', f'/api/transaction-rules/{e.id}/', {'position': 50}).status_code, 200)
        self.assertEqual(TransactionRule.objects.get(pk=e.pk).position, 1, 'the place is not edited directly')

    def test_a_new_rule_is_placed_where_it_is_not_hidden(self):
        self.rule('Other', ('name', 'contains', 'SHELL'))
        self.rule('AMAZON', ('name', 'contains', 'AMAZON'), rename='Amazon')
        self.rule('Last', ('name', 'contains', 'ZZZ'))
        body = lambda name, value: {'rule_name': name, 'subcategory': self.shop.id, 'clean_merchant_name': name,
                                    'conditions': [{'field': 'name', 'operator': 'contains', 'value': value}]}
        self.assertEqual(self.api('post', '/api/transaction-rules/', body('AMAZON PRIME', 'amazon prime')).status_code, 201)
        self.assertEqual(self.names_in_order(), ['Other', 'AMAZON PRIME', 'AMAZON', 'Last'], 'just above the rule that would catch it')
        self.api('post', '/api/transaction-rules/', body('Unrelated', 'COSTCO'))
        self.assertEqual(self.names_in_order()[-1], 'Unrelated', 'nothing catches it: the bottom')
        # The same when a rule is made from the register
        self.api('post', '/api/transactions/create-rule/', body('AMAZON FRESH', 'AMAZON FRESH'))
        self.assertEqual(self.names_in_order()[:4], ['Other', 'AMAZON PRIME', 'AMAZON FRESH', 'AMAZON'])

    def test_upgrading_numbers_rules_in_the_order_they_were_tried(self):
        import importlib
        from django.apps import apps
        from finance.models import TransactionRule
        migration = importlib.import_module('finance.migrations.0034_rule_positions')
        make = lambda name, value, old_priority: TransactionRule.objects.create(
            rule_name=name, subcategory=self.shop, position=old_priority, conditions=[{'field': 'name', 'operator': 'contains', 'value': value}])
        make('TARGET.COM', 'TARGETCOM', 10)
        make('AMAZON', 'AMAZON', 0)
        make('AMAZON PRIME', 'AMAZON PRIME', 0)
        make('Two conditions', 'QUICKMART', 0).__class__.objects.filter(rule_name='Two conditions').update(
            conditions=[{'field': 'name', 'operator': 'contains', 'value': 'QUICKMART'}, {'field': 'name', 'operator': 'contains', 'value': 'GAS'}])
        migration.number_in_previous_order(apps, None)
        self.assertEqual(list(TransactionRule.objects.order_by('position').values_list('rule_name', 'position')),
                         [('Two conditions', 1), ('AMAZON PRIME', 2), ('AMAZON', 3), ('TARGET.COM', 4)])

    def test_text_ignores_case_spaces_and_html_escapes(self):
        self.rule('SALT & PEPPER', ('name', 'contains', 'SALT &amp; PEPPER'), rename='Salt & Pepper')
        self.rule('METRO', ('name', 'contains', 'METRO CENTRAL           ANYTOWN'), rename='METRO')
        self.assertEqual(self.renamed('salt & pepper 0100'), 'Salt & Pepper')
        self.assertEqual(self.renamed('METRO CENTRAL ANYTOWN ST'), 'METRO')

    def test_amounts_are_compared_by_size_whatever_the_sign(self):
        # A rule saying "amount exact 31.40" must match the stored -31.40
        self.rule('Insurer', ('name', 'contains', 'ACME'), ('amount', 'exact', '31.40'), rename='Life insurance')
        self.assertEqual(self.renamed('ACME INS P&C AUTOPAY', '-31.40'), 'Life insurance')
        self.assertEqual(self.renamed('ACME INS P&C AUTOPAY', '-150.00'), 'ACME INS P&C AUTOPAY')
        self.rule('Big', ('amount', 'gt', '$1,000'), rename='Big one')
        self.assertEqual(self.renamed('ANYTHING', '-1500.00'), 'Big one')

    def test_other_fields_and_or_rules(self):
        self.rule('Memo', ('personal_memo', 'contains', 'ref 77'), rename='By memo')
        self.rule('Either', ('name', 'startswith', 'ZZ'), ('name', 'endswith', 'QQ'), rename='Either', logic='OR')
        self.rule('Account', ('account', 'exact', 'credit_card'), ('name', 'contains', 'fee'), rename='Card fee')
        self.assertEqual(self.renamed('X', memo='REF 7731'), 'By memo')
        self.assertEqual(self.renamed('ZZ TOP'), 'Either')
        self.assertEqual(self.renamed('TOP QQ'), 'Either')
        self.assertEqual(self.renamed('ANNUAL FEE'), 'Card fee')
        self.rule('Off', ('name', 'contains', 'GONE'), rename='Never', active=False)
        self.assertEqual(self.renamed('GONE FISHING'), 'GONE FISHING', 'inactive rules do nothing')

    def test_preview_uses_the_same_rules_as_import(self):
        Transaction.objects.create(account=self.card, fitid='1', dtposted=date(2026, 9, 1), trnamt=Decimal('-31.40'), name='ACME  P&amp;C', status='C')
        Transaction.objects.create(account=self.card, fitid='2', dtposted=date(2026, 9, 2), trnamt=Decimal('-5.00'), name='Coffee', status='C', personal_memo='ref 77')
        preview = lambda *conds, logic='AND': [row['description'] for row in self.api('post', '/api/transactions/preview-rule/', {
            'logic_operator': logic, 'conditions': [{'field': f, 'operator': o, 'value': v} for f, o, v in conds]}).json()]
        self.assertEqual(preview(('name', 'contains', 'acme p&c'), ('amount', 'exact', '31.40')), ['ACME  P&amp;C'])
        self.assertEqual(preview(('personal_memo', 'contains', 'REF 77')), ['Coffee'])
        self.assertEqual(preview(('name', 'startswith', 'coff')), ['Coffee'])

    def test_use_is_counted_on_import(self):
        from pathlib import Path
        from finance.management.commands.stage_import import Command
        used = self.rule('Coffee', ('name', 'contains', 'COFFEE'), rename='Coffee')
        unused = self.rule('Tea', ('name', 'contains', 'TEA HOUSE'))
        Account.objects.filter(pk=self.card.pk).update(account_number='4242')
        rows = ''.join(f'<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>2026092{n}<TRNAMT>-{n}.00<FITID>C{n}<NAME>COFFEE SHOP {n}</STMTTRN>' for n in (1, 2))
        Path(self.dropzone).mkdir(exist_ok=True)
        path = Path(self.dropzone) / 'card.qfx'
        path.write_text('OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\n\n<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>USD<BANKACCTFROM><BANKID>1<ACCTID>4242<ACCTTYPE>CHECKING</BANKACCTFROM>'
                        f'<BANKTRANLIST><DTSTART>20260901<DTEND>20260930{rows}</BANKTRANLIST><LEDGERBAL><BALAMT>1.00<DTASOF>20260930</LEDGERBAL></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>')
        command = Command()
        command.stdout = io.StringIO()
        command.rules = __import__('finance.rules', fromlist=['active_rules']).active_rules()
        command.process_file(path, Path(self.archive) / 'qfx')
        used.refresh_from_db()
        unused.refresh_from_db()
        self.assertEqual((used.hit_count, unused.hit_count), (2, 0))
        self.assertIsNotNone(used.last_matched)

    def test_rules_tab_lists_the_order_and_flags_repeats_and_unreachable_rules(self):
        self.rule('DISNEY PLUS', ('name', 'contains', 'DISNEY PLUS'))
        self.rule('DISNEY PLUS again', ('name', 'contains', 'disney  plus'))                       # the same rule twice
        self.rule('TARGET', ('name', 'contains', 'TARGET'))
        self.rule('TARGET.COM', ('name', 'contains', 'TARGETCOM'))                                  # TARGET always catches it first
        self.rule('AMAZON PRIME', ('name', 'contains', 'AMAZON PRIME'))                              # fine: tried first
        self.rule('AMAZON', ('name', 'contains', 'AMAZON'))
        rows = {row['rule_name']: row for row in self.client.get('/api/transaction-rules/').json()}
        self.assertEqual(rows['DISNEY PLUS again']['duplicate_of']['rule_name'], 'DISNEY PLUS')
        self.assertIsNone(rows['DISNEY PLUS']['redundant_with'], 'only one of a pair is flagged')
        self.assertEqual(rows['TARGET.COM']['shadowed_by']['rule_name'], 'TARGET')
        self.assertIsNone(rows['AMAZON PRIME']['shadowed_by'])
        self.assertIsNone(rows['AMAZON']['shadowed_by'])
        self.assertLess(rows['AMAZON PRIME']['order'], rows['AMAZON']['order'])
        self.assertEqual(sorted(row['order'] for row in rows.values()), list(range(1, 7)))
        self.assertEqual(rows['AMAZON PRIME']['redundant_with']['rule_name'], 'AMAZON', 'neither renames, same category')

    def test_a_rule_a_broader_one_already_covers_is_redundant(self):
        specific = self.rule('WATERWORKS', ('name', 'contains', 'SPRINGFIELD WATERWORKS'), rename='Water bill')
        broader = self.rule('WATERWORK', ('name', 'contains', 'SPRINGFIELD WATERWORK'), rename='Water bill')
        different = self.rule('AMAZON PRIME', ('name', 'contains', 'AMAZON PRIME'), rename='Prime')
        self.rule('AMAZON', ('name', 'contains', 'AMAZON'), rename='Amazon')
        rows = {row['id']: row for row in self.client.get('/api/transaction-rules/').json()}
        self.assertEqual(rows[specific.id]['redundant_with']['rule_name'], 'WATERWORK')
        self.assertIsNone(rows[different.id]['redundant_with'], 'a different result is not redundant')
        # A rule in between that would catch it first means removing it changes things
        from finance.rules import move
        between = self.rule('SPRINGFIELD', ('name', 'contains', 'SPRINGFIELD WATER'), rename='Something else')
        move(between, before=broader.id)
        rows = {row['id']: row for row in self.client.get('/api/transaction-rules/').json()}
        self.assertIsNone(rows[specific.id]['redundant_with'])

    def test_bad_conditions_are_refused_in_settings_too(self):
        body = {'rule_name': 'Bad', 'subcategory': self.shop.id, 'conditions': [{'field': 'amount', 'operator': 'gt', 'value': 'lots'}]}
        response = self.api('post', '/api/transaction-rules/', body)
        self.assertEqual(response.status_code, 400)
        self.assertIn('needs a number', str(response.json()))
        body['conditions'] = [{'field': 'name', 'operator': 'contains', 'value': 'ok'}]
        self.assertEqual(self.api('post', '/api/transaction-rules/', body).status_code, 201)


class NewInstallTests(TestCase):
    """What a brand-new install starts with."""

    def test_a_new_ledger_gets_the_starter_categories_once(self):
        from finance.models import Category, SubCategory
        from finance.starter import STARTER_CATEGORIES, add_starter_categories
        with override_settings(STARTER_CATEGORIES=True):
            add_starter_categories()
            self.assertEqual(Category.objects.count(), len(STARTER_CATEGORIES))
            self.assertTrue(SubCategory.objects.filter(category__name='Taxes', name='Sales Tax').exists(), 'the tax split uses it')
            self.assertEqual(Category.objects.get(name='Transfers').type, 'XFR')
            add_starter_categories()
            self.assertEqual(Category.objects.count(), len(STARTER_CATEGORIES), 'never added twice')

    def test_the_categories_the_app_uses_itself_are_found_by_their_job(self):
        from finance import loans
        from finance.models import LoanDetail, SubCategory
        from finance.starter import ROLES, add_starter_categories, subcategory_for
        with override_settings(STARTER_CATEGORIES=True):
            add_starter_categories()
        self.assertEqual(SubCategory.objects.filter(role__isnull=False).count(), len(ROLES), 'every job has its subcategory')
        before = SubCategory.objects.count()
        mortgage = LoanDetail(loan_type='Mortgage')
        found = loans.categories_for(mortgage)
        self.assertEqual({key: f'{sub.category.name} / {sub.name}' for key, sub in found.items()}, {
            'interest': 'Home / Mortgage Interest', 'property_tax': 'Taxes / Property Tax', 'insurance': 'Home / Home Insurance',
            'pmi': 'Home / PMI', 'principal': 'Transfers / Loan Principal', 'escrow': 'Transfers / Escrow'})
        self.assertEqual(loans.categories_for(LoanDetail(loan_type='Auto'))['interest'].name, 'Auto Loan Interest')
        self.assertEqual(subcategory_for('sales_tax').name, 'Sales Tax')
        self.assertEqual(SubCategory.objects.count(), before, 'nothing made twice')

        # Renamed and moved: still found by its job
        pmi = SubCategory.objects.get(role='pmi')
        pmi.name, pmi.category = 'Mortgage Insurance', SubCategory.objects.get(name='Life Insurance').category
        pmi.save()
        self.assertEqual(loans.categories_for(mortgage)['pmi'].pk, pmi.pk)
        # Gone (nothing used it): made again where it belongs
        SubCategory.objects.filter(role='escrow').delete()
        self.assertEqual(f"{subcategory_for('escrow').category.name} / {subcategory_for('escrow').name}", 'Transfers / Escrow')
        self.assertEqual(SubCategory.objects.count(), before)

    def test_a_ledger_from_before_roles_gets_them_by_name(self):
        from finance.models import Category, SubCategory
        from finance.starter import subcategory_for
        mine = SubCategory.objects.create(name='property tax', category=Category.objects.create(name='House', type='EXP'))
        self.assertEqual(subcategory_for('property_tax').pk, mine.pk, 'the one already there takes the job')
        self.assertEqual(SubCategory.objects.get(pk=mine.pk).role, 'property_tax')

    def test_an_existing_ledger_is_never_touched(self):
        from finance.models import Category
        from finance.starter import add_starter_categories
        account = Account.objects.create(name='Bank')
        Transaction.objects.create(account=account, fitid='1', dtposted=date(2026, 1, 1), trnamt=Decimal('-1'), name='x', status='C')
        with override_settings(STARTER_CATEGORIES=True):
            add_starter_categories()   # categories deleted on purpose, but there is a ledger
        self.assertEqual(Category.objects.count(), 0)
        Transaction.objects.all().delete()
        with override_settings(STARTER_CATEGORIES=False):
            add_starter_categories()
        self.assertEqual(Category.objects.count(), 0, 'switched off')



class IdleSignOutTests(StorageTestCase):
    """Signed out after SESSION_IDLE_MINUTES without activity; background refreshes do not count."""

    def setUp(self):
        super().setUp()
        overrides = override_settings(SESSION_IDLE_MINUTES=10)
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.clock = 1_000_000.0
        patcher = mock.patch('finance.idle.time.time', side_effect=lambda: self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def wait(self, minutes):
        self.clock += minutes * 60

    def test_the_page_is_told_the_limit(self):
        self.assertEqual(self.client.get('/api/me/').json()['idle_timeout_seconds'], 600)
        with override_settings(SESSION_IDLE_MINUTES=0):
            self.assertEqual(self.client.get('/api/me/').json()['idle_timeout_seconds'], 0)

    def test_activity_keeps_the_session_and_idleness_ends_it(self):
        self.assertEqual(self.client.get('/api/accounts/').status_code, 200)
        for _ in range(4):                       # 36 minutes, never 10 idle in a row
            self.wait(9)
            self.assertEqual(self.client.get('/api/accounts/').status_code, 200)
        self.wait(11)
        response = self.client.get('/api/accounts/')
        self.assertEqual(response.status_code, 401)
        self.assertIn('10 minutes', response.json()['detail'])
        self.assertFalse(self.client.get('/api/me/').json()['authenticated'], 'the session is gone')

    def test_background_refreshes_do_not_count_as_activity(self):
        self.client.get('/api/accounts/')
        for _ in range(2):                       # 8 minutes of refreshes only
            self.wait(4)
            self.assertEqual(self.client.get('/api/backups/', HTTP_X_ACTIVITY='background').status_code, 200)
        self.wait(4)                             # 12 minutes since the last real activity
        self.assertEqual(self.client.get('/api/backups/', HTTP_X_ACTIVITY='background').status_code, 401)

    def test_the_session_check_after_a_long_break_reports_signed_out(self):
        self.client.get('/api/accounts/')
        self.wait(60)
        session = self.client.get('/api/me/')
        self.assertEqual(session.status_code, 200)
        self.assertFalse(session.json()['authenticated'])

    def test_signing_out_still_works_after_the_server_ended_the_session(self):
        self.client.get('/api/accounts/')
        self.wait(30)
        response = self.api('post', '/api/logout/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('redirect_url', response.json())

    def test_switched_off(self):
        with override_settings(SESSION_IDLE_MINUTES=0):
            self.client.get('/api/accounts/')
            self.wait(600)
            self.assertEqual(self.client.get('/api/accounts/').status_code, 200)

    def test_sign_in_asks_for_the_password_each_time(self):
        from config import settings as real
        self.assertEqual(real.OIDC_AUTH_REQUEST_EXTRA_PARAMS, {'prompt': 'login'})
        self.assertEqual(real.SESSION_IDLE_MINUTES, 10)
        self.assertIn('finance.idle.IdleTimeout', real.MIDDLEWARE)


class AccountForecastTests(StorageTestCase):
    """The Accounts card and the account's ledger agree on whether, and when, the balance goes below zero."""

    def setUp(self):
        super().setUp()
        self.today = date.today()
        self.account = Account.objects.create(name='Checking', account_type='CHECKING', last_balance=Decimal('100.00'),
                                              balance_date=self.today - timedelta(days=5))

    def add(self, amount, days_from_today, status='P', name='x'):
        return Transaction.objects.create(account=self.account, fitid=f'{name}{amount}{days_from_today}{status}', name=name,
                                          dtposted=self.today + timedelta(days=days_from_today), trnamt=Decimal(amount), status=status)

    def card(self):
        return next(a for a in self.client.get('/api/accounts/dashboard/').json()['checking'] if a['name'] == 'Checking')

    def ledger(self):
        return self.client.get(f'/api/accounts/{self.account.account_id}/ledger/').json()

    def test_a_balance_already_below_zero_is_a_shortfall_today(self):
        self.add('-150.00', -1)                      # pending, dated yesterday
        card = self.card()
        self.assertTrue(card['will_decrement'], 'used to show green with a negative "Low"')
        self.assertEqual(card['deficit_date'], self.today.isoformat())
        self.assertEqual(Decimal(str(card['current_balance'])), Decimal('-50.00'))

    def test_negative_now_but_a_deposit_coming_is_still_a_shortfall(self):
        self.add('-150.00', 0)
        self.add('500.00', 3)
        self.assertTrue(self.card()['will_decrement'])

    def test_a_future_dip_that_recovers_is_found_with_its_date(self):
        self.add('-300.00', 4, name='rent')
        self.add('1000.00', 6, name='pay')
        card = self.card()
        self.assertTrue(card['will_decrement'])
        self.assertEqual(card['deficit_date'], (self.today + timedelta(days=4)).isoformat())
        self.assertEqual(Decimal(str(card['projected_balance'])), Decimal('-200.00'))

    def test_a_cleared_dip_in_the_past_is_history_not_a_forecast(self):
        self.add('-150.00', -3, status='C')
        self.add('200.00', -2, status='C')
        card = self.card()
        self.assertFalse(card['will_decrement'])
        self.assertEqual(Decimal(str(card['current_balance'])), Decimal('150.00'))

    def test_healthy_account_and_the_card_matches_the_ledger(self):
        self.add('-40.00', -1, status='R')           # reconciled after the bank balance: counted by both now
        self.add('-20.00', 2)
        card, ledger = self.card(), self.ledger()
        self.assertFalse(card['will_decrement'])
        self.assertEqual(Decimal(str(card['projected_balance'])), Decimal('40.00'))
        self.assertEqual(Decimal(str(ledger[-1]['running_balance'])), Decimal('40.00'))
        self.assertEqual(Decimal(str(card['current_balance'])), Decimal(str(ledger[1]['running_balance'])))
        lowest_in_ledger = min(Decimal(str(row['running_balance'])) for row in ledger[1:])
        self.assertEqual(Decimal(str(card['projected_balance'])), lowest_in_ledger)


class ImportedBalanceTests(StorageTestCase):
    """The balance a bank file reports is kept as the bank gave it, and shown as verified."""

    def setUp(self):
        super().setUp()
        self.account = Account.objects.create(name='Card', account_number='7777')

    def import_file(self, balance, as_of, rows=''):
        from pathlib import Path
        from finance.management.commands.stage_import import Command
        statement = ('OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\n\n<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>USD'
                     '<BANKACCTFROM><BANKID>1<ACCTID>7777<ACCTTYPE>CHECKING</BANKACCTFROM>'
                     f'<BANKTRANLIST><DTSTART>20260901<DTEND>20261031{rows}</BANKTRANLIST>'
                     f'<LEDGERBAL><BALAMT>{balance}<DTASOF>{as_of}</LEDGERBAL></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>')
        Path(self.dropzone).mkdir(exist_ok=True)
        path = Path(self.dropzone) / f'card-{as_of}.qfx'
        path.write_text(statement)
        command = Command()
        command.stdout = io.StringIO()
        command.rules = []
        command.process_file(path, Path(self.archive) / 'qfx')
        self.account.refresh_from_db()

    def test_a_beginning_balance_file_gets_its_ending_balance_calculated(self):
        # Some banks report the balance at the START of the
        # download period; the transactions after it give the ending balance
        rows = ('<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20261005<TRNAMT>100.00<FITID>B1<NAME>DEPOSIT</STMTTRN>'
                '<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261020<TRNAMT>-25.00<FITID>B2<NAME>FEE</STMTTRN>')
        self.import_file('500.00', '20261001', rows)
        self.assertEqual((self.account.last_balance, self.account.balance_date, self.account.is_balance_calculated),
                         (Decimal('575.00'), date(2026, 10, 20), True))
        card = next(a for a in self.client.get('/api/accounts/dashboard/').json()['checking'] + self.client.get('/api/accounts/dashboard/').json()['other']
                    if a['name'] == 'Card')
        self.assertTrue(card['is_balance_calculated'], 'the Accounts page is told it was calculated')

    def test_an_ending_balance_file_is_kept_as_verified(self):
        rows = '<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261020<TRNAMT>-25.00<FITID>E1<NAME>FEE</STMTTRN>'
        self.import_file('475.00', '20261021', rows)
        self.assertEqual((self.account.last_balance, self.account.balance_date, self.account.is_balance_calculated),
                         (Decimal('475.00'), date(2026, 10, 21), False))

    def test_the_forecast_card_says_which_kind_of_balance_it_is(self):
        Account.objects.filter(pk=self.account.pk).update(account_type='CHECKING', last_balance=Decimal('10'), balance_date=date(2026, 10, 1), is_balance_calculated=True)
        card = next(a for a in self.client.get('/api/accounts/dashboard/').json()['checking'] if a['name'] == 'Card')
        self.assertTrue(card['is_balance_calculated'], 'it used to be left out, so every card said Verified')


class NetWorthTests(StorageTestCase):
    """Net worth at each month end, rebuilt backwards from each account's last bank balance."""

    def setUp(self):
        super().setUp()
        self.checking = Account.objects.create(name='Checking', account_type='CHECKING', last_balance=Decimal('1000.00'), balance_date=date(2026, 3, 15))
        self.card = Account.objects.create(name='Card', account_type='CREDIT CARD', last_balance=Decimal('-300.00'), balance_date=date(2026, 3, 10))

    def add(self, account, day, amount):
        Transaction.objects.create(account=account, fitid=f'{account.pk}{day}{amount}', dtposted=day, trnamt=Decimal(amount), name='x', status='C')

    def history(self, today=date(2026, 3, 20)):
        from finance.networth import history
        return history(today=today)

    def test_the_balance_is_what_the_bank_has_not_what_is_pending(self):
        from finance.networth import history
        today = date(2026, 11, 6)
        bank = Account.objects.create(name='Checking', account_type='CHECKING', last_balance=Decimal('1000.00'),
                                      balance_date=date(2026, 11, 3))
        add = lambda fitid, day, amount, status: Transaction.objects.create(
            account=bank, fitid=fitid, dtposted=day, trnamt=Decimal(amount), name=fitid, status=status)
        add('BEFORE', date(2026, 11, 1), '-20.00', 'C')            # already in the bank's balance
        add('CLEARED', date(2026, 11, 5), '-50.00', 'C')           # marked cleared since
        add('RENT DUE', date(2026, 11, 5), '-200.00', 'P')         # pending: not through the bank yet
        add('PAY', date(2026, 11, 6), '500.00', 'P')
        add('NEXT WEEK', date(2026, 11, 13), '-75.00', 'P')        # scheduled
        points = {p['date']: p for p in history(today=today, start=date(2026, 10, 31))['points']}
        self.assertEqual(points[today]['accounts'][str(bank.pk)], Decimal('950.00'), "the bank's 1000, less the 50 marked cleared")
        self.assertEqual(points[date(2026, 11, 2)]['accounts'][str(bank.pk)], Decimal('1000.00'))
        self.assertEqual(Transaction.objects.get(fitid='BEFORE').in_balance_as_of, date(2026, 11, 3), 'dated before the balance: in it')

        # A card purchase made on the 2nd that the bank posted after its balance of
        # the 3rd: dated before the balance, but not in it, so it is added
        late = add('POSTED LATE', date(2026, 11, 2), '-30.00', 'C')
        Transaction.objects.filter(pk=late.pk).update(in_balance_as_of=None)
        points = {p['date']: p for p in history(today=today, start=date(2026, 11, 1))['points']}
        self.assertEqual(points[today]['accounts'][str(bank.pk)], Decimal('920.00'))
        self.assertEqual(points[date(2026, 11, 2)]['accounts'][str(bank.pk)], Decimal('970.00'), 'from the day it was made')
        from finance.balances import balance_on
        self.assertEqual(balance_on(bank, today), Decimal('920.00'), 'loans and the rest work it out the same way')

    def test_balances_are_worked_back_from_the_bank_balance(self):
        self.add(self.checking, date(2026, 1, 10), '500.00')
        self.add(self.checking, date(2026, 2, 5), '-200.00')
        self.add(self.checking, date(2026, 3, 1), '300.00')
        self.add(self.checking, date(2026, 3, 18), '-50.00')      # after the bank balance: added on
        self.add(self.card, date(2026, 2, 20), '-100.00')
        points = {p['date']: p for p in self.history()['points']}
        self.assertEqual((min(points), max(points), len(points)), (date(2026, 1, 10), date(2026, 3, 20), 70), 'a short range: every day')
        checking = str(self.checking.pk)
        # 1000 on 15 Mar; the 300 on 1 Mar and -200 on 5 Feb came after the earlier month ends
        self.assertEqual(points[date(2026, 1, 31)]['accounts'][checking], Decimal('900.00'))
        self.assertEqual(points[date(2026, 2, 28)]['accounts'][checking], Decimal('700.00'))
        self.assertEqual(points[date(2026, 3, 20)]['accounts'][checking], Decimal('950.00'))
        self.assertNotIn(str(self.card.pk), points[date(2026, 1, 31)]['accounts'], 'not counted before its first transaction')
        self.assertEqual(points[date(2026, 2, 28)]['accounts'][str(self.card.pk)], Decimal('-300.00'))
        today = points[date(2026, 3, 20)]
        self.assertEqual((today['assets'], today['liabilities'], today['net_worth']), (Decimal('950.00'), Decimal('-300.00'), Decimal('650.00')))

    def test_scheduled_transactions_and_closed_accounts(self):
        self.add(self.checking, date(2026, 3, 1), '10.00')
        self.add(self.checking, date(2026, 4, 1), '-999.00')      # future: a forecast, not net worth
        Account.objects.filter(pk=self.card.pk).update(closed_date=date(2026, 3, 12))
        self.add(self.card, date(2026, 3, 5), '-5.00')
        last = self.history()['points'][-1]
        self.assertEqual(last['accounts'][str(self.checking.pk)], Decimal('1000.00'))
        self.assertNotIn(str(self.card.pk), last['accounts'], 'closed before today')

    def test_debts_stored_as_positive_numbers_are_flipped(self):
        loan = Account.objects.create(name='Loan', account_type='LOAN', last_balance=Decimal('5000.00'), balance_date=date(2026, 3, 1), invert_sign=True)
        self.add(loan, date(2026, 2, 1), '-100.00')
        last = self.history()['points'][-1]
        self.assertEqual(last['accounts'][str(loan.pk)], Decimal('-5000.00'))

    def test_ranges_long_and_short(self):
        from finance.networth import history
        self.add(self.checking, date(2023, 6, 1), '5.00')
        long = history(today=date(2026, 3, 20), start=date(2025, 3, 20))['points']
        self.assertEqual(long[0]['date'], date(2025, 3, 20))
        self.assertEqual(long[1]['date'], date(2025, 3, 31), 'then month ends')
        self.assertEqual(long[-1]['date'], date(2026, 3, 20))
        self.assertEqual(len(long), 14)
        five_days = history(today=date(2026, 3, 20), start=date(2026, 3, 15))['points']
        self.assertEqual([p['date'].day for p in five_days], [15, 16, 17, 18, 19, 20])
        custom = history(today=date(2026, 3, 20), start=date(2024, 1, 1), end=date(2024, 12, 31))
        self.assertEqual((custom['start'], custom['end'], custom['points'][-1]['date']), (date(2024, 1, 1), date(2024, 12, 31), date(2024, 12, 31)))
        self.assertEqual(history(today=date(2026, 3, 20), end=date(2030, 1, 1))['end'], date(2026, 3, 20), 'never past today')

    def test_accounts_are_grouped_checking_savings_investments_cards_loans(self):
        from finance.networth import history
        from finance.models import AssetDetail, LoanDetail
        savings = Account.objects.create(name='Savings', account_type='SAVINGS', last_balance=Decimal('5'), balance_date=date(2026, 3, 1))
        brokerage = Account.objects.create(name='Brokerage', account_type='INVESTMENT')
        AssetDetail.objects.create(account=brokerage, kind='INVESTMENT', purchase_price=Decimal('1000'))
        car = Account.objects.create(name='Car', account_type='VEHICLE')
        AssetDetail.objects.create(account=car, kind='VEHICLE', purchase_price=Decimal('20000'))   # no dates: counted from today
        loan = Account.objects.create(name='Car loan', account_type='AUTO LOAN', is_liability=True,
                                      last_balance=Decimal('-15000'), balance_date=date(2026, 1, 1))
        LoanDetail.objects.create(liability_account=loan, loan_type='Auto', original_balance=Decimal('15000'), interest_rate=Decimal('5'),
                                  term_months=60, first_payment_date=date(2026, 1, 1))
        result = history(today=date(2026, 3, 20))
        self.assertEqual([(a['name'], a['group']) for a in result['accounts']], [
            ('Checking', 'checking'), ('Savings', 'savings'), ('Brokerage', 'investments'), ('Car', 'property'),
            ('Card', 'credit_cards'), ('Car loan', 'loans')])
        today = result['points'][-1]['accounts']
        self.assertEqual(today[str(car.pk)], Decimal('20000.00'), 'an undated vehicle still counts today')
        fresh = {a['name']: (a['as_of_label'], a['as_of']) for a in result['accounts']}
        self.assertEqual(fresh['Checking'], ('Verified', date(2026, 3, 15)))
        self.assertEqual(fresh['Car'], ('Purchase price', None))
        self.assertEqual(fresh['Car loan'], ('Updated', date(2026, 1, 1)))
        Account.objects.filter(pk=self.checking.pk).update(is_balance_calculated=True)
        self.assertEqual({a['name']: a['as_of_label'] for a in history(today=date(2026, 3, 20))['accounts']}['Checking'], 'Calculated')
        self.assertNotIn(str(car.pk), result['points'][0]['accounts'], 'but not in the past')

    def test_the_page_gets_it_from_the_api(self):
        self.add(self.checking, date(2026, 3, 1), '10.00')
        body = self.client.get('/api/net-worth/').json()
        self.assertEqual({a['name'] for a in body['accounts']}, {'Checking', 'Card'})
        self.assertIn('net_worth', body['points'][-1])
        self.assertEqual(self.client.get('/api/net-worth/?start=2026-03-01&end=2026-03-05').json()['points'][0]['date'], '2026-03-01')
        self.assertEqual(self.client.get('/api/net-worth/?start=soon').status_code, 400)
        self.assertEqual(self.client.get('/api/net-worth/?start=2026-03-05&end=2026-03-01').status_code, 400)
        self.as_member()
        self.assertEqual(self.client.get('/api/net-worth/').status_code, 200)


class AssetsAndLoansTests(StorageTestCase):
    """Homes, vehicles and loans: their details, values over time, and their place in net worth."""

    def create(self, body):
        response = self.api('post', '/api/holdings/', body)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def mortgage_body(self, **loan):
        return {'name': 'Mortgage', 'kind': 'mortgage', 'loan': {
            'lender': 'Big Bank', 'original_balance': '300000.00', 'interest_rate': '6.500', 'term_months': 360,
            'origination_date': '2026-01-15', 'first_payment_date': '2026-03-01', 'property_tax_amount': '400.00',
            'homeowners_insurance_amount': '150.00', **loan}}

    def test_loan_payments_match_the_standard_formula(self):
        from finance import loans
        from finance.models import LoanDetail
        home = LoanDetail(original_balance=Decimal('300000'), interest_rate=Decimal('6.5'), term_months=360,
                          first_payment_date=date(2026, 3, 1), payment_schedule='MONTHLY')
        car = LoanDetail(original_balance=Decimal('30000'), interest_rate=Decimal('6'), term_months=60,
                         first_payment_date=date(2026, 3, 1), payment_schedule='MONTHLY')
        self.assertEqual((loans.scheduled_payment(home), loans.scheduled_payment(car)), (Decimal('1896.20'), Decimal('579.98')))
        self.assertEqual(loans.scheduled_balance(car, date(2026, 2, 28)), Decimal('30000.00'), 'nothing paid before the first payment')
        self.assertEqual(loans.scheduled_balance(car, date(2026, 3, 1)), Decimal('29570.02'), '30000 - (579.98 - 150.00 interest)')
        self.assertEqual(loans.scheduled_balance(car, date(2031, 6, 1)), Decimal('0.00'))
        self.assertEqual(loans.payoff_date(car), date(2031, 2, 1), '60 payments from March 2026')
        car.default_extra_principal = Decimal('200')
        self.assertLess(loans.payoff_date(car), date(2031, 2, 1), 'extra principal pays it off sooner')

    def test_a_home_with_its_mortgage(self):
        home = self.create({'name': 'Our House', 'kind': 'home', 'asset': {
            'purchase_date': '2026-01-15', 'purchase_price': '400000.00', 'address': '1 Main St', 'property_type': 'Single family',
            'year_built': 1998, 'square_feet': 2100}})
        mortgage = self.create(self.mortgage_body(asset_account=home['id']))
        self.assertEqual((mortgage['kind'], mortgage['loan']['scheduled_payment'], mortgage['loan']['lender']), ('mortgage', '1896.20', 'Big Bank'))
        self.assertEqual(mortgage['loan']['asset_account'], home['id'])

        # A value from Zillow, and the balance from a statement
        self.api('post', f"/api/holdings/{home['id']}/values/", {'date': '2026-06-01', 'amount': '425000.00', 'source': 'Zillow estimate'})
        from finance.networth import history
        may = {p['date']: p for p in history(today=date(2026, 7, 15))['points']}[date(2026, 5, 31)]
        self.assertEqual(may['accounts'][str(home['id'])], Decimal('400000.00'), 'purchase price until a value is recorded')
        self.assertEqual(may['accounts'][str(mortgage['id'])], Decimal('-300000.00'), 'no payments recorded: still the amount borrowed')
        self.api('post', f"/api/holdings/{mortgage['id']}/values/", {'date': '2026-06-01', 'amount': '297500.00', 'source': 'June statement'})
        july = history(today=date(2026, 7, 15))['points'][-1]
        self.assertEqual(july['accounts'][str(home['id'])], Decimal('425000.00'))
        self.assertEqual(july['accounts'][str(mortgage['id'])], Decimal('-297500.00'), 'the statement balance is the new starting point')
        self.assertEqual(july['net_worth'], Decimal('127500.00'))
        # The months before that statement still count the loan, from its schedule
        from finance import loans
        from finance.models import LoanDetail
        may = {p['date']: p for p in history(today=date(2026, 7, 15))['points']}[date(2026, 5, 31)]
        scheduled = loans.scheduled_balance(LoanDetail.objects.get(liability_account_id=mortgage['id']), date(2026, 5, 31))
        self.assertLess(scheduled, Decimal('300000.00'), 'March to May payments are on the schedule')
        self.assertEqual(may['accounts'][str(mortgage['id'])], -scheduled)
        self.assertEqual(history(today=date(2026, 7, 15))['points'][0]['date'], date(2026, 1, 15), 'from the day it began')

    def test_equity_in_a_home_over_the_life_of_its_mortgage(self):
        from finance import loans
        from finance.models import Account
        home = self.create({'name': 'Our House', 'kind': 'home', 'asset': {'purchase_date': '2026-01-15', 'purchase_price': '400000.00'}})
        mortgage = self.create(self.mortgage_body(asset_account=home['id']))
        self.api('post', f"/api/holdings/{home['id']}/values/", {'date': '2026-06-01', 'amount': '425000.00'})
        self.api('post', f"/api/holdings/{mortgage['id']}/values/", {'date': '2026-06-01', 'amount': '297500.00'})
        result = loans.equity_history(Account.objects.get(pk=mortgage['id']), today=date(2026, 7, 15))
        points = {p['date']: p for p in result['points']}

        self.assertEqual(result['points'][0]['date'], date(2026, 1, 31), 'from the month the loan began')
        self.assertEqual(points[date(2026, 3, 31)]['source'], 'schedule', 'before the first known balance')
        self.assertEqual(points[date(2026, 3, 31)]['value'], Decimal('400000.00'), 'the purchase price until a value is recorded')
        today = result['today']
        self.assertEqual((today['date'], today['source']), (date(2026, 7, 15), 'actual'))
        self.assertEqual((today['value'], today['owed'], today['equity']), (Decimal('425000.00'), Decimal('297500.00'), Decimal('127500.00')))

        ahead = [p for p in result['points'] if p['date'] > date(2026, 7, 15)]
        self.assertTrue(all(p['source'] == 'projected' and p['value'] == Decimal('425000.00') for p in ahead), 'the latest value, held')
        self.assertEqual(ahead[0]['owed'], today['owed'], 'July 31: the next payment is August 1')
        self.assertLess(ahead[1]['owed'], today['owed'])
        self.assertEqual((ahead[-1]['owed'], ahead[-1]['equity']), (Decimal('0.00'), Decimal('425000.00')), 'paid off: all equity')
        self.assertEqual(ahead[-1]['date'], result['payoff_date'])

        # From either the loan or the home; a loan with nothing linked says how to link it
        self.assertEqual(self.client.get(f"/api/holdings/{home['id']}/equity/").json()['loan']['id'], mortgage['id'])
        self.assertEqual(self.client.get(f"/api/holdings/{mortgage['id']}/equity/").json()['asset']['id'], home['id'])
        loose = self.create({'name': 'Loan', 'kind': 'loan', 'loan': {
            'original_balance': '5000.00', 'interest_rate': '5.000', 'term_months': 24, 'first_payment_date': '2026-02-01'}})
        self.assertEqual(self.client.get(f"/api/holdings/{loose['id']}/equity/").status_code, 400)

    def test_a_vehicle_owned_jointly_and_a_car_loan(self):
        car = self.create({'name': 'Camry', 'kind': 'vehicle', 'asset': {
            'vehicle_year': 2024, 'make': 'Acme', 'model': 'Camry', 'trim': 'XSE', 'vin': '4T1K61AK0RU000000',
            'mileage': 15000, 'purchase_date': '2026-01-01', 'purchase_price': '32000.00', 'ownership_percent': '50.00'}})
        self.create({'name': 'Car loan', 'kind': 'auto_loan', 'loan': {
            'original_balance': '30000.00', 'interest_rate': '6.000', 'term_months': 60, 'first_payment_date': '2026-02-01',
            'asset_account': car['id']}})
        from finance.networth import history
        last = history(today=date(2026, 1, 20))['points'][-1]
        self.assertEqual(last['accounts'][str(car['id'])], Decimal('16000.00'), 'half of it is yours')

    def test_a_failed_save_leaves_nothing_behind(self):
        # Seen in dev: the account was created but its details failed, leaving an empty account per attempt
        with mock.patch('finance.serializers.AssetDetail.objects.update_or_create', side_effect=RuntimeError('no such table')):
            with self.assertRaises(RuntimeError):
                self.api('post', '/api/holdings/', {'name': 'Family hatchback', 'kind': 'vehicle', 'asset': {'make': 'Acme'}})
        self.assertFalse(Account.objects.filter(name='Family hatchback').exists())

    def test_kept_out_of_the_bank_account_lists(self):
        Account.objects.create(name='Checking', account_type='CHECKING', last_balance=Decimal('10'), balance_date=date(2026, 1, 1))
        self.create({'name': 'House', 'kind': 'home', 'asset': {'purchase_price': '1.00'}})
        self.create(self.mortgage_body())
        self.assertEqual([a['name'] for a in self.client.get('/api/accounts/').json()], ['Checking'])
        dashboard = self.client.get('/api/accounts/dashboard/').json()
        self.assertEqual([a['name'] for a in dashboard['checking'] + dashboard['other']], ['Checking'])
        self.assertEqual(sorted(h['name'] for h in self.client.get('/api/holdings/').json()), ['House', 'Mortgage'])

    def test_editing_values_and_validation(self):
        house = self.create({'name': 'House', 'kind': 'home', 'asset': {'purchase_price': '100.00'}})
        url = f"/api/holdings/{house['id']}/"
        self.api('post', url + 'values/', {'date': '2026-06-01', 'amount': '200.00'})
        updated = self.api('post', url + 'values/', {'date': '2026-06-01', 'amount': '250.00', 'source': 'appraisal'}).json()
        self.assertEqual([(v['amount'], v['source']) for v in updated['values']], [('250.00', 'appraisal')], 'same date replaces')
        self.assertEqual(self.api('post', url + 'values/', {'date': '2026-07-01', 'amount': '-5'}).status_code, 400)
        entry = updated['values'][0]['id']
        self.assertEqual(self.client.delete(url + f'values/{entry}/').json()['values'], [])

        renamed = self.api('patch', url, {'name': 'Home', 'asset': {'address': '2 Oak Ave'}}).json()
        self.assertEqual((renamed['name'], renamed['asset']['address'], renamed['asset']['purchase_price']), ('Home', '2 Oak Ave', '100.00'))
        self.assertEqual(self.api('patch', url, {'kind': 'mortgage'}).status_code, 400, 'an asset cannot become a loan')
        self.assertEqual(self.api('post', '/api/holdings/', {'name': 'Loan', 'kind': 'loan'}).status_code, 400, 'a loan needs its terms')
        bad = self.mortgage_body(term_months=0)
        self.assertEqual(self.api('post', '/api/holdings/', bad).status_code, 400)
        self.assertEqual(self.client.delete(url).status_code, 204)



class LoanPaymentTests(StorageTestCase):
    """A loan starts with what is owed; real payments change it; the schedule only suggests splits and looks ahead."""

    def setUp(self):
        super().setUp()
        self.checking = Account.objects.create(name='Checking', account_type='CHECKING', last_balance=Decimal('10000'), balance_date=date(2026, 1, 1))
        self.today = date.today()

    def mortgage(self, escrow_mode='EXPENSE', **extra):
        body = {'name': 'Mortgage', 'kind': 'mortgage', 'starting_balance': '300000.00', 'starting_date': '2026-01-15',
                'escrow_starting_balance': '1200.00', 'loan': {
                    'lender': 'Big Bank', 'original_balance': '300000.00', 'interest_rate': '6.000', 'term_months': 360,
                    'origination_date': '2026-01-15', 'first_payment_date': '2026-03-01', 'property_tax_amount': '400.00',
                    'homeowners_insurance_amount': '150.00', 'pmi_amount': '100.00', 'escrow_mode': escrow_mode, **extra}}
        response = self.api('post', '/api/holdings/', body)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def account(self, holding):
        return Account.objects.get(pk=holding['id'])

    def split(self, holding, day, **params):
        query = '&'.join(f'{k}={v}' for k, v in {'date': day, **params}.items())
        return self.client.get(f"/api/holdings/{holding['id']}/payment-split/?{query}").json()

    def pay(self, holding, body):
        return self.api('post', f"/api/holdings/{holding['id']}/payment/", body)

    def test_the_split_uses_the_actual_balance(self):
        m = self.mortgage()
        s = self.split(m, '2026-03-01')
        # 6% / 12 on 300,000 = 1,500 interest; 300k at 6% for 30 years = 1,798.65 P&I
        self.assertEqual((s['interest'], s['principal'], s['property_tax'], s['insurance'], s['pmi']), ('1500.00', '298.65', '400.00', '150.00', '100.00'))
        self.assertEqual(s['total'], '2448.65')
        self.assertEqual(self.split(m, '2026-03-01', extra='200')['principal'], '498.65')
        self.assertEqual(self.split(m, '2026-03-01', amount='3000')['principal'], '850.00', 'a bigger payment: the rest is principal')

    def test_a_payment_with_escrow_expensed(self):
        from finance.models import Transaction
        m = self.mortgage()
        s = self.split(m, '2026-03-01', extra='200')
        response = self.pay(m, {**s, 'date': '2026-03-01', 'from_account': self.checking.pk})
        self.assertEqual(response.status_code, 201, response.content)
        payment = Transaction.objects.get(pk=response.json()['payment_id'])
        self.assertEqual((payment.account, payment.trnamt, payment.status), (self.checking, Decimal('-2648.65'), 'P'))
        lines = {line.subcategory.name: line.amount for line in payment.splits.all()}
        self.assertEqual(lines, {'Loan Principal': Decimal('-498.65'), 'Mortgage Interest': Decimal('-1500.00'), 'Property Tax': Decimal('-400.00'),
                                 'Home Insurance': Decimal('-150.00'), 'PMI': Decimal('-100.00')})
        self.assertEqual(sum(lines.values()), payment.trnamt, 'the split lines add up to the payment')
        status = response.json()['holding']['loan_status']
        self.assertEqual(status['owed'], '299501.35', 'only the principal (with the extra) reduces the loan')
        self.assertIsNone(status['escrow_account'])

        # The next payment's interest is on the new, lower balance
        self.assertEqual(self.split(m, '2026-04-01')['interest'], '1497.51')

        # Deleting the payment takes its principal back off the loan
        payment.delete()
        self.assertEqual(self.client.get('/api/holdings/').json()[0]['loan_status']['owed'], '300000.00')

    def test_a_payment_into_an_escrow_account_and_the_lender_paying_a_bill(self):
        from finance.models import Transaction
        m = self.mortgage(escrow_mode='ACCOUNT')
        escrow_id = m['loan_status']['escrow_account']
        self.assertIsNotNone(escrow_id)
        self.assertEqual(Decimal(m['loan_status']['escrow_balance']), Decimal('1200.00'))
        s = self.split(m, '2026-03-01')
        payment = Transaction.objects.get(pk=self.pay(m, {**s, 'date': '2026-03-01', 'from_account': self.checking.pk}).json()['payment_id'])
        lines = {line.subcategory.name: line.amount for line in payment.splits.all()}
        self.assertEqual(lines['Escrow'], Decimal('-650.00'), 'tax, insurance and PMI go into escrow, not to expenses')
        self.assertNotIn('Property Tax', lines)
        holding = self.client.get('/api/holdings/').json()[0]
        self.assertEqual(Decimal(holding['loan_status']['escrow_balance']), Decimal('1850.00'))

        response = self.api('post', f"/api/holdings/{m['id']}/escrow-payment/", {'date': '2026-03-20', 'amount': '1600.00', 'kind': 'property_tax'})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Decimal(response.json()['loan_status']['escrow_balance']), Decimal('250.00'))
        bill = Transaction.objects.get(account_id=escrow_id, trnamt=Decimal('-1600.00'))
        self.assertEqual(bill.subcategory.name, 'Property Tax', 'the expense happens when the lender pays the bill')

        # Escrow sits with the loans in net worth, as an asset
        from finance.networth import history
        listed = {a['name']: a['group'] for a in history()['accounts']}
        self.assertEqual(listed['Mortgage escrow'], 'loans')
        self.assertEqual(self.client.get('/api/accounts/').json()[0]['name'], 'Checking', 'not in the bank-account lists')

    def test_an_imported_payment_is_split_and_linked_not_duplicated(self):
        from finance.models import Transaction
        m = self.mortgage()
        s = self.split(m, '2026-03-01')
        imported = Transaction.objects.create(account=self.checking, fitid='BANK-1', dtposted=date(2026, 3, 2), trnamt=Decimal('-2448.65'), name='BIG BANK MTG', status='C')
        candidates = self.split(m, '2026-03-01')['candidates']
        self.assertEqual([c['id'] for c in candidates], [imported.pk])
        self.assertEqual(self.pay(m, {**s, 'date': '2026-03-02', 'transaction_id': imported.pk}).status_code, 201)
        self.assertEqual(Transaction.objects.filter(account=self.checking).count(), 1, 'no second payment made')
        imported.refresh_from_db()
        self.assertEqual(imported.splits.count(), 5)
        self.assertEqual(self.split(m, '2026-04-01')['candidates'], [], 'already used')
        # The parts must add up to what the bank took
        other = Transaction.objects.create(account=self.checking, fitid='BANK-2', dtposted=date(2026, 4, 1), trnamt=Decimal('-2500.00'), name='BIG BANK MTG', status='C')
        response = self.pay(m, {**s, 'date': '2026-04-01', 'transaction_id': other.pk})
        self.assertEqual(response.status_code, 400)
        self.assertIn('add up to', response.json()['error'])

    def test_statement_balance_and_looking_ahead(self):
        m = self.mortgage()
        before = m['loan_status']['payoff_date']
        s = self.split(m, '2026-03-01', extra='50000')
        self.pay(m, {**s, 'date': '2026-03-01', 'from_account': self.checking.pk})
        after = self.client.get('/api/holdings/').json()[0]['loan_status']
        self.assertLess(after['payoff_date'], before, 'a big extra payment brings the payoff date forward')
        self.api('post', f"/api/holdings/{m['id']}/values/", {'date': '2026-06-01', 'amount': '240000.00', 'source': 'June statement'})
        self.assertEqual(self.client.get('/api/holdings/').json()[0]['loan_status']['owed'], '240000.00')

    def test_bad_payments_are_refused_with_a_reason(self):
        m = self.mortgage()
        refused = lambda body: self.pay(m, {'date': '2026-03-01', 'from_account': self.checking.pk, **body})
        self.assertIn('zero', refused({}).json()['error'])
        self.assertIn('number', refused({'principal': 'lots'}).json()['error'])
        self.assertIn('more than is owed', refused({'principal': '400000'}).json()['error'])
        self.assertIn('account', self.pay(m, {'date': '2026-03-01', 'principal': '10'}).json()['error'])
        self.assertEqual(self.api('post', f"/api/holdings/{m['id']}/escrow-payment/", {'date': '2026-03-01', 'amount': '5', 'kind': 'insurance'}).status_code, 400,
                         'expense mode has no escrow account')
