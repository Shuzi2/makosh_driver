from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies=[('fleet','0042_trip_acceptance_odometer')]
    operations=[migrations.AddField(model_name='trip',name='waybill_overrides',field=models.JSONField(default=dict,blank=True,editable=False))]
