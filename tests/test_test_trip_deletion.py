from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch
from django.core.files.base import ContentFile
from fleet.models import AuditEvent, Trip, User, Vehicle


@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False,
                   TEST_TRIP_DELETE_OWNER_ID=101,
                   TEST_TRIP_DELETE_OWNER_PHONE='+380501111001',
                   STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class TestTripDeletionTests(TestCase):
    def setUp(self):
        self.owner=User.objects.create_user('+380501111001',password='owner-test-password',pk=101,role='admin',full_name='Власник',must_change_password=False,can_delete_test_trips=True)
        self.admin=User.objects.create_user('+380501111002',role='admin',full_name='Інший',must_change_password=False)
        self.driver=User.objects.create_user('+380501111003',role='driver',full_name='Водій',must_change_password=False)
        self.vehicle=Vehicle.objects.create(plate='TESTDELETE',kind='tent',odometer_km=150540,odometer_updated_at=timezone.now())
        self.trip=Trip.objects.create(vehicle=self.vehicle,driver=self.driver,created_by=self.owner,origin='A',destination='B',cargo='C',planned_tons=1,planned_date=timezone.localdate(),status='completed',completed_at=timezone.now(),distance_km=540)
        AuditEvent.objects.create(trip=self.trip,action='Пробіг рейсу додано до одометра',detail='TESTDELETE: +540 км; одометр 150540 км')

    def test_only_configured_owner_can_access_delete(self):
        url=reverse('trip_delete_test',args=[self.trip.pk])
        for user in (self.admin,self.driver):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code,403)
            self.assertEqual(self.client.post(url,{'is_test':'yes','confirm':str(self.trip.pk),'password':'owner-test-password'}).status_code,403)
        self.assertTrue(Trip.objects.filter(pk=self.trip.pk).exists())

    def test_explicit_permission_works_without_environment_settings(self):
        self.owner.can_delete_test_trips = True
        self.owner.save(update_fields=['can_delete_test_trips'])
        self.client.force_login(self.owner)
        with override_settings(TEST_TRIP_DELETE_OWNER_ID='', TEST_TRIP_DELETE_OWNER_PHONE=''):
            self.assertEqual(self.client.get(reverse('trip_delete_test', args=[self.trip.pk])).status_code, 200)

    def test_only_superuser_can_grant_permission(self):
        url = reverse('account_test_trip_permission', args=[self.owner.pk])
        self.owner.can_delete_test_trips = False
        self.owner.save(update_fields=['can_delete_test_trips'])
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(url, {'enabled': '1'}).status_code, 403)
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.can_delete_test_trips)
        superuser = User.objects.create_superuser('+380501111004', password='test', full_name='Root')
        self.client.force_login(superuser)
        self.assertContains(self.client.get(reverse('account_detail', args=[self.owner.pk])), 'Надати дозвіл')
        self.assertEqual(self.client.post(url, {'enabled': '1'}).status_code, 302)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.can_delete_test_trips)
        self.assertEqual(self.client.post(url, {'enabled': '0'}).status_code, 302)
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.can_delete_test_trips)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse('trip_delete_test', args=[self.trip.pk])).status_code, 403)

    def test_deletion_requires_exact_number_and_account_password(self):
        self.client.force_login(self.owner)
        url=reverse('trip_delete_test',args=[self.trip.pk])
        for payload in ({'is_test':'yes','confirm':'wrong','password':'owner-test-password'},{'is_test':'yes','confirm':str(self.trip.pk),'password':'wrong'}):
            self.client.post(url,payload)
            self.assertTrue(Trip.objects.filter(pk=self.trip.pk).exists())
        self.client.post(url,{'confirm':str(self.trip.pk),'password':'owner-test-password'})
        self.assertTrue(Trip.objects.filter(pk=self.trip.pk).exists())

    def test_confirmed_trip_removed_and_safe_odometer_reverted(self):
        self.client.force_login(self.owner)
        response=self.client.post(reverse('trip_delete_test',args=[self.trip.pk]),{'is_test':'yes','confirm':str(self.trip.pk),'password':'owner-test-password'})
        self.assertEqual(response.status_code,302)
        self.assertFalse(Trip.objects.filter(pk=self.trip.pk).exists())
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.odometer_km,150000)
        self.assertTrue(AuditEvent.objects.filter(actor=self.owner,trip__isnull=True,action='Видалено тестовий рейс').exists())

    def test_later_odometer_update_is_not_overwritten(self):
        self.vehicle.odometer_km=151000;self.vehicle.odometer_updated_at=timezone.now()+timedelta(seconds=1)
        self.vehicle.save(update_fields=['odometer_km','odometer_updated_at'])
        self.client.force_login(self.owner)
        self.client.post(reverse('trip_delete_test',args=[self.trip.pk]),{'is_test':'yes','confirm':str(self.trip.pk),'password':'owner-test-password'})
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.odometer_km,151000)

    def test_documents_and_dependents_removed_without_touching_other_trip_or_archive(self):
        from fleet.models import Document, TripStop, TripExpense, TripEconomics, DriverRequest, DriverReportHistory, AttentionSkip
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            other=Trip.objects.create(vehicle=self.vehicle,driver=self.driver,created_by=self.owner,origin='OTHER',destination='C',cargo='C',planned_tons=1,planned_date=timezone.localdate(),status='completed',completed_at=timezone.now())
            archive=DriverReportHistory.objects.create(source_sheet='old',source_row=1,driver=self.driver,driver_label='Водій')
            stop=TripStop.objects.create(trip=self.trip,position=1,kind='load',address='A',cargo='C',planned_tons=1)
            doc=Document(trip=self.trip,stop=stop,kind='ttn',original_name='ttn.pdf',content_type='application/pdf',size=3,uploaded_by=self.driver)
            doc.file.save('ttn.pdf',ContentFile(b'PDF'),save=True)
            doc_path=Path(doc.file.path)
            expense=TripExpense.objects.create(trip=self.trip,driver=self.driver,category='other',amount=10,currency='UAH')
            expense.receipt.save('receipt.pdf',ContentFile(b'PDF'),save=True);receipt_path=Path(expense.receipt.path)
            TripEconomics.objects.create(trip=self.trip)
            DriverRequest.objects.create(id=uuid.uuid4(),user=self.driver,trip=self.trip,kind='status',fingerprint='x')
            AttentionSkip.objects.create(trip=self.trip,stop=stop,category='documents',episode='one',skipped_by=self.owner)
            self.client.force_login(self.owner)
            with self.captureOnCommitCallbacks(execute=True):
                response=self.client.post(reverse('trip_delete_test',args=[self.trip.pk]),{'is_test':'yes','confirm':str(self.trip.pk),'password':'owner-test-password'})
            self.assertEqual(response.status_code,302)
            self.assertFalse(doc_path.exists());self.assertFalse(receipt_path.exists())
            self.assertTrue(Trip.objects.filter(pk=other.pk).exists());self.assertTrue(DriverReportHistory.objects.filter(pk=archive.pk).exists())
            self.assertFalse(TripStop.objects.filter(pk=stop.pk).exists())

    def test_no_access_if_identity_configuration_missing(self):
        self.client.force_login(self.owner)
        self.owner.can_delete_test_trips = False
        self.owner.save(update_fields=['can_delete_test_trips'])
        with override_settings(TEST_TRIP_DELETE_OWNER_PHONE=''):
            self.assertEqual(self.client.get(reverse('trip_delete_test',args=[self.trip.pk])).status_code,403)

    def test_file_cleanup_failure_is_recorded_without_deleting_other_files(self):
        from fleet.models import Document
        doc=Document.objects.create(trip=self.trip,kind='ttn',file=f'trips/{self.trip.pk}/document.pdf',original_name='document.pdf',content_type='application/pdf',size=10,uploaded_by=self.driver)
        self.client.force_login(self.owner)
        with patch('django.core.files.storage.default_storage.delete',side_effect=OSError('storage unavailable')):
            with self.captureOnCommitCallbacks(execute=True):
                response=self.client.post(reverse('trip_delete_test',args=[self.trip.pk]),{'is_test':'yes','confirm':str(self.trip.pk),'password':'owner-test-password'})
        self.assertEqual(response.status_code,302)
        self.assertFalse(Trip.objects.filter(pk=self.trip.pk).exists())
        self.assertFalse(Document.all_objects.filter(pk=doc.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(action='Видалено тестовий рейс',detail__contains='Не вдалося видалити').exists())

    def test_only_owner_sees_button_and_deleted_trip_disappears_from_waybills(self):
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(reverse('trip_detail',args=[self.trip.pk])),'Видалити тестовий рейс')
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse('trip_detail',args=[self.trip.pk])),'Видалити тестовий рейс')
        self.assertContains(self.client.get(reverse('waybill_list')),f'№{self.trip.pk}')
        self.client.post(reverse('trip_delete_test',args=[self.trip.pk]),{'is_test':'yes','confirm':str(self.trip.pk),'password':'owner-test-password'})
        page=self.client.get(reverse('waybill_list'))
        self.assertNotIn(self.trip.pk,[trip.pk for trip in page.context['trips']])
