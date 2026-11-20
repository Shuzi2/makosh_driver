from django.test import TestCase, override_settings
from django.urls import reverse
from fleet.models import User


@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False,
                   STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class DriverMenuMarkupTests(TestCase):
    def test_driver_menu_triggers_are_buttons_with_initially_hidden_panels(self):
        driver=User.objects.create_user('+380501111990',role='driver',full_name='Водій',must_change_password=False)
        self.client.force_login(driver)
        response=self.client.get(reverse('dashboard'))
        self.assertContains(response,'aria-controls="driver-settings-panel"')
        self.assertContains(response,'aria-controls="driver-notification-panel"')
        self.assertContains(response,'id="driver-settings-panel" data-driver-menu-panel hidden')
        self.assertContains(response,'class="driver-notifications-panel" data-driver-menu-panel hidden')
