from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('fleet', '0051_pushsubscription_pushdelivery')]
    operations = [migrations.AddField(model_name='user', name='can_delete_test_trips', field=models.BooleanField(default=False, verbose_name='Може видаляти тестові рейси'))]
