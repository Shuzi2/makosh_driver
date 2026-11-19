import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies=[('fleet','0008_trip_contact')]
    operations=[migrations.CreateModel(name='DriverRequest',fields=[
        ('id',models.UUIDField(primary_key=True,editable=False,serialize=False)),
        ('kind',models.CharField(max_length=16)),
        ('fingerprint',models.CharField(max_length=64)),
        ('result',models.JSONField(default=dict)),
        ('created_at',models.DateTimeField(auto_now_add=True)),
        ('user',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,to=settings.AUTH_USER_MODEL)),
        ('trip',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,to='fleet.trip')),
    ])]
