from django.test import TestCase, override_settings
from django.urls import reverse
from fleet.models import AuditEvent, User
from unittest.mock import patch
import hashlib
from django.utils import timezone
from fleet.models import LoginThrottle, MailSettings


@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class CargoAdminUsersTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user('+380501234561', 'Test-Password-986!', role='admin', full_name='Логіст', must_change_password=False)
        cls.driver = User.objects.create_user('+380501234562', 'Test-Password-986!', full_name='Олена Тест', must_change_password=False)
        cls.other = User.objects.create_user('+380501234563', 'Test-Password-986!', full_name='Інший', must_change_password=False)
        AuditEvent.objects.create(actor=cls.driver, action='Вхід', detail='Старий текст')
        AuditEvent.objects.create(actor=cls.admin, action='Змінено активність користувача', detail=cls.driver.full_name)
        AuditEvent.objects.create(actor=cls.other, action='Вхід', detail='Інший запис')

    def setUp(self):
        self.client.force_login(self.admin)

    def test_search_users_by_name_or_phone(self):
        for q in ('Олена', '1234562'):
            response = self.client.get(reverse('accounts'), {'q': q})
            self.assertContains(response, 'Олена Тест')
            self.assertEqual(list(response.context['users']),[self.driver])

    def test_filters_combine_status_and_role(self):
        self.other.is_active = False
        self.other.save(update_fields=['is_active'])
        response = self.client.get(reverse('accounts'), {'state': 'inactive', 'role': 'driver'})
        self.assertContains(response, 'Інший')
        self.assertEqual(list(response.context['users']),[self.other])
        self.assertEqual(response.context['counts']['inactive'], 1)
        self.assertEqual(response.context['counts']['driver'], 2)

    def test_journal_filters_actor_and_action(self):
        response = self.client.get(reverse('admin_journal'), {'user': self.driver.pk, 'category': 'Вхід'})
        self.assertContains(response, 'Старий текст')
        self.assertNotContains(response, 'Інший запис')
        self.assertEqual(response.context['page_obj'].paginator.count, 1)

    def test_account_detail_history_and_guard(self):
        response = self.client.get(reverse('account_detail', args=[self.driver.pk]))
        self.assertContains(response, 'Старий текст')
        self.assertContains(response, 'Змінено активність користувача')
        self.assertNotContains(response, self.driver.password)
        self.client.force_login(self.driver)
        self.assertEqual(self.client.get(reverse('account_detail', args=[self.other.pk])).status_code, 403)

    def test_role_assignment_is_in_account_details(self):
        detail = reverse('account_detail', args=[self.driver.pk])
        self.assertContains(self.client.get(detail), 'Зберегти роль')
        self.assertNotContains(self.client.get(reverse('admin_roles')), 'Ролі команди')
        response = self.client.post(reverse('admin_role_update', args=[self.driver.pk]), {'role': 'admin', 'return_to': 'account'})
        self.assertRedirects(response, detail, fetch_redirect_response=False)
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.role, 'admin')
        self.assertTrue(AuditEvent.objects.filter(action='Змінено роль користувача').exists())

    def test_edit_user_contacts_and_validate_unique_phone(self):
        url = reverse('account_detail', args=[self.driver.pk])
        response = self.client.post(url, {'full_name': 'Олена Нова', 'phone': '0501234562', 'email': 'olena@example.com'})
        self.assertRedirects(response, url, fetch_redirect_response=False)
        self.driver.refresh_from_db()
        self.assertEqual((self.driver.full_name, self.driver.phone, self.driver.email), ('Олена Нова', '+380501234562', 'olena@example.com'))
        self.assertTrue(AuditEvent.objects.filter(action='Змінено дані користувача').exists())
        response = self.client.post(url, {'full_name': 'Олена', 'phone': self.other.phone, 'email': ''})
        self.assertEqual(response.status_code, 200)
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.full_name, 'Олена Нова')
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url, {'full_name': 'Зміна', 'phone': self.driver.phone}).status_code, 403)

    def test_split_names_and_reset_only_user_phone_bucket(self):
        url = reverse('account_detail', args=[self.driver.pk])
        response = self.client.post(url, {'surname': 'Коваль', 'given_name': 'Олена', 'patronymic': 'Іванівна', 'phone': self.driver.phone, 'email': ''})
        self.assertEqual(response.status_code, 302)
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.full_name, 'Коваль Олена Іванівна')
        phone_key = hashlib.sha256(('phone:' + self.driver.phone).encode()).hexdigest()
        ip_key = hashlib.sha256(b'ip:127.0.0.1').hexdigest()
        for key in (phone_key, ip_key): LoginThrottle.objects.create(key=key, failures=10, window_start=timezone.now())
        self.client.post(reverse('account_action', args=[self.driver.pk]), {'action': 'reset_attempts'})
        self.assertFalse(LoginThrottle.objects.filter(key=phone_key).exists())
        self.assertTrue(LoginThrottle.objects.filter(key=ip_key).exists())

    @patch('fleet.mail_service.send_mail')
    def test_email_reset_failure_preserves_password_and_invitation_does_not_reset(self, send):
        MailSettings.objects.create(enabled=True, site_url='https://example.test')
        self.driver.email = 'driver@example.test'
        self.driver.save(update_fields=['email'])
        action = reverse('account_action', args=[self.driver.pk])
        before = self.driver.password
        send.side_effect = RuntimeError('SMTP failed')
        self.client.post(action, {'action': 'email_password'})
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.password, before)
        send.side_effect = None
        self.client.post(action, {'action': 'email_invite'})
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.password, before)
        self.assertIn('https://example.test', send.call_args.args[3])
        self.client.post(action, {'action': 'email_password'})
        self.driver.refresh_from_db()
        self.assertNotEqual(self.driver.password, before)
        self.assertTrue(self.driver.must_change_password)

