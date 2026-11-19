from django.db import migrations, models


def consolidate_roles(apps, schema_editor):
    User = apps.get_model('fleet', 'User')
    users = User.objects.using(schema_editor.connection.alias)
    users.filter(role='logistic').update(role='admin')
    # Former read-only accounts do not acquire administrator access.
    users.exclude(role__in=['admin', 'driver']).update(role='driver', is_staff=False, is_superuser=False)


class Migration(migrations.Migration):
    dependencies = [('fleet', '0001_initial')]
    operations = [
        migrations.RunPython(consolidate_roles, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='user', name='role',
            field=models.CharField(choices=[('admin', 'Логіст / Адміністратор'), ('driver', 'Водій')], default='driver', max_length=16, verbose_name='Роль'),
        ),
        migrations.AddConstraint(
            model_name='user',
            constraint=models.CheckConstraint(condition=models.Q(role__in=['admin', 'driver']), name='user_role_admin_or_driver'),
        ),
    ]
