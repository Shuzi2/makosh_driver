from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('fleet', '0022_operations_admin')]
    operations = [
        migrations.CreateModel(
            name='DriverPermissionSettings',
            fields=[
                ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ('view_trips', models.BooleanField(default=True)),
                ('change_stages', models.BooleanField(default=True)),
                ('upload_documents', models.BooleanField(default=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'constraints': [models.CheckConstraint(condition=models.Q(id=1), name='driver_permissions_singleton')]},
        ),
    ]
