from django.test import TestCase, override_settings
from django.urls import reverse
from fleet.models import FleetCopySettings, User, Vehicle, Trailer


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

    def test_dimensions_are_saved_for_tractor_and_independent_trailer(self):
        vehicle=Vehicle.objects.get(plate='COPY123')
        trailer=Trailer.objects.create(plate='TRAIL123',kind='tent')
        vehicle.trailer_plate=trailer.plate;vehicle.save()
        url=reverse('admin_copy_data')
        values={'brand':'SCANIA','model_name':'R450','vin':'TRACTOR123','length_mm':'6000','width_mm':'2550','height_mm':'3850','empty_weight_kg':'8075','max_weight_kg':'19000'}
        self.assertEqual(self.client.post(url,{'action':'tractor','vehicle_id':vehicle.pk,**{f'tractor-{vehicle.pk}-{k}':v for k,v in values.items()}}).status_code,302)
        trailer_values={'brand':'SCHMITZ','model_name':'S01','vin':'TRAILER456','length_mm':'10500','width_mm':'2500','height_mm':'3800','empty_weight_kg':'6600','max_weight_kg':'42000'}
        self.assertEqual(self.client.post(url,{'action':'trailer','trailer_id':trailer.pk,**{f'trailer-{trailer.pk}-{k}':v for k,v in trailer_values.items()}}).status_code,302)
        vehicle.refresh_from_db();trailer.refresh_from_db()
        self.assertEqual(vehicle.length_mm,6000);self.assertEqual(vehicle.empty_weight_kg,8075);self.assertEqual(vehicle.max_weight_kg,19000)
        self.assertEqual(trailer.length_mm,10500);self.assertEqual(trailer.empty_weight_kg,6600);self.assertEqual(trailer.max_weight_kg,42000)
        self.assertEqual((vehicle.brand,vehicle.model_name,vehicle.vin),('SCANIA','R450','TRACTOR123'))
        self.assertEqual((trailer.brand,trailer.model_name,trailer.vin),('SCHMITZ','S01','TRAILER456'))
        page=self.client.get(reverse('dashboard'))
        self.assertContains(page,'data-copy-tractor-length="6000"')
        self.assertContains(page,'data-copy-trailer-length="10500"')
        self.assertContains(page,'data-copy-tractor-model="R450"')
        self.assertContains(page,'data-copy-tractor-vin="TRACTOR123"')
        self.assertContains(page,'data-copy-trailer-brand="SCHMITZ"')
        self.assertContains(page,'data-copy-trailer-vin="TRAILER456"')
        invalid={**values,'empty_weight_kg':'19001'}
        response=self.client.post(url,{'action':'tractor','vehicle_id':vehicle.pk,**{f'tractor-{vehicle.pk}-{k}':v for k,v in invalid.items()}})
        self.assertEqual(response.status_code,200)
        vehicle.refresh_from_db();self.assertEqual(vehicle.empty_weight_kg,8075)
        other=Trailer.objects.create(plate='TRAIL456',kind='tent',length_mm=11000)
        vehicle.trailer_plate=other.plate;vehicle.save()
        page=self.client.get(reverse('dashboard'))
        self.assertContains(page,'data-copy-trailer-length="11000"')
        trailer.refresh_from_db();self.assertEqual(trailer.length_mm,10500)
        self.client.force_login(self.driver)
        self.assertEqual(self.client.post(url,{'action':'trailer','trailer_id':trailer.pk,**values}).status_code,403)
