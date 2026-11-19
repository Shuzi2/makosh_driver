from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone

class Migration(migrations.Migration):
    dependencies = [('fleet', '0033_alter_document_options_alter_document_managers_and_more'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(name='DriverAssignmentNotification', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
        ('read_at', models.DateTimeField(blank=True, null=True)),
        ('resolved_at', models.DateTimeField(blank=True, null=True)),
        ('recipient', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='assignment_notifications', to=settings.AUTH_USER_MODEL)),
        ('trip', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='assignment_notifications', to='fleet.trip')),
    ], options={'ordering':['-created_at','-pk']})]
