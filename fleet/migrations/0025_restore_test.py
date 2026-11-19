from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('fleet', '0024_driver_permissions')]
    operations = [
        migrations.AlterField(
            model_name='operationjob', name='kind',
            field=models.CharField(max_length=12, choices=[('backup', 'Резервна копія'), ('cleanup', 'Очищення'), ('restore_test', 'Перевірка відновлення')]),
        ),
    ]

