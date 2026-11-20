from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False,
                   STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class LinkPreviewTests(TestCase):
    def test_login_exposes_absolute_social_preview(self):
        response = self.client.get(reverse('login'))
        self.assertContains(response, 'property="og:title"')
        self.assertContains(response, 'property="og:image" content="http://testserver/static/brand/social-preview-v2.png"')
        self.assertContains(response, 'property="og:description"')
