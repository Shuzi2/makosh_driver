from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('fleet', '0027_moderator_role')]
    operations = [migrations.CreateModel(name='ModeratorPermissionSettings', fields=[
        ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
        ('view_fleet', models.BooleanField(default=True)), ('edit_fleet', models.BooleanField(default=True)),
        ('view_trips', models.BooleanField(default=True)), ('edit_trips', models.BooleanField(default=True)),
        ('view_documents', models.BooleanField(default=True)), ('edit_documents', models.BooleanField(default=True)),
        ('view_reports', models.BooleanField(default=True)), ('updated_at', models.DateTimeField(auto_now=True)),
    ])]
