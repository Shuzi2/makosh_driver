from django.db import migrations, models
from django.utils import timezone


def restore_times(apps, schema_editor):
    Trip = apps.get_model('fleet', 'Trip')
    Event = apps.get_model('fleet', 'AuditEvent')
    alias = schema_editor.connection.alias
    for trip in Trip.objects.using(alias).iterator():
        event = Event.objects.using(alias).filter(trip_id=trip.pk, action__in=['Змінено статус', 'Розпочато рейс із черги']).order_by('-created_at', '-id').first()
        changed = event.created_at if event else (trip.completed_at if trip.status == 'completed' else trip.created_at if trip.status in ['planned', 'queued'] else None)
        Trip.objects.using(alias).filter(pk=trip.pk).update(status_changed_at=changed)


class Migration(migrations.Migration):
    dependencies = [('fleet', '0005_repairs_and_trip_queue')]
    operations = [
        migrations.AddField(model_name='trip', name='status_changed_at', field=models.DateTimeField(default=timezone.now, null=True, blank=True)),
        migrations.RunPython(restore_times, migrations.RunPython.noop),
    ]
