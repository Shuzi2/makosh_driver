from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('fleet', '0052_user_can_delete_test_trips')]
    operations = [migrations.AddField(model_name='tripstop', name='google_maps_url', field=models.URLField(blank=True, max_length=2048, verbose_name='Посилання Google Maps'))]
