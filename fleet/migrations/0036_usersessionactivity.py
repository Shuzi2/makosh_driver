import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('fleet', '0035_vehiclerepair_expenses'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(name='UserSessionActivity', fields=[
        ('session_hash', models.CharField(max_length=64, primary_key=True, serialize=False)),
        ('browser', models.CharField(blank=True, max_length=40)),
        ('platform', models.CharField(blank=True, max_length=40)),
        ('login_at', models.DateTimeField(blank=True, null=True)),
        ('last_seen_at', models.DateTimeField(db_index=True)),
        ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='session_activity', to=settings.AUTH_USER_MODEL)),
    ])]
