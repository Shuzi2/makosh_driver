from django.db import migrations,models


class Migration(migrations.Migration):
    dependencies=[('fleet','0016_existing_trip_routes')]
    operations=[
        migrations.AddField(model_name='tripstop',name='settlement_code',field=models.CharField('Код населеного пункту',max_length=19,blank=True)),
        migrations.AddField(model_name='tripstop',name='settlement_name',field=models.CharField('Населений пункт',max_length=180,blank=True)),
        migrations.AddField(model_name='tripstop',name='region',field=models.CharField('Область',max_length=150,blank=True)),
        migrations.AddField(model_name='tripstop',name='district',field=models.CharField('Район',max_length=150,blank=True)),
        migrations.AddField(model_name='tripstop',name='community',field=models.CharField('Громада (ОТГ)',max_length=150,blank=True)),
    ]
