"""Local maintenance worker: no shell interpolation or Docker socket access."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.sessions.models import Session
from django.db import connection, transaction
from django.utils import timezone
from .models import AuditEvent, LoginThrottle, OperationJob, OperationsSettings, OperationsMetricSample

KYIV = ZoneInfo('Europe/Kyiv')
LOCK_ID = 714032691


def queue_job(kind, user=None, scheduled_for=None):
    if kind not in ('backup', 'cleanup', 'restore_test'):
        raise ValueError('Unknown operation')
    with transaction.atomic():
        OperationsSettings.objects.get_or_create(pk=1)
        OperationsSettings.objects.select_for_update().get(pk=1)
        pending = OperationJob.objects.filter(kind=kind, status__in=['queued', 'running']).first()
        if pending:
            return pending
        job = OperationJob.objects.create(kind=kind, requested_by=user, scheduled_for=scheduled_for)
        if user:
            AuditEvent.objects.create(actor=user, action='Запит резервної копії' if kind == 'backup' else 'Запит технічного очищення', detail=str(job.pk))
        return job


def schedule_due(now=None):
    now = now or timezone.now()
    local = now.astimezone(KYIV)
    config, _ = OperationsSettings.objects.get_or_create(pk=1)
    if local.time().replace(tzinfo=None) < config.backup_time:
        return
    for kind, enabled in [('backup', config.backup_enabled), ('cleanup', config.cleanup_enabled)]:
        if enabled and not OperationJob.objects.filter(kind=kind, scheduled_for=local.date()).exists():
            queue_job(kind, scheduled_for=local.date())
    if config.backup_enabled and local.weekday() == 6 and not OperationJob.objects.filter(kind='restore_test', scheduled_for=local.date()).exists():
        queue_job('restore_test', scheduled_for=local.date())


def _technical_querysets(days):
    if not 90 <= int(days) <= 3650:
        raise ValueError('Retention must be between 90 and 3650 days')
    cutoff = timezone.now() - timedelta(days=int(days))
    from .models import MapSearchCache, UserSessionActivity
    return {
        'sessions': Session.objects.filter(expire_date__lt=cutoff),
        'session_activity': UserSessionActivity.objects.filter(last_seen_at__lt=cutoff),
        'login_throttle': LoginThrottle.objects.filter(window_start__lt=cutoff),
        'map_cache': MapSearchCache.objects.filter(cached_at__lt=cutoff),
        'operation_logs': OperationJob.objects.filter(finished_at__lt=cutoff, status__in=['succeeded', 'failed'], artifact=''),
        'metric_samples': OperationsMetricSample.objects.filter(sampled_at__lt=cutoff),
    }


def cleanup_preview(days):
    return {key: qs.count() for key, qs in _technical_querysets(days).items()}


def perform_cleanup(days):
    with transaction.atomic():
        counts = {key: qs.delete()[0] for key, qs in _technical_querysets(days).items()}
        AuditEvent.objects.create(action='Технічне очищення', detail=json.dumps({'days': days, 'counts': counts}, ensure_ascii=False))
    return counts


def backup_path(job):
    expected = f'makosh-{job.id}.tar.gz'
    if job.kind != 'backup' or job.artifact != expected:
        raise ValueError('Invalid backup artifact')
    root = Path(settings.BACKUP_ROOT).resolve()
    path = root / expected
    if path.is_symlink() or path.resolve().parent != root:
        raise ValueError('Invalid backup path')
    return path


def prune_backups(keep):
    if not 1 <= int(keep) <= 90:
        raise ValueError('Invalid backup retention')
    jobs = OperationJob.objects.filter(kind='backup', status='succeeded').exclude(artifact='').order_by('-created_at')
    retained_days = set()
    for job in jobs:
        day = job.created_at.astimezone(KYIV).date()
        if day not in retained_days and len(retained_days) < int(keep):
            retained_days.add(day)
            continue
        path = backup_path(job)
        path.unlink(missing_ok=True)
        job.artifact = ''
        job.details = {**job.details, 'pruned_at': timezone.now().isoformat()}
        job.save(update_fields=['artifact', 'details'])


def _sha(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def perform_backup(job):
    import psycopg
    from psycopg import sql
    db = settings.DATABASES['default']
    if connection.vendor != 'postgresql':
        raise RuntimeError('Backup requires PostgreSQL')
    root = Path(settings.BACKUP_ROOT).resolve()
    root.mkdir(parents=True, exist_ok=True)
    media = Path(settings.MEDIA_ROOT).resolve()
    env = {**os.environ, 'PGPASSWORD': str(db['PASSWORD'])}
    args = ['pg_dump', '--host', str(db['HOST']), '--port', str(db['PORT']), '--username', str(db['USER']), '--dbname', str(db['NAME']), '--format=custom', '--no-password']
    artifact = f'makosh-{job.id}.tar.gz'
    final = root / artifact
    if final.exists() or final.is_symlink():
        raise RuntimeError('Backup target already exists')
    with tempfile.TemporaryDirectory(prefix='.incomplete-', dir=root) as tmp:
        tmp = Path(tmp)
        dump = tmp / 'database.dump'
        uploads = tmp / 'uploads.tar.gz'
        # Share locks preserve DB/file consistency, allowing reads but briefly waiting writes.
        with psycopg.connect(host=db['HOST'], port=db['PORT'], user=db['USER'], password=db['PASSWORD'], dbname=db['NAME']) as snapshot:
            snapshot.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            snapshot.execute("SET LOCAL lock_timeout = '10s'")
            tables = [r[0] for r in snapshot.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename NOT IN ('fleet_operationjob','fleet_operationssettings') ORDER BY tablename")]
            snapshot.execute(sql.SQL('LOCK TABLE {} IN SHARE MODE').format(sql.SQL(', ').join(map(sql.Identifier, tables))))
            snapshot_id = snapshot.execute('SELECT pg_export_snapshot()').fetchone()[0]
            documents = [r[0] for r in snapshot.execute('SELECT file FROM fleet_document')]
            for name in documents:
                document = media / name
                if not document.resolve().is_relative_to(media) or not document.is_file() or document.is_symlink():
                    raise RuntimeError('An uploaded document is missing or outside the uploads directory')
            result = subprocess.run(args + ['--snapshot', snapshot_id, '--file', str(dump)], env=env, capture_output=True, timeout=1800)
            if result.returncode:
                raise RuntimeError('pg_dump failed; previous backups retained')
            with tarfile.open(uploads, 'w:gz') as archive:
                for path in sorted(media.rglob('*')):
                    if path.is_symlink():
                        raise RuntimeError('Symlink in uploads; backup stopped')
                    if path.is_file():
                        archive.add(path, arcname='uploads/' + path.relative_to(media).as_posix(), recursive=False)
            with tarfile.open(uploads, 'r:gz') as archive:
                members = set(archive.getnames())
                if any('uploads/' + name not in members for name in documents):
                    raise RuntimeError('Backup missing document files')
        with dump.open('rb') as file:
            if file.read(5) != b'PGDMP':
                raise RuntimeError('Invalid PostgreSQL dump')
        listed = subprocess.run(['pg_restore', '--list', str(dump)], capture_output=True, timeout=60)
        if listed.returncode:
            raise RuntimeError('PostgreSQL dump validation failed')
        manifest = {'format': 1, 'job': str(job.id), 'created_at': timezone.now().isoformat(), 'timezone': 'Europe/Kyiv', 'database': db['NAME'], 'documents': len(documents), 'sha256': {'database.dump': _sha(dump), 'uploads.tar.gz': _sha(uploads)}}
        (tmp / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        partial = tmp / 'bundle.tar.gz'
        with tarfile.open(partial, 'w:gz') as archive:
            for name in ('database.dump', 'uploads.tar.gz', 'manifest.json'):
                archive.add(tmp / name, arcname=name, recursive=False)
        os.chmod(partial, 0o600)
        os.replace(partial, final)
    return artifact, final.stat().st_size, manifest


def perform_restore_test():
    """Restore the newest retained archive into a database owned by this invocation."""
    job = OperationJob.objects.filter(kind='backup', status='succeeded').exclude(artifact='').order_by('-created_at').first()
    if not job:
        raise RuntimeError('No backup available for restore test')
    path = backup_path(job)
    with tempfile.TemporaryDirectory(prefix='makosh-restore-') as directory:
        tmp = Path(directory)
        expected = {'database.dump', 'uploads.tar.gz', 'manifest.json'}
        with tarfile.open(path, 'r:gz') as bundle:
            members = bundle.getmembers()
            if len(members) != len(expected) or {m.name for m in members} != expected or any(not m.isfile() for m in members):
                raise RuntimeError('Invalid backup bundle')
            for member in members:
                with bundle.extractfile(member) as source, (tmp / member.name).open('wb') as target:
                    shutil.copyfileobj(source, target)
        manifest = json.loads((tmp / 'manifest.json').read_text(encoding='utf-8'))
        for name in ('database.dump', 'uploads.tar.gz'):
            if _sha(tmp / name) != manifest['sha256'][name]:
                raise RuntimeError('Backup checksum mismatch')
        if connection.vendor != 'postgresql':
            raise RuntimeError('Restore test requires PostgreSQL')
        import psycopg
        from psycopg import sql
        db = settings.DATABASES['default']
        scratch = 'makosh_verify_' + uuid.uuid4().hex
        env = {**os.environ, 'PGPASSWORD': str(db['PASSWORD'])}
        args = ['pg_restore', '--host', str(db['HOST']), '--port', str(db['PORT']), '--username', str(db['USER']), '--dbname', scratch, '--no-password', '--no-owner', '--no-privileges', '--exit-on-error', str(tmp / 'database.dump')]
        created = False
        with psycopg.connect(host=db['HOST'], port=db['PORT'], user=db['USER'], password=db['PASSWORD'], dbname=db['NAME'], autocommit=True) as admin:
            try:
                admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(scratch)))
                created = True
                result = subprocess.run(args, env=env, capture_output=True, timeout=1800)
                if result.returncode:
                    raise RuntimeError('Restore failed')
                with psycopg.connect(host=db['HOST'], port=db['PORT'], user=db['USER'], password=db['PASSWORD'], dbname=scratch) as restored:
                    documents = [row[0] for row in restored.execute('SELECT file FROM fleet_document')]
                with tarfile.open(tmp / 'uploads.tar.gz', 'r:gz') as uploads:
                    files = {m.name for m in uploads.getmembers() if m.isfile()}
                    if any(not name or name.startswith('/') or '..' in Path(name).parts or 'uploads/' + name not in files for name in documents):
                        raise RuntimeError('Restored documents are missing from archive')
                if len(documents) != manifest['documents']:
                    raise RuntimeError('Restored document count mismatch')
                return {'backup_job': str(job.pk), 'documents': len(documents), 'verified_at': timezone.now().isoformat()}
            finally:
                if created:
                    admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(scratch)))


def resource_metrics():
    """Live readings: Linux Docker VM, app container, and backup filesystem."""
    def gib(value):
        return f'{value / (1024 ** 3):.2f} ГБ'
    def bounded(value):
        return round(max(0.0, min(100.0, value)), 1)
    def cpu_totals():
        parts = Path('/proc/stat').read_text().splitlines()[0].split()[1:]
        numbers = [int(part) for part in parts]
        # guest and guest_nice are included in user/nice, so do not double-count.
        total = sum(numbers[:8])
        idle = numbers[3] + numbers[4]
        return total, idle
    def network_totals():
        for line in Path('/proc/net/dev').read_text().splitlines():
            name, sep, rest = line.partition(':')
            if sep and name.strip() == 'eth0':
                fields = rest.split()
                return int(fields[0]), int(fields[8])
        raise ValueError('No eth0')
    def net_rate(value):
        return f'{value / 1024:.1f} КБ/с' if value < 1024*1024 else f'{value / (1024*1024):.1f} МБ/с'
    rows = []
    def add(key, label, value, hint, percent=None):
        rows.append({'key':key,'label':label,'value':value,'hint':hint,'percent':percent})
    try:
        total1, idle1 = cpu_totals()
        rx1, tx1 = network_totals()
        started = time.monotonic()
        # A longer sample catches brief CPU and network bursts that a 150 ms
        # snapshot can miss on an otherwise idle Docker VM.
        time.sleep(.75)
        total2, idle2 = cpu_totals()
        rx2, tx2 = network_totals()
        elapsed = max(time.monotonic() - started, .001)
        delta = total2-total1
        cpu = bounded(100 * (delta-(idle2-idle1)) / delta) if delta > 0 else None
        add('cpu','Процесор Docker VM', f'{cpu:.1f} %' if cpu is not None else 'Недоступно', 'Завантаження всіх ядер віртуального середовища Docker.', cpu)
        add('network','Мережа контейнера', 'Вхід: '+net_rate(max(0,rx2-rx1)/elapsed), 'Вихід: '+net_rate(max(0,tx2-tx1)/elapsed)+' · тільки контейнер застосунку')
    except (OSError, ValueError, IndexError):
        add('cpu','Процесор Docker VM','Недоступно','Вимірювання недоступне')
        add('network','Мережа контейнера','Недоступно','Вимірювання недоступне')
    try:
        fields = {}
        for line in Path('/proc/meminfo').read_text().splitlines():
            key, _, rest = line.partition(':')
            if key in ('MemTotal','MemAvailable'):
                fields[key] = int(rest.split()[0]) * 1024
        total, available = fields['MemTotal'], fields['MemAvailable']
        if total <= 0 or available > total:
            raise ValueError('Invalid memory values')
        used = total-available
        percent=bounded(100*used/total)
        add('memory','Пам’ять Docker VM', f'{percent:.1f} %', f'Зайнято {gib(used)} з {gib(total)} · доступно {gib(available)}', percent)
    except (OSError, ValueError, KeyError, IndexError):
        add('memory','Пам’ять Docker VM','Недоступно','Вимірювання недоступне')
    try:
        disk = shutil.disk_usage(settings.BACKUP_ROOT)
        if disk.total <= 0:
            raise ValueError('Invalid disk capacity')
        used = disk.total-disk.free
        percent = bounded(100*used/disk.total)
        detail=f'Зайнято {gib(used)} з {gib(disk.total)} · вільно {gib(disk.free)}'
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_database_size(current_database())')
                detail += ' · база '+gib(cursor.fetchone()[0])
        add('disk','Диск для копій', f'{percent:.1f} %', detail, percent)
    except (OSError, ValueError):
        add('disk','Диск для копій','Недоступно','Вимірювання недоступне')
    order = {'cpu':0,'memory':1,'disk':2,'network':3}
    rows.sort(key=lambda row:order[row['key']])
    return {'rows': rows, 'scope': 'Процесор і пам’ять — Linux-середовище Docker; мережа — контейнер застосунку; диск — місце для копій. Це не повне навантаження Windows.'}

def sample_resource_metrics(now=None):
    now = now or timezone.now()
    if OperationsMetricSample.objects.filter(sampled_at__gt=now - timedelta(minutes=5)).exists():
        return
    rows = resource_metrics()['rows']
    OperationsMetricSample.objects.create(sampled_at=now, rows=[row for row in rows if row['label'] != 'Розмір бази'])


def worker_tick():
    """Single worker execution across processes; release lock even on an exception."""
    if connection.vendor != 'postgresql':
        raise RuntimeError('Worker requires PostgreSQL')
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock(%s)', [LOCK_ID])
        if not cursor.fetchone()[0]:
            return
    try:
        OperationsSettings.objects.get_or_create(pk=1)
        OperationsSettings.objects.filter(pk=1).update(worker_seen_at=timezone.now())
        # Holding the global lock means any previous running job was interrupted.
        OperationJob.objects.filter(status='running').update(status='failed', finished_at=timezone.now(), message='Виконання перерване перезапуском. Попередні копії збережено; повторіть вручну.')
        try:
            sample_resource_metrics()
        except Exception as exc:
            print(f'Metrics sample: {type(exc).__name__}', flush=True)
        schedule_due()
        for job in OperationJob.objects.filter(status='queued').order_by('created_at'):
            job.status = 'running'; job.started_at = timezone.now()
            job.save(update_fields=['status', 'started_at'])
            try:
                config = OperationsSettings.objects.get(pk=1)
                if job.kind == 'backup':
                    job.artifact, job.size_bytes, job.details = perform_backup(job)
                    job.message = 'База та файли скопійовані; архів і контрольні суми перевірено.'
                elif job.kind == 'restore_test':
                    job.details = perform_restore_test()
                    job.message = 'Копію відновлено в тимчасовій базі; документи та контрольні суми перевірено.'
                else:
                    job.details = perform_cleanup(config.cleanup_days)
                    job.message = 'Очищено лише технічні записи. Ділову історію збережено.'
                job.status = 'succeeded'
            except Exception as exc:
                job.status = 'failed'
                # Never persist exception text containing DB credentials or document paths.
                job.message = f'Операція не завершена ({type(exc).__name__}). Попередні копії збережено. Перевірте місце на диску та журнал worker.'
                print(f'Operation {job.id}: {type(exc).__name__}', flush=True)
            job.finished_at = timezone.now()
            job.save()
            AuditEvent.objects.create(actor=job.requested_by, action='Результат обслуговування', detail=f'{job.kind}: {job.status}; {job.id}')
            if job.kind == 'backup' and job.status == 'succeeded':
                prune_backups(config.backup_keep)
            OperationsSettings.objects.filter(pk=1).update(worker_seen_at=timezone.now())
    finally:
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_unlock(%s)', [LOCK_ID])

