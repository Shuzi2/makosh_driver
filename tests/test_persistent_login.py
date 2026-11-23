from django.test import TestCase, override_settings
from django.urls import reverse
from fleet.models import User


@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False,
                   STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class PersistentLoginTests(TestCase):
    def test_login_cookie_survives_browser_close_and_logout_invalidates_session(self):
        User.objects.create_user('+380501239876', password='secure-test-password', full_name='Тест', role='driver', must_change_password=False)
        response = self.client.post(reverse('login'), {'username': '+380501239876', 'password': 'secure-test-password'})
        self.assertEqual(response.status_code, 302)
        cookie = response.cookies['sessionid']
        self.assertGreaterEqual(int(cookie['max-age']), 364 * 24 * 60 * 60)
        self.assertTrue(cookie['expires'])
        self.assertTrue(self.client.session.get('_auth_user_id'))
        self.client.post(reverse('logout'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_admin_session_survives_browser_close(self):
        User.objects.create_user('+380501239877', password='secure-test-password', full_name='Адмін', role='admin', must_change_password=False)
        response = self.client.post(reverse('login'), {'username': '+380501239877', 'password': 'secure-test-password'})
        self.assertEqual(response.status_code, 302)
        self.assertGreaterEqual(int(response.cookies['sessionid']['max-age']), 364 * 24 * 60 * 60)

    def test_moderator_session_is_renewed_after_a_day(self):
        moderator=User.objects.create_user('+380501239879',password='secure-test-password',full_name='Логіст',role='moderator',must_change_password=False)
        self.client.force_login(moderator)
        session=self.client.session;session['driver_session_renewed_at']=0;session.save()
        response=self.client.get(reverse('dashboard'))
        self.assertGreaterEqual(int(response.cookies['sessionid']['max-age']),364*24*60*60)

    def test_driver_session_is_renewed_after_a_day_of_activity(self):
        driver = User.objects.create_user('+380501239878', password='secure-test-password', full_name='Водій', role='driver', must_change_password=False)
        self.client.force_login(driver)
        session = self.client.session
        session['driver_session_renewed_at'] = 0
        session.save()
        response = self.client.get(reverse('dashboard'))
        self.assertGreaterEqual(int(response.cookies['sessionid']['max-age']), 364 * 24 * 60 * 60)
