from django.db import migrations,models


def initialize_kind(apps,schema_editor):
    Trailer=apps.get_model('fleet','Trailer')
    Vehicle=apps.get_model('fleet','Vehicle')
    alias=schema_editor.connection.alias
    for vehicle in Vehicle.objects.using(alias).filter(trailer_id__isnull=False).iterator():
        Trailer.objects.using(alias).filter(pk=vehicle.trailer_id).update(kind=vehicle.kind)


class Migration(migrations.Migration):
    dependencies=[('fleet','0038_separate_trailers')]
    operations=[
        migrations.AddField(model_name='trailer',name='kind',field=models.CharField(choices=[('tent','Тент'),('grain','Зерновоз')],default='tent',max_length=12,verbose_name='Тип причепа'),preserve_default=False),
        migrations.RunPython(initialize_kind,migrations.RunPython.noop),
    ]
