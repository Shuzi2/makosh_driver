from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies=[('fleet','0010_trip_transfers_and_queue_priority')]
    operations=[
        migrations.AddField(model_name='triptransfer',name='kind',field=models.CharField(max_length=16,choices=[('transfer','Передача рейсу'),('substitution','Підміна водія')],default='transfer')),
        migrations.AddField(model_name='triptransfer',name='effective_at',field=models.DateTimeField(null=True,blank=True)),
        migrations.AddField(model_name='triptransfer',name='location',field=models.CharField(max_length=180,blank=True)),
        migrations.AddField(model_name='triptransfer',name='note',field=models.TextField(max_length=1000,blank=True)),
    ]
