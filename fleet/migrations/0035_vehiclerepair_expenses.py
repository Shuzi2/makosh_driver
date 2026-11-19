import django.core.validators
from django.db import migrations, models
import fleet.models

class Migration(migrations.Migration):
    dependencies = [('fleet', '0034_driverassignmentnotification')]
    operations = [
        migrations.AddField(model_name='vehiclerepair', name='amount', field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True, validators=[django.core.validators.MinValueValidator(0)], verbose_name='Витрати, грн')),
        migrations.AddField(model_name='vehiclerepair', name='invoice', field=models.FileField(blank=True, upload_to=fleet.models.repair_document_path, verbose_name='Рахунок')),
        migrations.AddField(model_name='vehiclerepair', name='act', field=models.FileField(blank=True, upload_to=fleet.models.repair_document_path, verbose_name='Акт')),
    ]
