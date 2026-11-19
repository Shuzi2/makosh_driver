import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def create_trailers(apps, schema_editor):
    Vehicle=apps.get_model('fleet','Vehicle')
    Trailer=apps.get_model('fleet','Trailer')
    alias=schema_editor.connection.alias
    for vehicle in Vehicle.objects.using(alias).all().iterator():
        if vehicle.trailer_plate:
            trailer,_=Trailer.objects.using(alias).get_or_create(plate=vehicle.trailer_plate)
            Vehicle.objects.using(alias).filter(pk=vehicle.pk).update(trailer_id=trailer.pk)


class Migration(migrations.Migration):
    dependencies=[('fleet','0037_trip_completed_identity'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[
        migrations.CreateModel(name='Trailer',fields=[('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),('plate',models.CharField(max_length=20,unique=True,verbose_name='Номер причепа'))],options={'ordering':['plate']}),
        migrations.AlterField(model_name='vehicle',name='trailer_plate',field=models.CharField(blank=True,max_length=20,null=True,unique=True,verbose_name='Причіп')),
        migrations.AddField(model_name='vehicle',name='trailer',field=models.OneToOneField(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,related_name='vehicle',to='fleet.trailer')),
        migrations.CreateModel(name='TrailerTransfer',fields=[('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),('note',models.CharField(blank=True,max_length=500)),('created_at',models.DateTimeField(auto_now_add=True)),('actor',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,to=settings.AUTH_USER_MODEL)),('from_vehicle',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,related_name='trailer_transfers_out',to='fleet.vehicle')),('to_vehicle',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,related_name='trailer_transfers_in',to='fleet.vehicle')),('trailer',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='transfers',to='fleet.trailer'))],options={'ordering':['-created_at','-pk']}),
        migrations.RunPython(create_trailers,migrations.RunPython.noop),
    ]
