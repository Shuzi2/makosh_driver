from datetime import timedelta
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from fleet.models import User, Vehicle, Trip, MaintenanceItem, MaintenanceNotification
from fleet.maintenance_notifications import scan_due


@override_settings(SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False,CSRF_COOKIE_SECURE=False,STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class DriverMaintenanceTests(TestCase):
    def setUp(self):
        self.driver=User.objects.create_user('+380501111001',full_name='Водій',role='driver',must_change_password=False)
        self.manager=User.objects.create_user('+380501111002',full_name='Логіст',role='admin',must_change_password=False)
        self.other=User.objects.create_user('+380501111003',full_name='Інший',role='driver',must_change_password=False)
        self.vehicle=Vehicle.objects.create(plate='TEST001',kind='tent',default_driver=self.driver,odometer_km=0)
        self.client.force_login(self.driver)

    def test_driver_updates_odometer_only_between_trips(self):
        url=reverse('driver_maintenance_odometer')
        self.assertEqual(self.client.post(url,{'odometer':'650000','revision':''}).status_code,302)
        self.vehicle.refresh_from_db();self.assertEqual(self.vehicle.odometer_km,650000)
        Trip.objects.create(vehicle=self.vehicle,driver=self.driver,created_by=self.manager,origin='А',destination='Б',cargo='Товар',planned_tons=1,planned_date=timezone.localdate(),status='road')
        response=self.client.post(url,{'odometer':'651000','revision':self.vehicle.odometer_updated_at.isoformat()})
        self.assertContains(response,'після завершення поточного рейсу')
        self.vehicle.refresh_from_db();self.assertEqual(self.vehicle.odometer_km,650000)

    def test_completed_trip_increments_actual_start_once(self):
        from types import SimpleNamespace
        from fleet.finish_details import save_finish_details
        self.client.post(reverse('driver_maintenance_odometer'),{'odometer':'650000','revision':''})
        trip=Trip.objects.create(vehicle=self.vehicle,driver=self.driver,created_by=self.manager,origin='А',destination='Б',cargo='Товар',planned_tons=1,planned_date=timezone.localdate(),status='road')
        form=SimpleNamespace(cleaned_data={'empty_distance_km':100,'loaded_distance_km':600,'distance_km':700,'fuel_used_l':200},fields={})
        save_finish_details(trip,form,self.driver);save_finish_details(trip,form,self.driver)
        self.vehicle.refresh_from_db();self.assertEqual(self.vehicle.odometer_km,650700)

    def test_plan_visible_to_logistician_and_only_assigned_driver_can_edit(self):
        url=reverse('driver_maintenance_create')
        with self.captureOnCommitCallbacks(execute=True):
            response=self.client.post(url,{'title':'Мастило','applies_to':'tractor','due_date':(timezone.localdate()+timedelta(days=5)).isoformat(),'due_km':'100000'})
        self.assertEqual(response.status_code,302)
        item=MaintenanceItem.objects.get(title='Мастило')
        self.assertEqual((item.reminder_days,item.reminder_km),(7,1000))
        self.assertContains(self.client.get(reverse('driver_service')),'Мастило')
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('driver_maintenance_edit',args=[item.pk])).status_code,403)
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse('maintenance')),'Мастило')
        self.assertIn('Мастило',self.client.get(reverse('ttn_notifications')).json()['items'][0]['kind'])

    def test_reminder_is_deduplicated_and_readable(self):
        item=MaintenanceItem.objects.create(vehicle=self.vehicle,kind='service',title='ТО мастило',due_date=timezone.localdate()+timedelta(days=7),reminder_days=7,created_by=self.manager)
        with self.captureOnCommitCallbacks(execute=True):
            scan_due();scan_due()
        self.assertEqual(MaintenanceNotification.objects.filter(item=item,recipient=self.manager).count(),1)
        self.client.force_login(self.manager)
        notice=MaintenanceNotification.objects.get(item=item,recipient=self.manager)
        self.assertEqual(self.client.post(reverse('ttn_notification_read'),{'id':f'maintenance:{notice.pk}'}).json()['updated'],True)

    def test_complete_keeps_history_and_next_service(self):
        item=MaintenanceItem.objects.create(vehicle=self.vehicle,kind='service',title='ТО',due_km=100000,created_by=self.manager)
        url=reverse('driver_maintenance_complete',args=[item.pk])
        with self.captureOnCommitCallbacks(execute=True):
            response=self.client.post(url,{'revision':item.updated_at.isoformat(),'vehicle_revision':'','completed_on':timezone.localdate().isoformat(),'odometer':'0','result':'Заміна','next_due_km':'120000'})
        self.assertEqual(response.status_code,302)
        item.refresh_from_db();self.assertIsNotNone(item.completed_at)
        self.assertEqual(item.next_item.due_km,120000)
