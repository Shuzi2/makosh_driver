from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('fleet', '0026_operations_metric_sample')]
    operations = [
        migrations.RemoveConstraint(model_name='user', name='user_role_admin_or_driver'),
        migrations.AlterField(model_name='user', name='role', field=models.CharField(choices=[('admin', 'Логіст / Адміністратор'), ('moderator', 'Модератор'), ('driver', 'Водій')], default='driver', max_length=16, verbose_name='Роль')),
        migrations.AddConstraint(model_name='user', constraint=models.CheckConstraint(condition=models.Q(role__in=['admin','moderator','driver']), name='user_role_valid')),
    ]
