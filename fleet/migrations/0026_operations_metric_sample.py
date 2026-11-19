from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('fleet', '0025_restore_test')]
    operations = [migrations.CreateModel(name='OperationsMetricSample', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('sampled_at', models.DateTimeField(db_index=True)),
        ('rows', models.JSONField(default=list)),
    ])]
