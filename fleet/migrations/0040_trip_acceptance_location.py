from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('fleet', '0039_trailer_kind')]
    operations = [
        migrations.AddField(
            model_name='trip', name='acceptance_location',
            field=models.CharField('Місце водія під час прийняття рейсу', max_length=250, blank=True),
        ),
    ]
