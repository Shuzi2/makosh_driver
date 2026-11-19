from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('fleet', '0006_trip_status_changed_at')]
    operations = [
        migrations.AddField(model_name='trip', name='accepted_at', field=models.DateTimeField(null=True, blank=True)),
        migrations.AddField(model_name='trip', name='accepted_by', field=models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.PROTECT, related_name='accepted_trips', to=settings.AUTH_USER_MODEL)),
    ]
