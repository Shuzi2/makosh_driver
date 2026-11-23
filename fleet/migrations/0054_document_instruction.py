from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('fleet', '0053_tripstop_google_maps_url')]
    operations = [migrations.AlterField(model_name='document', name='kind', field=models.CharField(choices=[('ttn', 'ТТН із завантаження'), ('signed_ttn', 'Підписана ТТН'), ('weight', 'Ваговий талон'), ('other', 'Інший документ'), ('instruction', 'Інструкція для водія')], max_length=16, verbose_name='Тип документа'))]
