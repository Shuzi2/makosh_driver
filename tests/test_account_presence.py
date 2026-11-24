from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from fleet.models import User, UserSessionActivity


@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class AccountPresenceTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('+380501111001', password='Password-example-123', full_name='Адміністратор', role='admin', must_change_password=False)
        self.driver = User.objects.create_user('+380501111002', password='Password-example-123', full_name='Водій', role='driver', must_change_password=False)
        self.client.force_login(self.admin)
        self.driver_client = Client()
        self.driver_client.force_login(self.driver)
        self.driver_client.get(reverse('dashboard'))

    def presence(self):
        response = self.client.get(reverse('accounts_presence'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store')
        return response.json()['users'][str(self.driver.pk)]

    def test_active_and_stale_connection(self):
        self.assertTrue(self.presence()['online'])
        UserSessionActivity.objects.filter(user=self.driver).update(last_seen_at=timezone.now()-timedelta(minutes=6))
        status = self.presence()
        self.assertFalse(status['online'])
        self.assertTrue(status['last_seen'])
        response = self.client.get(reverse('accounts'))
        self.assertContains(response, 'На сайті')
        self.assertContains(response, 'Не в мережі')

    def test_expired_session_and_changed_password_are_offline(self):
        self.driver.set_password('Changed-password-456')
        self.driver.save(update_fields=['password'])
        self.assertFalse(self.presence()['online'])
        self.driver_client.force_login(self.driver)
        self.driver_client.get(reverse('dashboard'))
        self.assertTrue(self.presence()['online'])
        self.driver_client.logout()
        self.assertFalse(self.presence()['online'])

    def test_disabled_account_and_permissions(self):
        self.driver.is_active = False
        self.driver.save(update_fields=['is_active'])
        self.assertFalse(self.presence()['online'])
        self.driver.is_active = True
        self.driver.save(update_fields=['is_active'])
        self.assertEqual(self.driver_client.get(reverse('accounts_presence')).status_code, 403)
