from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('fleet','0007_trip_acceptance')]
    operations = [
        migrations.AddField(model_name='trip', name='contact_name', field=models.CharField('Контакт на розвантаження', max_length=150, blank=True)),
        migrations.AddField(model_name='trip', name='contact_phone', field=models.CharField('Телефон на розвантаженні', max_length=16, blank=True)),
        migrations.AddField(model_name='trip', name='loading_contact_name', field=models.CharField('Контакт на завантаження', max_length=150, blank=True)),
        migrations.AddField(model_name='trip', name='loading_contact_phone', field=models.CharField('Телефон на завантаженні', max_length=16, blank=True)),
    ]
