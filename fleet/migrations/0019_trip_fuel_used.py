from django.db import migrations,models
import django.core.validators


class Migration(migrations.Migration):
    dependencies=[('fleet','0018_driver_rest')]
    operations=[migrations.AddField(model_name='trip',name='fuel_used_l',field=models.DecimalField('Витрачено пального, л',max_digits=7,decimal_places=2,null=True,blank=True,validators=[django.core.validators.MinValueValidator(0),django.core.validators.MaxValueValidator(20000)]))]
