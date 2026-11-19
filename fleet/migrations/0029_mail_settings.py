from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('fleet', '0028_moderator_permissions')]
    operations = [migrations.CreateModel(name='MailSettings', fields=[
        ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
        ('enabled', models.BooleanField(default=False)), ('host', models.CharField(blank=True,max_length=200)),
        ('port', models.PositiveIntegerField(default=587)), ('security', models.CharField(choices=[('starttls','STARTTLS'),('tls','TLS')],default='starttls',max_length=10)),
        ('username', models.CharField(blank=True,max_length=200)), ('password_cipher', models.TextField(blank=True)),
        ('from_email', models.EmailField(blank=True,max_length=254)), ('from_name', models.CharField(default='МАКОШ Автопарк',max_length=100)),
        ('site_url', models.URLField(blank=True,max_length=250)),
    ])]
