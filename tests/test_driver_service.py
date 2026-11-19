from django.test import TestCase, override_settings
from django.urls import reverse
from fleet.models import User, Vehicle, DriverServiceRequest, AuditEvent, MaintenanceItem, Trip
from django.utils import timezone


@override_settings(SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False,CSRF_COOKIE_SECURE=False,STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class DriverServiceTests(TestCase):
    def setUp(self):
        self.driver=User.objects.create_user('+380501212121',full_name='Водій Один',role='driver',must_change_password=False)
        self.other=User.objects.create_user('+380501212122',full_name='Водій Два',role='driver',must_change_password=False)
        self.manager=User.objects.create_user('+380501212123',full_name='Логіст',role='admin',must_change_password=False)
        self.vehicle=Vehicle.objects.create(plate='SERVICE1',trailer_plate='TRAILER1',kind='tent',default_driver=self.driver)
        self.client.force_login(self.driver)

    def create_request(self):
        url=reverse('driver_service_create');page=self.client.get(url)
        data={'request_key':page.context['form'].initial['request_key'],'category':'problem','title':'Не працює пічка','description':'Потрібна діагностика','parts':'Мотор пічки','urgent':'on','due_date':'2026-12-01','due_km':'150000'}
        result=self.client.post(url,data);self.assertEqual(result.status_code,302)
        return DriverServiceRequest.objects.get(),data

    def test_driver_creates_and_logistician_updates_request(self):
        from html import unescape
        import re
        item,data=self.create_request()
        self.assertEqual((item.tractor_plate,item.trailer_plate,item.status),('SERVICE1','TRAILER1','new'))
        self.assertFalse(self.vehicle.in_repair)
        self.assertContains(self.client.get(reverse('driver_service')),'Не працює пічка')
        self.client.force_login(self.manager)
        page=self.client.get(reverse('manager_messages'))
        self.assertContains(page,'Не працює пічка');self.assertContains(page,'Мотор пічки')
        self.assertEqual(page.context['service_nav_count'],1)
        self.assertEqual(self.client.get(reverse('attention_count')).json()['service_count'],1)
        revision=unescape(re.search(r'name="revision" value="([^"]+)"',page.content.decode()).group(1))
        response=self.client.post(reverse('manager_service_update',args=[item.pk]),{'revision':revision,'status':'in_progress','manager_note':'Запчастини замовлено'})
        self.assertEqual(response.status_code,302);item.refresh_from_db();self.assertEqual(item.status,'in_progress')
        history=self.client.get(reverse('manager_messages')).context['items'][0].message_history
        self.assertEqual([entry['text'] for entry in history],['Водій надіслав звернення','Стан змінено: У роботі','Логіст залишив або змінив відповідь'])
        self.assertEqual(history[1]['actor'],'Логіст')
        self.client.force_login(self.driver)
        self.assertContains(self.client.get(reverse('driver_service')),'Запчастини замовлено')
        inbox=self.client.get(reverse('driver_notifications')).json()
        self.assertEqual(inbox['unread'],1)
        self.assertEqual(inbox['items'][0]['type'],'service')
        self.assertIn('#service-',inbox['items'][0]['url'])
        notice_id=inbox['items'][0]['id']
        self.client.force_login(self.other)
        self.assertFalse(self.client.post(reverse('driver_notification_read'),{'id':notice_id}).json()['updated'])
        self.client.force_login(self.driver)
        self.assertTrue(self.client.post(reverse('driver_notification_read'),{'id':notice_id}).json()['updated'])
        self.assertEqual(self.client.get(reverse('driver_notifications')).json()['unread'],0)
        self.assertTrue(AuditEvent.objects.filter(action='Оновлено сервісний запис водія').exists())

    def test_manager_message_filters_and_completed_history(self):
        item,_=self.create_request()
        self.client.force_login(self.manager);url=reverse('manager_messages')
        self.assertContains(self.client.get(url,{'q':'Мотор'}),'Не працює пічка')
        self.assertNotContains(self.client.get(url,{'q':'Невідоме'}),'Не працює пічка')
        self.client.post(reverse('manager_service_update',args=[item.pk]),{'revision':item.updated_at.isoformat(),'status':'done','manager_note':'Замінено'})
        self.assertNotContains(self.client.get(url),'Не працює пічка')
        self.assertContains(self.client.get(url,{'status':'done'}),'Не працює пічка')
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code,403)

    def test_status_change_without_reply_notifies_driver_once(self):
        from fleet.models import DriverServiceNotification
        item,_=self.create_request()
        self.client.force_login(self.manager)
        update=reverse('manager_service_update',args=[item.pk])
        self.client.post(update,{'revision':item.updated_at.isoformat(),'status':'in_progress','manager_note':''})
        item.refresh_from_db()
        notice=DriverServiceNotification.objects.get(service_request=item)
        self.assertEqual(notice.message,'Стан: У роботі')
        self.client.post(update,{'revision':item.updated_at.isoformat(),'status':'in_progress','manager_note':''})
        self.assertEqual(DriverServiceNotification.objects.filter(service_request=item).count(),1)
        self.client.force_login(self.driver)
        inbox=self.client.get(reverse('driver_notifications')).json()
        self.assertEqual(inbox['items'][0]['message'],'Стан: У роботі')
        self.assertTrue(self.client.post(reverse('driver_notification_read'),{'id':inbox['items'][0]['id']}).json()['updated'])
        self.assertEqual(self.client.get(reverse('driver_notifications')).json()['unread'],0)
        self.assertEqual(len(self.client.get(reverse('driver_notifications')).json()['items']),1)

    def test_retry_is_idempotent_and_other_drivers_cannot_read_or_edit(self):
        item,data=self.create_request()
        self.client.post(reverse('driver_service_create'),data)
        self.assertEqual(DriverServiceRequest.objects.count(),1)
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(reverse('driver_service')),'Не працює пічка')
        self.assertEqual(self.client.get(reverse('driver_service_edit',args=[item.pk])).status_code,404)
        self.assertEqual(self.client.post(reverse('manager_service_update',args=[item.pk]),{}).status_code,403)

    def test_service_date_picker_and_date_saved(self):
        page=self.client.get(reverse('driver_service_create'))
        self.assertContains(page,'data-service-date-picker')
        self.assertContains(page,'type="date"')
        item,_=self.create_request()
        self.assertEqual(item.due_date.isoformat(),'2026-12-01')

    def test_driver_edits_and_stale_manager_update_does_not_overwrite(self):
        item,_=self.create_request();edit=reverse('driver_service_edit',args=[item.pk])
        revision=item.updated_at.isoformat()
        data={'revision':revision,'category':'service','title':'Заміна мастила','description':'Планове ТО','due_km':'155000'}
        self.assertEqual(self.client.post(edit,data).status_code,302)
        item.refresh_from_db();self.assertEqual(item.title,'Заміна мастила')
        self.client.force_login(self.manager)
        self.client.post(reverse('manager_service_update',args=[item.pk]),{'revision':revision,'status':'done','manager_note':'Готово'})
        item.refresh_from_db();self.assertEqual(item.status,'new')

    def test_unassigned_driver_cannot_create_and_sees_no_vehicle(self):
        self.client.force_login(self.other)
        self.assertContains(self.client.get(reverse('driver_service')),'Автомобіль не закріплено')
        self.assertEqual(self.client.get(reverse('driver_service_create')).status_code,302)
        self.assertEqual(DriverServiceRequest.objects.count(),0)

    def test_driver_can_see_scheduled_maintenance_for_assigned_vehicle(self):
        MaintenanceItem.objects.create(vehicle=self.vehicle,kind='service',title='Планова заміна мастила',due_km=150000,created_by=self.manager)
        self.assertContains(self.client.get(reverse('driver_service')),'Планова заміна мастила')

    def test_current_trip_vehicle_wins_over_default_vehicle(self):
        temporary=Vehicle.objects.create(plate='SERVICE2',kind='grain')
        Trip.objects.create(vehicle=temporary,driver=self.driver,created_by=self.manager,origin='А',destination='Б',cargo='Вантаж',planned_tons=10,planned_date=timezone.localdate(),status='road')
        item,_=self.create_request()
        self.assertEqual(item.vehicle_id,temporary.pk)
        self.assertEqual(item.tractor_plate,'SERVICE2')

    def test_moderator_without_fleet_edit_right_cannot_update_request(self):
        from fleet.permissions_models import ModeratorPermissionSettings
        item,_=self.create_request()
        moderator=User.objects.create_user('+380501212124',full_name='Модератор',role='moderator',must_change_password=False)
        ModeratorPermissionSettings.objects.create(pk=1,view_fleet=True,edit_fleet=False)
        self.client.force_login(moderator)
        response=self.client.post(reverse('manager_service_update',args=[item.pk]),{'revision':item.updated_at.isoformat(),'status':'done'})
        self.assertEqual(response.status_code,403)
        item.refresh_from_db();self.assertEqual(item.status,'new')
