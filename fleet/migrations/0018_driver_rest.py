import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies=[('fleet','0017_stop_administrative_address')]
    operations=[
        migrations.AddField(model_name='user',name='rest_started_at',field=models.DateTimeField(null=True,blank=True)),
        migrations.AddField(model_name='user',name='rest_changed_at',field=models.DateTimeField(default=django.utils.timezone.now)),
        migrations.AlterField(model_name='driverrequest',name='trip',field=models.ForeignKey(null=True,blank=True,on_delete=django.db.models.deletion.PROTECT,to='fleet.trip')),
    ]
