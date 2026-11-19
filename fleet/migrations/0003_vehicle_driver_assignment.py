from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('fleet', '0002_two_roles')]
    operations = [migrations.AddConstraint(
        model_name='vehicle',
        constraint=models.UniqueConstraint(fields=['default_driver'], condition=models.Q(default_driver__isnull=False), name='one_default_vehicle_per_driver'),
    )]
