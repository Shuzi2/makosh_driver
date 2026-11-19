from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
import tempfile
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.utils import timezone
from django.contrib.sessions.models import Session
from fleet.models import AuditEvent, LoginThrottle, DriverRequest, OperationJob, OperationsSettings
from fleet.operations import queue_job, cleanup_preview, perform_cleanup, schedule_due, backup_path, prune_backups


class OperationsTests(TestCase):
    def test_pending_requests_are_deduplicated(self):
        self.assertEqual(queue_job('backup').pk, queue_job('backup').pk)
        with self.assertRaises(ValueError): queue_job('drop_database')

    def test_schedule_uses_kyiv_and_runs_once_per_day(self):
        OperationsSettings.objects.create()
        before = datetime(2026, 11, 9, 0, 59, tzinfo=dt_timezone.utc)
        schedule_due(before)
        self.assertFalse(OperationJob.objects.exists())
        schedule_due(before + timedelta(minutes=1))
        self.assertEqual(OperationJob.objects.count(), 2)
        OperationJob.objects.update(status='succeeded')
        schedule_due(before + timedelta(hours=6))
        self.assertEqual(OperationJob.objects.count(), 2)

    def test_cleanup_only_old_technical_records(self):
        old = timezone.now() - timedelta(days=100)
        AuditEvent.objects.create(action='Business history')
        AuditEvent.objects.update(created_at=old)
        LoginThrottle.objects.create(key='old', window_start=old)
        LoginThrottle.objects.create(key='recent', window_start=timezone.now())
        Session.objects.create(session_key='old', session_data='', expire_date=old)
        Session.objects.create(session_key='recent', session_data='', expire_date=timezone.now()-timedelta(days=1))
        self.assertEqual(cleanup_preview(90)['login_throttle'], 1)
        perform_cleanup(90)
        self.assertTrue(AuditEvent.objects.filter(action='Business history').exists())
        self.assertTrue(AuditEvent.objects.filter(action='Технічне очищення').exists())
        self.assertEqual(LoginThrottle.objects.get().key, 'recent')
        self.assertEqual(Session.objects.get().session_key, 'recent')
        with self.assertRaises(ValueError): perform_cleanup(1)

    def test_artifact_path_rejects_traversal_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(BACKUP_ROOT=directory):
            job=OperationJob(kind='backup', artifact='../secret')
            with self.assertRaises(ValueError): backup_path(job)
            job.artifact=f'makosh-{job.id}.tar.gz'
            self.assertEqual(backup_path(job).parent, Path(directory))

    def test_schedule_dst_fallback_does_not_duplicate(self):
        OperationsSettings.objects.create()
        for hour in (0, 1, 2, 3):
            schedule_due(datetime(2026, 10, 25, hour, 30, tzinfo=dt_timezone.utc))
            OperationJob.objects.update(status='succeeded')
        self.assertEqual(OperationJob.objects.count(), 3)

    def test_disabled_schedules_do_not_queue(self):
        OperationsSettings.objects.create(backup_enabled=False, cleanup_enabled=False)
        schedule_due(datetime(2026, 11, 9, 12, tzinfo=dt_timezone.utc))
        self.assertFalse(OperationJob.objects.exists())

    def test_pruning_keeps_unrelated_files_and_failed_jobs(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(BACKUP_ROOT=directory):
            unrelated=Path(directory)/'do-not-delete.txt';unrelated.write_text('keep')
            for offset in range(3):
                job=OperationJob.objects.create(kind='backup',status='succeeded')
                job.created_at=timezone.now()-timedelta(days=offset)
                job.artifact=f'makosh-{job.id}.tar.gz';job.save()
                backup_path(job).write_bytes(b'test')
            prune_backups(2)
            self.assertTrue(unrelated.exists())
            self.assertEqual(len(list(Path(directory).glob('*.tar.gz'))),2)

    def test_manual_copies_do_not_evict_other_daily_restore_points(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(BACKUP_ROOT=directory):
            for offset in [3, 2, 1, 0, 0, 0]:
                job=OperationJob.objects.create(kind='backup',status='succeeded')
                job.artifact=f'makosh-{job.id}.tar.gz';job.save()
                OperationJob.objects.filter(pk=job.pk).update(created_at=timezone.now()-timedelta(days=offset))
                backup_path(job).write_bytes(b'test')
            prune_backups(3)
            remaining=OperationJob.objects.exclude(artifact='')
            self.assertEqual(remaining.count(),3)
            self.assertEqual(len(set(job.created_at.date() for job in remaining)),3)

    def test_weekly_restore_check_is_scheduled_once_on_sunday(self):
        OperationsSettings.objects.create()
        now = datetime(2026, 11, 8, 1, 0, tzinfo=dt_timezone.utc)
        schedule_due(now)
        self.assertEqual(OperationJob.objects.filter(kind='restore_test').count(), 1)
        OperationJob.objects.update(status='succeeded')
        schedule_due(now + timedelta(hours=2))
        schedule_due(now + timedelta(days=1))
        self.assertEqual(OperationJob.objects.filter(kind='restore_test').count(), 1)

    def test_restore_check_rejects_missing_backup(self):
        from fleet.operations import perform_restore_test
        with self.assertRaisesRegex(RuntimeError, 'No backup'):
            perform_restore_test()

    def test_restore_check_rejects_checksum_mismatch_before_database_access(self):
        import io
        import json
        import tarfile
        from fleet.operations import perform_restore_test
        with tempfile.TemporaryDirectory() as directory, override_settings(BACKUP_ROOT=directory):
            job = OperationJob.objects.create(kind='backup', status='succeeded')
            job.artifact = f'makosh-{job.id}.tar.gz'
            job.save()
            entries = {'database.dump': b'damaged', 'uploads.tar.gz': b'uploads',
                       'manifest.json': json.dumps({'sha256': {'database.dump': 'wrong', 'uploads.tar.gz': 'wrong'}}).encode()}
            with tarfile.open(backup_path(job), 'w:gz') as bundle:
                for name, data in entries.items():
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    bundle.addfile(info, io.BytesIO(data))
            with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
                perform_restore_test()

    def test_restore_failure_drops_only_created_scratch_database(self):
        import hashlib
        import io
        import json
        import tarfile
        from types import SimpleNamespace
        from fleet.operations import perform_restore_test
        class Admin:
            def __init__(self): self.statements = []
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def execute(self, statement): self.statements.append(statement.as_string(None))
        admin = Admin()
        with tempfile.TemporaryDirectory() as directory, override_settings(BACKUP_ROOT=directory):
            job = OperationJob.objects.create(kind='backup', status='succeeded')
            job.artifact = f'makosh-{job.id}.tar.gz'; job.save()
            entries = {'database.dump': b'dump', 'uploads.tar.gz': b'uploads'}
            entries['manifest.json'] = json.dumps({'sha256': {name: hashlib.sha256(data).hexdigest() for name, data in entries.items()}}).encode()
            with tarfile.open(backup_path(job), 'w:gz') as bundle:
                for name, data in entries.items():
                    info = tarfile.TarInfo(name); info.size = len(data)
                    bundle.addfile(info, io.BytesIO(data))
            db = {'NAME': 'production', 'HOST': 'db', 'PORT': '5432', 'USER': 'worker', 'PASSWORD': 'secret'}
            with override_settings(DATABASES={'default': db}), patch('fleet.operations.connection.vendor', 'postgresql'), patch('psycopg.connect', return_value=admin), patch('fleet.operations.subprocess.run', return_value=SimpleNamespace(returncode=1)):
                with self.assertRaisesRegex(RuntimeError, 'Restore failed'):
                    perform_restore_test()
            self.assertEqual(len(admin.statements), 2)
            self.assertTrue(admin.statements[0].startswith('CREATE DATABASE "makosh_verify_'))
            self.assertEqual(admin.statements[1], admin.statements[0].replace('CREATE DATABASE', 'DROP DATABASE') + ' WITH (FORCE)')
            self.assertNotIn('production', admin.statements[1])

    def test_metrics_history_uses_configured_technical_cleanup(self):
        from fleet.models import OperationsMetricSample
        from fleet.operations import sample_resource_metrics
        now = timezone.now()
        OperationsMetricSample.objects.create(sampled_at=now - timedelta(days=100), rows=[])
        OperationsMetricSample.objects.create(sampled_at=now - timedelta(days=8), rows=[])
        with patch('fleet.operations.resource_metrics', return_value={'rows': [{'label': 'CPU контейнера', 'value': '2%'}]}):
            sample_resource_metrics(now)
            sample_resource_metrics(now + timedelta(minutes=4))
            self.assertEqual(OperationsMetricSample.objects.count(), 3)
            sample_resource_metrics(now + timedelta(minutes=5))
        self.assertEqual(OperationsMetricSample.objects.count(), 4)
        perform_cleanup(90)
        self.assertEqual(OperationsMetricSample.objects.count(), 3)
