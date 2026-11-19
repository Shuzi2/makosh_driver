from django.db import migrations, models
from django.core.validators import MaxValueValidator

class Migration(migrations.Migration):
    dependencies=[('fleet','0040_trip_acceptance_location')]
    operations=[
        migrations.AddField(model_name='trip',name='empty_distance_km',field=models.PositiveIntegerField('Переїзд до завантаження, км',null=True,blank=True,validators=[MaxValueValidator(20000)])),
        migrations.AddField(model_name='trip',name='loaded_distance_km',field=models.PositiveIntegerField('Пробіг з вантажем, км',null=True,blank=True,validators=[MaxValueValidator(20000)])),
    ]
