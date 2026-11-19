from decimal import Decimal
import tempfile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from fleet.models import User, Vehicle, VehicleRepair

@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class RepairCostTests(TransactionTestCase):
    def setUp(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        override = override_settings(MEDIA_ROOT=media.name)
        override.enable()
        self.addCleanup(override.disable)
        self.admin = User.objects.create_user('+380501001001', password='Test-password-123', role='admin', full_name='Адмін', must_change_password=False)
        self.driver = User.objects.create_user('+380501001002', password='Test-password-123', role='driver', full_name='Водій', must_change_password=False)
        self.vehicle = Vehicle.objects.create(plate='TEST1234', trailer_plate='TRAIL123', kind='tent', in_repair=True)
        self.repair = VehicleRepair.objects.create(vehicle=self.vehicle, reason='Гальма', created_by=self.admin)
        self.client.force_login(self.admin)

    def test_finish_repair_saves_cost_and_protects_document(self):
        receipt = SimpleUploadedFile('invoice.pdf', b'%PDF-1.4\nexample', content_type='application/pdf')
        response = self.client.post(reverse('vehicle_repair', args=[self.vehicle.pk]), {'operation':'finish', 'result':'Готово', 'amount':'1234.50', 'invoice': receipt})
        self.assertEqual(response.status_code, 302)
        self.repair.refresh_from_db()
        self.assertEqual(self.repair.amount, Decimal('1234.50'))
        self.assertTrue(self.repair.invoice)
        download = reverse('repair_document_download', args=[self.repair.pk, 'invoice'])
        response = self.client.get(download)
        self.assertEqual(response.status_code, 200)
        response.close()
        self.client.force_login(self.driver)
        self.assertEqual(self.client.get(download).status_code, 403)

    def test_reject_fake_file_and_negative_cost(self):
        response = self.client.post(reverse('vehicle_repair', args=[self.vehicle.pk]), {'operation':'finish', 'amount':'-1', 'invoice':SimpleUploadedFile('invoice.pdf', b'not a pdf')})
        self.assertEqual(response.status_code, 200)
        self.vehicle.refresh_from_db()
        self.assertTrue(self.vehicle.in_repair)

    def test_finish_without_amount_then_update_and_report(self):
        response = self.client.post(reverse('vehicle_repair', args=[self.vehicle.pk]), {'operation':'finish', 'result':'Готово'})
        self.assertEqual(response.status_code, 302)
        self.repair.refresh_from_db()
        self.assertIsNone(self.repair.amount)
        url = reverse('repair_costs_edit', args=[self.repair.pk])
        response = self.client.post(url, {'amount':'480.25', 'act':SimpleUploadedFile('act.pdf', b'%PDF-1.4\nexample')})
        self.assertEqual(response.status_code, 302)
        self.repair.refresh_from_db()
        self.assertEqual(self.repair.amount, Decimal('480.25'))
        self.assertTrue(self.repair.act)
        response = self.client.get(reverse('repair_report'))
        self.assertContains(response, '480,25')
        self.assertContains(response, self.vehicle.plate)
        self.assertEqual(self.client.get(reverse('repair_report'), {'format':'csv'}).status_code, 200)
