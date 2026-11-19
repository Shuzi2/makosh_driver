from django.test import TestCase, override_settings
from django.urls import reverse
from fleet.models import User, ModeratorPermissionSettings

@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, STORAGES={'default': {'BACKEND':'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ModeratorAccessTests(TestCase):
    def setUp(self):
        self.moderator = User.objects.create_user('+380501000001', password='Example-password-123', role='moderator', full_name='Модератор', must_change_password=False)
        self.client.force_login(self.moderator)

    def test_moderator_cannot_access_administration(self):
        for name in ('admin_overview', 'admin_journal', 'admin_roles', 'admin_maintenance', 'accounts', 'account_create'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_disabling_trip_access_denies_direct_requests(self):
        ModeratorPermissionSettings.objects.create(view_trips=False)
        self.assertEqual(self.client.get(reverse('trip_list')).status_code, 403)

    def test_dashboard_and_trips_do_not_expose_documents_when_disabled(self):
        ModeratorPermissionSettings.objects.create(view_documents=False)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 403)
        self.assertEqual(self.client.get(reverse('trip_list')).status_code, 403)
