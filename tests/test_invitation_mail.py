from unittest.mock import patch
from django.test import TestCase, override_settings
from django.urls import reverse
from fleet.models import User, MailSettings

@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, STORAGES={'default': {'BACKEND':'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class InvitationMailTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('+380501111111', password='Example-password-123', role='admin', full_name='Адмін', must_change_password=False)
        self.client.force_login(self.admin)
    def test_temporary_user_creation(self):
        response = self.client.post(reverse('account_create'), {'full_name':'Тест Водій','phone':'0502222222','role':'driver','access_method':'password'})
        self.assertContains(response, 'Тимчасовий пароль')
        self.assertTrue(User.objects.filter(phone='+380502222222').exists())
    def test_smtp_password_is_encrypted_and_not_rendered(self):
        response = self.client.post(reverse('admin_mail'), {'enabled':'on','host':'smtp.example.test','port':'587','security':'starttls','username':'mail@example.test','password':'Not-a-real-secret-123','from_email':'mail@example.test','from_name':'Тест','site_url':'https://example.test'})
        self.assertEqual(response.status_code, 302)
        config = MailSettings.objects.get(pk=1)
        self.assertNotIn('Not-a-real-secret-123', config.password_cipher)
        self.assertNotContains(self.client.get(reverse('admin_mail')), 'Not-a-real-secret-123')
    @patch('fleet.mail_service.send_mail')
    def test_invite_is_one_use(self, send):
        MailSettings.objects.create(enabled=True, site_url='https://example.test')
        self.client.post(reverse('account_create'), {'full_name':'Тест Водій','phone':'0502222222','email':'user@example.test','role':'driver','access_method':'invite'})
        self.assertEqual(send.call_count, 1)
        link = send.call_args.args[3].split('https://example.test')[1].split()[0]
        self.client.logout()
        self.assertEqual(self.client.get(link).status_code, 200)
        response = self.client.post(link, {'new_password1':'Fresh-Password-2026!', 'new_password2':'Fresh-Password-2026!'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(link).status_code, 400)
