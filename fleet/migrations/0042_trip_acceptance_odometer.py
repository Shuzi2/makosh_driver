from django.db import migrations, models
from django.core.validators import MaxValueValidator

class Migration(migrations.Migration):
    dependencies=[('fleet','0041_trip_waybill_distances')]
    operations=[migrations.AddField(model_name='trip',name='acceptance_odometer_km',field=models.PositiveIntegerField('Показник спідометра при прийнятті, км',null=True,blank=True,validators=[MaxValueValidator(9999999)]))]
