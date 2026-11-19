from django.db import migrations, models


def snapshot_existing_completed(apps, schema_editor):
    Trip = apps.get_model('fleet', 'Trip')
    alias = schema_editor.connection.alias
    for trip in Trip.objects.using(alias).filter(status='completed').select_related('vehicle', 'driver').iterator(chunk_size=500):
        Trip.objects.using(alias).filter(pk=trip.pk, completed_tractor_plate__isnull=True).update(
            completed_tractor_plate=trip.vehicle.plate,
            completed_trailer_plate=trip.vehicle.trailer_plate,
            completed_vehicle_kind=trip.vehicle.kind,
            completed_driver_name=trip.driver.full_name,
        )


class Migration(migrations.Migration):
    dependencies = [('fleet', '0036_usersessionactivity')]
    operations = [
        migrations.AddField(model_name='trip', name='completed_tractor_plate', field=models.CharField(max_length=20, null=True, blank=True, editable=False)),
        migrations.AddField(model_name='trip', name='completed_trailer_plate', field=models.CharField(max_length=20, null=True, blank=True, editable=False)),
        migrations.AddField(model_name='trip', name='completed_vehicle_kind', field=models.CharField(max_length=12, null=True, blank=True, editable=False)),
        migrations.AddField(model_name='trip', name='completed_driver_name', field=models.CharField(max_length=150, null=True, blank=True, editable=False)),
        migrations.RunPython(snapshot_existing_completed, migrations.RunPython.noop),
    ]
