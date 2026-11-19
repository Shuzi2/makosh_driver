from django.conf import settings
from django.db import migrations, models
from django.utils import timezone
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies=[('fleet','0009_driverrequest')]
    operations=[
        migrations.AddField(model_name='trip',name='queue_priority_at',field=models.DateTimeField(null=True,blank=True)),
        migrations.CreateModel(name='TripTransfer',fields=[
            ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
            ('previous_status',models.CharField(max_length=16)),
            ('next_status',models.CharField(max_length=16)),
            ('moved_at',models.DateTimeField(default=timezone.now)),
            ('trip',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='transfers',to='fleet.trip')),
            ('previous_driver',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='transferred_trips_from',to=settings.AUTH_USER_MODEL)),
            ('next_driver',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='transferred_trips_to',to=settings.AUTH_USER_MODEL)),
            ('from_vehicle',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='outgoing_trip_transfers',to='fleet.vehicle')),
            ('to_vehicle',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='incoming_trip_transfers',to='fleet.vehicle')),
            ('moved_by',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='trip_transfers',to=settings.AUTH_USER_MODEL)),
        ],options={'ordering':['-moved_at','-pk']}),
    ]
