from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [('fleet', '0029_mail_settings'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(
        name='TtnNotification',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
            ('read_at', models.DateTimeField(blank=True, null=True)),
            ('document', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notifications', to='fleet.document')),
            ('recipient', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ttn_notifications', to=settings.AUTH_USER_MODEL)),
        ],
        options={'ordering':['-created_at','-pk'], 'constraints':[models.UniqueConstraint(fields=('recipient','document'),name='ttn_notice_recipient_document_unique')]},
    )]
