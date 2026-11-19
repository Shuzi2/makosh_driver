from django.conf import settings
from django.db import migrations,models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies=[('fleet','0011_driver_substitution_details')]
    operations=[
        migrations.AddField(model_name='trip',name='cancelled_at',field=models.DateTimeField(null=True,blank=True)),
        migrations.AddField(model_name='trip',name='cancelled_by',field=models.ForeignKey(null=True,blank=True,on_delete=django.db.models.deletion.PROTECT,related_name='cancelled_trips',to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name='trip',name='cancellation_reason',field=models.CharField(max_length=500,blank=True)),
        migrations.AddField(model_name='trip',name='cancellation_notice_required',field=models.BooleanField(default=False)),
        migrations.AddField(model_name='trip',name='cancellation_acknowledged_at',field=models.DateTimeField(null=True,blank=True)),
    ]
