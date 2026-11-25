"""Durable, deduplicated service alerts for fleet staff."""
from django.db import transaction
from .models import MaintenanceItem, MaintenanceNotification, User
from .push import staff_can_receive, enqueue_staff


def notify(item,episode,message):
    with transaction.atomic():
        item=MaintenanceItem.objects.select_for_update().select_related('vehicle').get(pk=item.pk)
        created=False
        for user in User.objects.filter(is_active=True,role__in=['admin','moderator']):
            if not staff_can_receive(user,'staff_maintenance'): continue
            _,is_new=MaintenanceNotification.objects.get_or_create(recipient=user,item=item,episode=episode,defaults={'message':message[:300]})
            created=created or is_new
        if created:
            transaction.on_commit(lambda: enqueue_staff('staff_maintenance',message=message))


def scan_due():
    for item in MaintenanceItem.objects.filter(kind='service',completed_at__isnull=True).select_related('vehicle'):
        state=item.status_info()['key']
        if state not in ('soon','overdue'): continue
        episode=f'{state}:{item.due_date or "-"}:{item.due_km if item.due_km is not None else "-"}'
        text=f'{"Прострочено ТО" if state=="overdue" else "Наближається ТО"}: {item.vehicle.plate} · {item.title}'
        notify(item,episode,text)
