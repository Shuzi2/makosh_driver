import uuid
from datetime import time
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models


class OperationsSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    backup_enabled = models.BooleanField(default=True)
    backup_time = models.TimeField(default=time(3, 0))
    backup_keep = models.PositiveSmallIntegerField(default=14, validators=[MinValueValidator(1), MaxValueValidator(90)])
    cleanup_enabled = models.BooleanField(default=True)
    cleanup_days = models.PositiveSmallIntegerField(default=90, validators=[MinValueValidator(90), MaxValueValidator(3650)])
    updated_at = models.DateTimeField(auto_now=True)
    worker_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='operations_singleton')]


class OperationJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=12, choices=[('backup', 'Резервна копія'), ('cleanup', 'Очищення'), ('restore_test', 'Перевірка відновлення')])
    status = models.CharField(max_length=12, default='queued', choices=[('queued', 'У черзі'), ('running', 'Виконується'), ('succeeded', 'Успішно'), ('failed', 'Помилка')])
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    scheduled_for = models.DateField(null=True, blank=True)
    message = models.TextField(blank=True)
    artifact = models.CharField(max_length=255, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['kind'], condition=models.Q(status__in=['queued', 'running']), name='one_pending_operation_kind'),
            models.UniqueConstraint(fields=['kind', 'scheduled_for'], condition=models.Q(scheduled_for__isnull=False), name='one_daily_operation_kind'),
        ]


class OperationsMetricSample(models.Model):
    sampled_at = models.DateTimeField(db_index=True)
    rows = models.JSONField(default=list)
