from django.db import models

class MailSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField(default=False)
    host = models.CharField(max_length=200, blank=True)
    port = models.PositiveIntegerField(default=587)
    security = models.CharField(max_length=10, choices=[('starttls', 'STARTTLS'), ('tls', 'TLS')], default='starttls')
    username = models.CharField(max_length=200, blank=True)
    password_cipher = models.TextField(blank=True)
    from_email = models.EmailField(blank=True)
    from_name = models.CharField(max_length=100, default='МАКОШ Автопарк')
    site_url = models.URLField(max_length=250, blank=True)
