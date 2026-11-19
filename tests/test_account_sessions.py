from django.contrib.sessions.models import Session
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from fleet.models import User

@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class AccountSessionTests(TestCase):
    def setUp(self):
        self.admin=User.objects.create_user('+380501101001',password='Password-example-123',full_name='Адміністратор',role='admin',must_change_password=False)
        self.driver=User.objects.create_user('+380501101002',password='Password-example-123',full_name='Водій Приклад',role='driver',must_change_password=False)
        self.other=User.objects.create_user('+380501101003',password='Password-example-123',full_name='Інший водій',role='driver',must_change_password=False)
        self.client.force_login(self.admin)
        self.driver_client=Client(HTTP_USER_AGENT='Mozilla/5.0 (Windows NT 10.0) Chrome/120.0.0.0 Safari/537.36')
        self.driver_client.force_login(self.driver)
        self.driver_client.get(reverse('dashboard'))
        self.other_client=Client(); self.other_client.force_login(self.other)
        self.other_client.get(reverse('dashboard'))

    def test_admin_can_end_only_target_session_without_exposing_key(self):
        response=self.client.get(reverse('account_detail',args=[self.driver.pk]))
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(response.context['account_sessions']),1)
        entry=response.context['account_sessions'][0]
        self.assertContains(response,'Chrome')
        key=self.driver_client.session.session_key
        self.assertNotContains(response,key)
        url=reverse('account_sessions_end',args=[self.driver.pk])
        self.assertEqual(self.client.get(url).status_code,405)
        self.assertEqual(self.client.post(url,{'session':entry['token']}).status_code,302)
        self.assertFalse(Session.objects.filter(session_key=key).exists())
        self.assertTrue(Session.objects.filter(session_key=self.other_client.session.session_key).exists())
        self.assertEqual(self.driver_client.get(reverse('dashboard')).status_code,302)

    def test_driver_cannot_end_sessions_and_admin_cannot_target_someone_elses_key(self):
        url=reverse('account_sessions_end',args=[self.driver.pk])
        entry=self.client.get(reverse('account_detail',args=[self.other.pk])).context['account_sessions'][0]
        self.assertEqual(self.client.post(url,{'session':entry['token']}).status_code,404)
        self.assertEqual(self.driver_client.post(url,{'session':'all'}).status_code,403)

    def test_end_all_and_password_invalidated_session_not_counted(self):
        second=Client(); second.force_login(self.driver)
        second.get(reverse('dashboard'))
        url=reverse('account_detail',args=[self.driver.pk])
        self.assertEqual(len(self.client.get(url).context['account_sessions']),2)
        self.driver.set_password('Different-password-456');self.driver.save(update_fields=['password'])
        self.assertEqual(len(self.client.get(url).context['account_sessions']),0)
        second.force_login(self.driver); second.get(reverse('dashboard'))
        key=second.session.session_key
        self.assertEqual(self.client.post(reverse('account_sessions_end',args=[self.driver.pk]),{'session':'all'}).status_code,302)
        self.assertFalse(Session.objects.filter(session_key=key).exists())

    def test_admin_may_end_own_session_and_is_logged_out(self):
        url=reverse('account_sessions_end',args=[self.admin.pk])
        response=self.client.post(url,{'session':'all'})
        self.assertRedirects(response,reverse('login'),fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('accounts')).status_code,302)
