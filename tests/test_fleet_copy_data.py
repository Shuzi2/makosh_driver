from django.test import TestCase, override_settings
from django.urls import reverse
from fleet.models import FleetCopySettings, User, Vehicle


@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False,
    STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class FleetCopyDataTests(TestCase):
    def setUp(self):
        self.admin=User.objects.create_user('+380501100001',role='admin',full_name='Адміністратор',must_change_password=False)
        self.driver=User.objects.create_user('+380501100002',role='driver',full_name='Тест Водій',must_change_password=False)
        self.other=User.objects.create_user('+380501100003',role='driver',full_name='Інший водій',must_change_password=False)
        Vehicle.objects.create(plate='COPY123',kind='tent',default_driver=self.driver)
        self.client.force_login(self.admin)

    def test_admin_can_save_copy_details_without_overwriting_other_drivers(self):
        url=reverse('admin_copy_data')
        self.assertEqual(self.client.get(url).status_code,200)
        self.assertEqual(self.client.post(url,{'action':'settings','carrier':'Тест перевізник','storage_address':'Тест адреса'}).status_code,302)
        self.assertEqual(self.client.post(url,{'action':'driver','driver_id':self.driver.pk,'driver_license_number':'TEST 123456','passport_number':'TEST 654321'}).status_code,302)
        self.driver.refresh_from_db();self.other.refresh_from_db()
        self.assertEqual(self.driver.driver_license_number,'TEST 123456');self.assertEqual(self.driver.passport_number,'TEST 654321')
        self.assertEqual(self.other.driver_license_number,'');self.assertEqual(self.other.passport_number,'')
        self.assertEqual(FleetCopySettings.objects.get(pk=1).carrier,'Тест перевізник')
        page=self.client.get(reverse('dashboard'))
        self.assertContains(page,'data-copy-license="TEST 123456"');self.assertContains(page,'data-copy-carrier="Тест перевізник"')
        live=self.client.get(reverse('dashboard')+'?live=1').json()
        self.assertEqual(live['copy_carrier'],'Тест перевізник')

    def test_driver_cannot_view_or_edit_admin_copy_data(self):
        self.client.force_login(self.driver)
        self.assertEqual(self.client.get(reverse('admin_copy_data')).status_code,403)
        self.assertEqual(self.client.post(reverse('admin_copy_data'),{'action':'driver','driver_id':self.other.pk,'passport_number':'TEST'}).status_code,403)
        self.assertNotContains(self.client.get(reverse('dashboard')),'data-copy-passport')
