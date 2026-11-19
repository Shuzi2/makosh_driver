from datetime import timedelta
from decimal import Decimal
import tempfile
from django.test import TestCase,override_settings
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from fleet.models import User,Vehicle,Trip,TripStop,Document,MaintenanceItem,SavedPlace,AuditEvent
from fleet.places import remember_stop
from fleet.maintenance import add_months


@override_settings(SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False,CSRF_COOKIE_SECURE=False,STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class WorkspaceFeaturesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin=User.objects.create_user('+380509700001',role='admin',full_name='Логіст',must_change_password=False)
        cls.driver=User.objects.create_user('+380509700002',full_name='Водій',must_change_password=False)
        cls.other=User.objects.create_user('+380509700003',full_name='Інший',must_change_password=False)
        cls.vehicle=Vehicle.objects.create(plate='FEATURE1',trailer_plate='FEATURE2',kind='tent',default_driver=cls.driver)
    def setUp(self):
        self.media=tempfile.TemporaryDirectory();self.addCleanup(self.media.cleanup)
        settings=override_settings(MEDIA_ROOT=self.media.name);settings.enable();self.addCleanup(settings.disable)
        self.client.force_login(self.admin)
    def trip(self,**extra):
        extra.setdefault('status','completed')
        if extra['status']=='completed': extra.setdefault('completed_at',timezone.now())
        return Trip.objects.create(vehicle=self.vehicle,driver=self.driver,origin='Вінниця',destination='Гайсин',cargo='Добрива',planned_tons=24,planned_date=timezone.localdate(),created_by=self.admin,**extra)
    def stop(self,trip=None):
        return TripStop.objects.create(trip=trip or self.trip(),position=1,kind='load',address='м. Вінниця, Вінницька громада, Вінницький район, Вінницька область',settlement_name='м. Вінниця',region='Вінницька область',district='Вінницький район',community='Вінницька громада',cargo='Добрива',planned_tons=24,lat='49.233083',lon='28.468217',contact_name='Склад',contact_phone='+380501234567',notes='Другі ворота')
    def analytics(self,**extra):
        today=timezone.localdate().isoformat()
        return self.client.get(reverse('analytics'),{'start':today,'end':today,**extra})
    def test_fleet_categories_filter_cards_metrics_and_live_refresh(self):
        grain=Vehicle.objects.create(plate='GRAIN001',trailer_plate='GRAIN002',kind='grain',in_repair=True)
        self.trip(status='planned')
        url=reverse('dashboard')
        all_view=self.client.get(url)
        self.assertEqual(all_view.context['vehicle_count'],2)
        self.assertContains(all_view,'?kind=tent'); self.assertContains(all_view,'?kind=grain')
        tent=self.client.get(url,{'kind':'tent'})
        self.assertEqual(tent.context['vehicle_count'],1); self.assertEqual(tent.context['active_count'],1); self.assertEqual(tent.context['repair_count'],0)
        self.assertContains(tent,self.vehicle.plate); self.assertNotContains(tent,grain.plate)
        grain_view=self.client.get(url,{'kind':'grain'})
        self.assertEqual(grain_view.context['vehicle_count'],1); self.assertEqual(grain_view.context['active_count'],0); self.assertEqual(grain_view.context['repair_count'],1)
        self.assertContains(grain_view,grain.plate); self.assertNotContains(grain_view,self.vehicle.plate)
        live=self.client.get(url,{'kind':'grain','live':'1'}).json()
        self.assertIn(grain.plate,live['cards']); self.assertNotIn(self.vehicle.plate,live['cards'])
        self.client.force_login(self.driver)
        self.assertNotContains(self.client.get(url),'?kind=grain')
    def item(self,**extra):
        return MaintenanceItem.objects.create(vehicle=self.vehicle,kind='service',title='Заміна мастила',due_date=timezone.localdate()+timedelta(days=10),created_by=self.admin,**extra)
    def complete_data(self,item,**extra):
        item.refresh_from_db();self.vehicle.refresh_from_db()
        return {'revision':item.updated_at.isoformat(),'vehicle_revision':self.vehicle.odometer_updated_at.isoformat() if self.vehicle.odometer_updated_at else '', 'completed_on':timezone.localdate().isoformat(),'result':'Замінено',**extra}

    def test_weighted_fuel_rate_uses_only_paired_completed_trips(self):
        now=timezone.now()
        self.trip(status='completed',completed_at=now,distance_km=100,fuel_used_l=50)
        self.trip(status='completed',completed_at=now,distance_km=900,fuel_used_l=270)
        self.trip(status='completed',completed_at=now,distance_km=500)
        self.trip(status='completed',completed_at=now,fuel_used_l=100)
        self.trip(status='completed',completed_at=now,distance_km=0,fuel_used_l=20)
        self.trip(status='queued',distance_km=100,fuel_used_l=100)
        response=self.analytics();self.assertEqual(response.status_code,200)
        total=response.context['total']
        self.assertEqual(total['rate'],Decimal('32.00'));self.assertEqual(total['paired'],2)
        self.assertEqual(total['km'],1500);self.assertEqual(total['fuel'],440)
        self.assertEqual(total['missing_km'],2);self.assertEqual(total['missing_fuel'],1)
        self.assertEqual(response.context['vehicles'][0]['rate'],Decimal('32.00'))
    def test_zero_fuel_is_valid_empty_fuel_is_unknown(self):
        self.trip(status='completed',completed_at=timezone.now(),distance_km=100,fuel_used_l=0)
        self.assertEqual(self.analytics().context['total']['rate'],0)
    def test_search_matching_multiple_stops_does_not_double_totals(self):
        trip=self.trip(status='completed',completed_at=timezone.now(),distance_km=100,fuel_used_l=30,route_enabled=True)
        self.stop(trip);TripStop.objects.create(trip=trip,position=2,kind='unload',address='Вінниця',cargo='Добрива',planned_tons=24)
        total=self.analytics(q='Вінниця').context['total'];self.assertEqual(total['km'],100);self.assertEqual(total['trips'],1)
    def test_analytics_period_csv_and_empty_invalid_range(self):
        self.trip(status='completed',completed_at=timezone.now()-timedelta(days=2),distance_km=100,fuel_used_l=30)
        self.assertEqual(self.analytics().context['total']['trips'],0)
        self.assertEqual(self.analytics(start='2030-01-01',end='2020-01-01').context['total']['trips'],0)
        self.assertIn('text/csv',self.analytics(format='csv')['Content-Type'])
    def test_driver_cannot_access_staff_features(self):
        item=self.item();place=remember_stop(self.stop(),self.admin)
        self.client.force_login(self.driver)
        urls=[('analytics',[]),('saved_places',[]),('saved_place_search',[]),('saved_place_create',[]),('saved_place_edit',[place.pk]),('saved_place_toggle',[place.pk]),('maintenance',[]),('maintenance_create',[]),('maintenance_edit',[item.pk]),('maintenance_complete',[item.pk]),('vehicle_odometer',[self.vehicle.pk])]
        for name,args in urls:
            with self.subTest(name=name): self.assertEqual(self.client.get(reverse(name,args=args)).status_code,403)
    def test_remember_stop_keeps_all_area_contact_and_pin_data(self):
        stop=self.stop();place=remember_stop(stop,self.admin)
        for field in ['address','settlement_name','region','district','community','lat','lon','contact_name','contact_phone','notes']: self.assertEqual(getattr(place,field),getattr(stop,field))
        self.assertEqual(remember_stop(stop,self.admin).pk,place.pk);self.assertEqual(SavedPlace.objects.count(),1)
        place.address='Інша адреса';place.contact_name='Інший контакт';place.save();stop.refresh_from_db()
        self.assertEqual(stop.contact_name,'Склад');self.assertIn('Вінниця',stop.address)
    def test_saved_place_search_and_archive_do_not_delete_old_trip_data(self):
        stop=self.stop();place=remember_stop(stop,self.admin)
        result=self.client.get(reverse('saved_place_search'),{'q':'Вінниця','kind':'load'}).json()['results']
        self.assertEqual(result[0]['lat'],'49.233083');self.assertEqual(result[0]['community'],'Вінницька громада')
        self.assertEqual(self.client.get(reverse('saved_place_toggle',args=[place.pk])).status_code,405)
        self.client.post(reverse('saved_place_toggle',args=[place.pk]),{'active':'0'})
        self.assertEqual(self.client.get(reverse('saved_place_search')).json()['results'],[]);self.assertTrue(TripStop.objects.filter(pk=stop.pk).exists())
        self.client.post(reverse('saved_place_toggle',args=[place.pk]),{'active':'1'})
        self.assertEqual(len(self.client.get(reverse('saved_place_search')).json()['results']),1)
    def test_saved_place_form_validates_area_and_stale_edits(self):
        from fleet.places import SavedPlaceForm
        place=remember_stop(self.stop(),self.admin)
        data={f:getattr(place,f) if getattr(place,f) is not None else '' for f in SavedPlaceForm.Meta.fields};data['revision']=place.updated_at.isoformat()
        form=SavedPlaceForm(data,instance=place);self.assertTrue(form.is_valid(),form.errors)
        place.notes='Нова примітка';place.save()
        self.assertFalse(SavedPlaceForm(data,instance=place).is_valid())
        data.update(address='Невідоме село',region='',district='',community='');data.pop('revision')
        self.assertFalse(SavedPlaceForm(data).is_valid())
    def test_saved_place_create_edit_templates_render(self):
        place=remember_stop(self.stop(),self.admin)
        for name,args in [('saved_places',[]),('saved_place_create',[]),('saved_place_edit',[place.pk]),('maintenance',[]),('maintenance_create',[])]:
            with self.subTest(name=name): self.assertEqual(self.client.get(reverse(name,args=args)).status_code,200)
    def test_maintenance_deadlines_and_missing_odometer(self):
        item=self.item(due_km=10000)
        item.due_date=None;self.assertEqual(item.status_info()['key'],'unknown')
        self.vehicle.odometer_km=9500;item.vehicle=self.vehicle
        self.assertEqual(item.status_info()['key'],'soon')
        self.vehicle.odometer_km=10001;self.assertEqual(item.status_info()['key'],'overdue')
        self.vehicle.odometer_km=100;item.due_date=timezone.localdate()-timedelta(days=1)
        self.assertEqual(item.status_info()['key'],'overdue')
        item.due_date=timezone.localdate()+timedelta(days=31);self.assertEqual(item.status_info()['key'],'ok')

    def test_vehicle_maintenance_overview_distinguishes_date_mileage_and_missing_plans(self):
        other=Vehicle.objects.create(plate='NOMAIN1',trailer_plate='NOMAIN2',kind='grain')
        day=timezone.localdate()
        MaintenanceItem.objects.create(vehicle=self.vehicle,kind='service',title='ТО за датою',due_date=day+timedelta(days=8),created_by=self.admin)
        MaintenanceItem.objects.create(vehicle=self.vehicle,kind='service',title='ТО за пробігом',due_km=12000,created_by=self.admin)
        MaintenanceItem.objects.create(vehicle=self.vehicle,kind='document',title='Страхування',due_date=day+timedelta(days=1),created_by=self.admin)
        response=self.client.get(reverse('maintenance'))
        overview={entry['vehicle'].pk:entry for entry in response.context['service_overview']}
        self.assertEqual(overview[self.vehicle.pk]['by_date']['item'].title,'ТО за датою')
        self.assertEqual(overview[self.vehicle.pk]['by_km']['item'].title,'ТО за пробігом')
        self.assertIsNone(overview[self.vehicle.pk]['by_km']['state']['km'])
        self.assertEqual(overview[other.pk]['count'],0)
        self.assertContains(response,'ТО ще не заплановано')
        filtered=self.client.get(reverse('maintenance'),{'vehicle':str(other.pk)})
        self.assertEqual([entry['vehicle'].pk for entry in filtered.context['service_overview']],[other.pk])

    def test_maintenance_shows_active_and_completed_repairs(self):
        from fleet.models import VehicleRepair
        self.vehicle.in_repair=True;self.vehicle.save(update_fields=['in_repair'])
        repair=VehicleRepair.objects.create(vehicle=self.vehicle,reason='Потребує заміни гальм',started_at=timezone.now(),created_by=self.admin)
        response=self.client.get(reverse('maintenance'))
        self.assertContains(response,'Потребує заміни гальм')
        self.assertContains(response,f'/vehicles/{self.vehicle.pk}/repair/')
        repair.finished_at=timezone.now();repair.result='Гальма замінено';repair.save()
        self.vehicle.in_repair=False;self.vehicle.save(update_fields=['in_repair'])
        response=self.client.get(reverse('maintenance'))
        self.assertContains(response,'Гальма замінено')
        self.assertEqual(len(response.context['repair_vehicles']),0)

    def test_recent_fleet_dates_use_kyiv_local_calendar(self):
        from fleet.views import fleet_event_time
        now=timezone.now()
        self.assertTrue(fleet_event_time(now).startswith('Сьогодні,'))
        yesterday=timezone.localtime(now)-timedelta(days=1)
        self.assertTrue(fleet_event_time(yesterday).startswith('Учора,'))
        self.assertEqual(fleet_event_time(None),'')

    def test_trip_list_sorts_by_vehicle_and_driver_with_filters(self):
        vehicle2=Vehicle.objects.create(plate='AAA001',trailer_plate='AAA002',kind='grain')
        trip1=self.trip(status='completed')
        trip2=Trip.objects.create(vehicle=vehicle2,driver=self.other,origin='Вінниця',destination='Гайсин',cargo='Добрива',planned_tons=24,planned_date=timezone.localdate(),created_by=self.admin,status='completed',completed_at=timezone.now())
        response=self.client.get(reverse('trip_list'),{'sort':'vehicle','status':'completed'})
        self.assertEqual([t.pk for t in response.context['trips']],[trip2.pk,trip1.pk])
        response=self.client.get(reverse('trip_list'),{'sort':'vehicle_desc','status':'completed'})
        self.assertEqual([t.pk for t in response.context['trips']],[trip1.pk,trip2.pk])
        response=self.client.get(reverse('trip_list'),{'sort':'driver','status':'completed'})
        self.assertEqual([t.driver.full_name for t in response.context['trips']],sorted([self.driver.full_name,self.other.full_name]))
        self.assertContains(response,'value="driver" selected')
        self.assertEqual(self.client.get(reverse('trip_list'),{'sort':'bad'}).context['sort'],'recent')

    def test_trip_sort_keeps_the_last_200_before_sorting(self):
        old=self.trip(status='completed');old.planned_date=timezone.localdate()-timedelta(days=10);old.save()
        Trip.objects.bulk_create([Trip(vehicle=self.vehicle,driver=self.driver,origin='A',destination='B',cargo='X',planned_tons=1,planned_date=timezone.localdate(),created_by=self.admin,status='completed',completed_at=timezone.now()) for _ in range(201)])
        response=self.client.get(reverse('trip_list'),{'sort':'vehicle'})
        self.assertEqual(len(response.context['trips']),200)
        self.assertNotIn(old.pk,[trip.pk for trip in response.context['trips']])

    def test_trip_list_filters_driver_vehicle_and_planned_period_together(self):
        vehicle2=Vehicle.objects.create(plate='OTHER01',trailer_plate='OTHER02',kind='grain')
        target=self.trip(status='completed');target.planned_date=timezone.localdate()-timedelta(days=3);target.save()
        self.trip(status='completed')
        Trip.objects.create(vehicle=vehicle2,driver=self.other,origin='A',destination='B',cargo='X',planned_tons=1,planned_date=target.planned_date,created_by=self.admin,status='completed',completed_at=timezone.now())
        params={'vehicle':self.vehicle.pk,'driver':self.driver.pk,'date_from':target.planned_date.isoformat(),'date_to':target.planned_date.isoformat(),'sort':'vehicle'}
        response=self.client.get(reverse('trip_list'),params)
        self.assertEqual([t.pk for t in response.context['trips']],[target.pk])
        self.assertContains(response,'Усі автомобілі');self.assertContains(response,'Усі водії')
        self.assertContains(response,target.planned_date.isoformat())
        self.assertContains(response,'Скинути фільтри')

    def test_trip_filter_invalid_dates_and_ids_do_not_fail(self):
        response=self.client.get(reverse('trip_list'),{'vehicle':'9'*30,'driver':'abc','date_from':'2026-02-30','date_to':'bad'})
        self.assertEqual(response.status_code,200)

    def test_queue_card_colour_matches_driver_acceptance_on_dashboard_and_refresh(self):
        queued=self.trip(status='queued')
        response=self.client.get(reverse('dashboard'))
        self.assertContains(response,'class="queue-item is-pending"')
        self.assertContains(response,'Очікує прийняття')
        queued.accepted_at=timezone.now();queued.accepted_by=self.driver;queued.save()
        response=self.client.get(reverse('dashboard'))
        self.assertContains(response,'class="queue-item is-accepted"')
        self.assertContains(response,'Прийнято')
        self.assertIn('class="queue-item is-accepted"',self.client.get(reverse('dashboard'),{'live':'1'}).json()['cards'])

    def test_accepting_free_queued_trip_makes_it_current(self):
        trip=self.trip(status='queued')
        self.client.force_login(self.driver)
        with self.captureOnCommitCallbacks(execute=True):
            response=self.client.post(reverse('trip_accept',args=[trip.pk]),{'acceptance_location':'м. Вінниця, база','acceptance_odometer_km':'150000'})
        self.assertEqual(response.status_code,302)
        trip.refresh_from_db()
        self.assertTrue(trip.is_accepted);self.assertEqual(trip.status,'planned')

    def test_accepted_queue_waits_for_vehicle_and_earlier_trip(self):
        from fleet.queue_promotion import promote_ready
        self.vehicle.in_repair=True;self.vehicle.save(update_fields=['in_repair'])
        first=self.trip(status='queued',accepted_at=timezone.now(),accepted_by=self.driver)
        second=self.trip(status='queued',accepted_at=timezone.now(),accepted_by=self.driver)
        promote_ready()
        first.refresh_from_db();second.refresh_from_db()
        self.assertEqual(first.status,'queued');self.assertEqual(second.status,'queued')
        self.vehicle.in_repair=False;self.vehicle.save(update_fields=['in_repair'])
        promote_ready()
        first.refresh_from_db();second.refresh_from_db()
        self.assertEqual(first.status,'planned');self.assertEqual(second.status,'queued')

    def test_accepted_queue_does_not_skip_unaccepted_earlier_trip(self):
        from fleet.queue_promotion import promote_ready
        first=self.trip(status='queued')
        second=self.trip(status='queued',accepted_at=timezone.now(),accepted_by=self.driver)
        promote_ready();second.refresh_from_db();self.assertEqual(second.status,'queued')
        first.status='cancelled';first.save(update_fields=['status'])
        promote_ready();second.refresh_from_db();self.assertEqual(second.status,'planned')

    def test_next_driver_receives_notice_while_vehicle_in_current_trip(self):
        current=self.trip(status='planned',accepted_at=timezone.now(),accepted_by=self.driver)
        self.client.force_login(self.other)
        version=self.client.get(reverse('driver_updates')).json()['version']
        queued=Trip.objects.create(vehicle=self.vehicle,driver=self.other,status='queued',origin='Київ',destination='Львів',cargo='Вантаж',planned_tons=1,planned_date=timezone.localdate(),created_by=self.admin)
        from fleet.driver_notifications import notify_assignment
        notify_assignment(queued)
        self.assertNotEqual(version,self.client.get(reverse('driver_updates')).json()['version'])
        response=self.client.get(reverse('dashboard'))
        self.assertContains(response,'data-driver-notifications')
        self.assertNotContains(response,'Нове завдання від логіста')
        self.assertEqual(self.client.get(reverse('driver_notifications')).json()['unread'],1)
        self.assertContains(response,'Авто ще виконує поточний рейс')
        self.assertContains(response,reverse('trip_accept',args=[queued.pk]))
        with self.captureOnCommitCallbacks(execute=True): self.client.post(reverse('trip_accept',args=[queued.pk]),{'acceptance_location':'м. Вінниця, база','acceptance_odometer_km':'150000'})
        queued.refresh_from_db();current.refresh_from_db()
        self.assertTrue(queued.is_accepted);self.assertEqual(queued.status,'queued');self.assertEqual(current.status,'planned')
        self.client.post(reverse('trip_start',args=[queued.pk]));queued.refresh_from_db()
        self.assertEqual(queued.status,'queued')
        self.client.force_login(self.admin)
        response=self.client.get(reverse('dashboard'))
        self.assertContains(response,'Очікує звільнення авто')
        self.assertContains(response,self.driver.full_name);self.assertContains(response,self.other.full_name)

    def test_driver_bell_tracks_new_assignments_and_read_state(self):
        from fleet.driver_notifications import notify_assignment
        old=self.trip(status='queued')
        self.client.force_login(self.driver)
        self.assertEqual(self.client.get(reverse('driver_notifications')).json()['unread'],0)
        notify_assignment(old)
        data=self.client.get(reverse('driver_notifications')).json()
        self.assertEqual(data['unread'],1);self.assertEqual(data['items'][0]['trip'],old.pk)
        self.assertEqual(self.client.post(reverse('driver_notification_read'),{'id':data['items'][0]['id']}).status_code,200)
        self.assertEqual(self.client.get(reverse('driver_notifications')).json()['unread'],0)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('driver_notifications')).json()['items'],[])
        self.assertFalse(self.client.post(reverse('driver_notification_read'),{'id':data['items'][0]['id']}).json()['updated'])

    def test_accepted_notice_does_not_reappear_if_stage_is_reset(self):
        from fleet.driver_notifications import notify_assignment
        trip=self.trip(status='queued');notify_assignment(trip)
        self.client.force_login(self.driver)
        with self.captureOnCommitCallbacks(execute=True): self.client.post(reverse('trip_accept',args=[trip.pk]),{'acceptance_location':'м. Вінниця, база','acceptance_odometer_km':'150000'})
        self.assertEqual(self.client.get(reverse('driver_notifications')).json()['unread'],0)
        trip.refresh_from_db();trip.accepted_at=None;trip.accepted_by=None;trip.save(update_fields=['accepted_at','accepted_by'])
        self.assertEqual(self.client.get(reverse('driver_notifications')).json()['items'],[])

    def test_driver_change_form_lists_queued_trips_individually(self):
        queued=self.trip(status='queued')
        response=self.client.get(reverse('vehicle_edit',args=[self.vehicle.pk]))
        self.assertContains(response,f'Рейс №{queued.pk}')
        self.assertContains(response,reverse('trip_detail',args=[queued.pk]))
        self.assertContains(response,'Рішення для кожного рейсу в черзі')

    def test_move_free_driver_between_free_vehicles_keeps_queued_trips(self):
        source=Vehicle.objects.create(plate='SOURCE1',trailer_plate='SOURCET1',kind='tent',default_driver=self.other)
        queued=self.trip(status='queued')
        response=self.client.post(reverse('vehicle_edit',args=[self.vehicle.pk]),{'plate':self.vehicle.plate,'trailer_plate':self.vehicle.trailer_plate,'kind':self.vehicle.kind,'default_driver':self.other.pk,'transfer_from_vehicle':'on',f'queue_choice_{queued.pk}':'keep'})
        self.assertEqual(response.status_code,302)
        self.vehicle.refresh_from_db();source.refresh_from_db();queued.refresh_from_db()
        self.assertEqual(self.vehicle.default_driver_id,self.other.pk);self.assertIsNone(source.default_driver_id)
        self.assertEqual(queued.driver_id,self.driver.pk);self.assertEqual(queued.status,'queued')
        self.assertTrue(source.assignments.filter(driver=self.other,ended_at__isnull=False).exists())
        self.assertTrue(self.vehicle.assignments.filter(driver=self.other,ended_at__isnull=True).exists())

    def test_driver_change_displays_link_to_vehicle(self):
        data={'plate':self.vehicle.plate,'trailer_plate':self.vehicle.trailer_plate,'kind':self.vehicle.kind,'default_driver':self.other.pk}
        response=self.client.post(reverse('vehicle_edit',args=[self.vehicle.pk]),data,follow=True)
        self.assertContains(response,'Водія змінено')
        self.assertContains(response,f'/#vehicle-{self.vehicle.pk}')

    def test_driver_change_requires_individual_queue_choices(self):
        first=self.trip(status='queued');second=self.trip(status='queued')
        data={'plate':self.vehicle.plate,'trailer_plate':self.vehicle.trailer_plate,'kind':self.vehicle.kind,'default_driver':self.other.pk,f'queue_choice_{first.pk}':'keep'}
        response=self.client.post(reverse('vehicle_edit',args=[self.vehicle.pk]),data)
        self.assertEqual(response.status_code,200)
        self.vehicle.refresh_from_db();self.assertEqual(self.vehicle.default_driver_id,self.driver.pk)
        self.assertContains(response,'Оберіть рішення для кожного рейсу')

    def test_driver_change_can_transfer_one_queue_trip_and_keep_another(self):
        from fleet.models import TripTransfer
        first=self.trip(status='queued',accepted_at=timezone.now(),accepted_by=self.driver);second=self.trip(status='queued')
        data={'plate':self.vehicle.plate,'trailer_plate':self.vehicle.trailer_plate,'kind':self.vehicle.kind,'default_driver':self.other.pk,f'queue_choice_{first.pk}':'transfer',f'queue_choice_{second.pk}':'keep'}
        response=self.client.post(reverse('vehicle_edit',args=[self.vehicle.pk]),data)
        self.assertEqual(response.status_code,302)
        first.refresh_from_db();second.refresh_from_db()
        self.assertEqual(first.driver_id,self.other.pk);self.assertIsNone(first.accepted_at);self.assertIsNone(first.accepted_by)
        self.assertEqual(second.driver_id,self.driver.pk);self.assertEqual(first.vehicle_id,self.vehicle.pk)
        self.assertTrue(TripTransfer.objects.filter(trip=first,previous_driver=self.driver,next_driver=self.other).exists())

    def test_accepted_previous_driver_queue_does_not_auto_start_after_change(self):
        from fleet.queue_promotion import promote_ready
        queued=self.trip(status='queued',accepted_at=timezone.now(),accepted_by=self.driver)
        data={'plate':self.vehicle.plate,'trailer_plate':self.vehicle.trailer_plate,'kind':self.vehicle.kind,'default_driver':self.other.pk,f'queue_choice_{queued.pk}':'keep'}
        self.assertEqual(self.client.post(reverse('vehicle_edit',args=[self.vehicle.pk]),data).status_code,302)
        promote_ready();queued.refresh_from_db()
        self.assertEqual(queued.status,'queued');self.assertEqual(queued.driver_id,self.driver.pk)

    def test_driver_change_refuses_if_trip_started_while_form_was_open(self):
        self.trip(status='planned')
        data={'plate':self.vehicle.plate,'trailer_plate':self.vehicle.trailer_plate,'kind':self.vehicle.kind,'default_driver':self.other.pk,'expect_free':'1'}
        response=self.client.post(reverse('vehicle_edit',args=[self.vehicle.pk]),data)
        self.assertEqual(response.status_code,200)
        self.vehicle.refresh_from_db();self.assertEqual(self.vehicle.default_driver_id,self.driver.pk)
        self.assertContains(response,'Авто вже виконує рейс')

    def test_cannot_assign_unattached_driver_who_is_on_active_trip(self):
        another=Vehicle.objects.create(plate='OTHER1',trailer_plate='OTHERT1',kind='tent')
        Trip.objects.create(vehicle=another,driver=self.other,status='planned',origin='А',destination='Б',cargo='Вантаж',planned_tons=1,planned_date=timezone.localdate(),created_by=self.admin)
        data={'plate':self.vehicle.plate,'trailer_plate':self.vehicle.trailer_plate,'kind':self.vehicle.kind,'default_driver':self.other.pk}
        response=self.client.post(reverse('vehicle_edit',args=[self.vehicle.pk]),data)
        self.assertEqual(response.status_code,200)
        self.vehicle.refresh_from_db();self.assertEqual(self.vehicle.default_driver_id,self.driver.pk)

    def test_cannot_transfer_driver_busy_with_an_active_trip(self):
        source=Vehicle.objects.create(plate='SOURCE1',trailer_plate='SOURCET1',kind='tent',default_driver=self.other)
        Trip.objects.create(vehicle=source,driver=self.other,status='planned',origin='А',destination='Б',cargo='Вантаж',planned_tons=1,planned_date=timezone.localdate(),created_by=self.admin)
        response=self.client.post(reverse('vehicle_edit',args=[self.vehicle.pk]),{'plate':self.vehicle.plate,'trailer_plate':self.vehicle.trailer_plate,'kind':self.vehicle.kind,'default_driver':self.other.pk,'transfer_from_vehicle':'on'})
        self.assertEqual(response.status_code,200)
        self.vehicle.refresh_from_db();source.refresh_from_db()
        self.assertEqual(self.vehicle.default_driver_id,self.driver.pk);self.assertEqual(source.default_driver_id,self.other.pk)
    def test_complete_keeps_history_and_creates_single_next_plan(self):
        self.vehicle.odometer_km=10000;self.vehicle.save()
        item=self.item(interval_months=6,interval_km=10000)
        data=self.complete_data(item,odometer='10100',next_due_km='20100',next_due_date=add_months(timezone.localdate(),6).isoformat())
        url=reverse('maintenance_complete',args=[item.pk]);self.assertEqual(self.client.post(url,data).status_code,302)
        item.refresh_from_db();self.vehicle.refresh_from_db()
        self.assertIsNotNone(item.completed_at);self.assertEqual(item.completed_odometer,10100);self.assertEqual(self.vehicle.odometer_km,10100)
        self.assertEqual(item.next_item.due_km,20100)
        self.client.post(url,data);self.assertEqual(MaintenanceItem.objects.count(),2)
        self.assertEqual(AuditEvent.objects.filter(action='Виконано ТО / поновлено документ').count(),1)
    def test_backdated_completion_does_not_rewind_vehicle_odometer(self):
        self.vehicle.odometer_km=10000;self.vehicle.save();item=self.item()
        self.client.post(reverse('maintenance_complete',args=[item.pk]),self.complete_data(item,odometer=9000,completed_on=(timezone.localdate()-timedelta(days=1)).isoformat()))
        item.refresh_from_db();self.vehicle.refresh_from_db()
        self.assertEqual(item.completed_odometer,9000);self.assertEqual(self.vehicle.odometer_km,10000)
    def test_stale_completion_and_odometer_changes_are_rejected(self):
        item=self.item();data=self.complete_data(item,odometer=10000)
        self.vehicle.odometer_updated_at=timezone.now();self.vehicle.save()
        response=self.client.post(reverse('maintenance_complete',args=[item.pk]),data)
        self.assertContains(response,'Одометр уже змінився');item.refresh_from_db();self.assertIsNone(item.completed_at)
        response=self.client.post(reverse('vehicle_odometer',args=[self.vehicle.pk]),{'odometer_km':100,'revision':''})
        self.assertContains(response,'Показник уже змінився')
    def test_maintenance_requires_due_and_document_date(self):
        from fleet.maintenance import MaintenanceForm
        data={'vehicle':self.vehicle.pk,'kind':'service','applies_to':'tractor','title':'ТО','reminder_days':30,'reminder_km':1000}
        self.assertFalse(MaintenanceForm(data).is_valid())
        data.update(kind='document',due_km=100)
        self.assertFalse(MaintenanceForm(data).is_valid())
        data.update(due_date=timezone.localdate().isoformat(),due_km='')
        self.assertTrue(MaintenanceForm(data).is_valid())
    def test_calendar_repeat_uses_last_valid_day(self):
        from datetime import date
        self.assertEqual(add_months(date(2026,1,31),1),date(2026,2,28))
        self.assertEqual(add_months(date(2024,1,31),1),date(2024,2,29))
    def test_maintenance_keeps_vehicle_numbers_after_later_changes(self):
        item=self.item();tractor=self.vehicle.plate;trailer=self.vehicle.trailer_plate
        self.vehicle.plate='NEW1';self.vehicle.trailer_plate='NEW2';self.vehicle.save()
        item.refresh_from_db();self.assertEqual(item.tractor_plate,tractor);self.assertEqual(item.trailer_plate,trailer)
    def test_maintenance_forms_render(self):
        item=self.item()
        for name,args in [('maintenance_edit',[item.pk]),('maintenance_complete',[item.pk]),('vehicle_odometer',[self.vehicle.pk])]:
            self.assertEqual(self.client.get(reverse(name,args=args)).status_code,200)
    def test_document_renewal_does_not_change_odometer_and_can_repeat_by_date(self):
        self.vehicle.odometer_km=10000;self.vehicle.odometer_updated_at=timezone.now();self.vehicle.save();previous=self.vehicle.odometer_updated_at
        item=self.item();item.kind='document';item.save()
        data=self.complete_data(item,next_due_date=(timezone.localdate()+timedelta(days=365)).isoformat())
        self.assertEqual(self.client.post(reverse('maintenance_complete',args=[item.pk]),data).status_code,302)
        item.refresh_from_db();self.vehicle.refresh_from_db();self.assertIsNone(item.completed_odometer)
        self.assertEqual(self.vehicle.odometer_updated_at,previous);self.assertEqual(item.next_item.kind,'document')
    def document(self,trip=None,content_type='application/pdf'):
        return Document.objects.create(trip=trip or self.trip(),kind='weight',file=SimpleUploadedFile('example.pdf',b'%PDF-1.4\n sample',content_type=content_type),original_name='example.pdf',size=16,content_type=content_type,uploaded_by=self.admin)
    def test_inline_document_scoped_private_and_safe_headers(self):
        doc=self.document();self.client.force_login(self.driver)
        response=self.client.get(reverse('document_inline',args=[doc.pk]))
        self.assertEqual(response.status_code,200);self.assertEqual(response['Content-Type'],'application/pdf')
        self.assertIn('inline',response['Content-Disposition']);self.assertIn('no-store',response['Cache-Control'])
        self.assertIn("frame-ancestors 'self'",response['Content-Security-Policy']);self.assertEqual(response['X-Frame-Options'],'SAMEORIGIN')
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('document_inline',args=[doc.pk])).status_code,404)
        self.assertEqual(self.client.get(reverse('document_preview',args=[doc.pk]),{'format':'json'}).status_code,404)
    def test_preview_contains_only_own_trip_documents_and_has_html_fallback(self):
        doc=self.document();second=self.document(trip=doc.trip);unrelated=self.document()
        self.client.force_login(self.driver)
        response=self.client.get(reverse('document_preview',args=[doc.pk]),{'format':'json'})
        self.assertEqual({r['id'] for r in response.json()['documents']},{doc.pk,second.pk})
        self.assertEqual(self.client.get(reverse('document_preview',args=[doc.pk])).status_code,200)
    def test_inline_rejects_unsupported_types_and_missing_files(self):
        doc=self.document(content_type='text/html')
        self.assertEqual(self.client.get(reverse('document_inline',args=[doc.pk])).status_code,404)
        doc.content_type='application/pdf';doc.file.name='missing.pdf';doc.save()
        self.assertEqual(self.client.get(reverse('document_inline',args=[doc.pk])).status_code,404)
    def test_driver_can_restore_weight_and_other_draft_context(self):
        trip=self.trip(accepted_at=timezone.now(),accepted_by=self.driver,route_enabled=True);point=self.stop(trip)
        self.client.force_login(self.driver)
        for kind in ['weight','other']:
            response=self.client.get(reverse('driver_documents'),{'trip':trip.pk,'stop':point.pk,'kind':kind})
            self.assertEqual(response.context['upload_form'].initial['kind'],kind)

    def test_route_creation_does_not_add_saved_places_even_with_old_checkbox_payload(self):
        from fleet.route_forms import create_formset
        data={'stops-TOTAL_FORMS':2,'stops-INITIAL_FORMS':0,'stops-MAX_NUM_FORMS':30,'stops-MIN_NUM_FORMS':0}
        for i,(kind,address) in enumerate([('load','Вінниця'),('unload','Гайсин')]):
            for field,value in dict(kind=kind,address=address,cargo='Добрива',planned_tons=24,ORDER=i+1,save_place='on').items():data[f'stops-{i}-{field}']=value
        formset=create_formset(data);self.assertTrue(formset.is_valid(),formset.errors)
        trip=self.trip();formset.save_route(trip,self.admin)
        self.assertEqual(SavedPlace.objects.count(),0);self.assertEqual(trip.stops.count(),2)
        self.assertNotIn('save_place',formset.forms[0].fields)

    def test_second_phone_and_phone_without_name_are_valid_and_normalized(self):
        from fleet.places import SavedPlaceForm
        from fleet.route_forms import StopForm
        point=self.stop();place=remember_stop(point,self.admin)
        data={f:getattr(place,f) if getattr(place,f) is not None else '' for f in SavedPlaceForm.Meta.fields}
        data.update(revision=place.updated_at.isoformat(),contact_name='',contact_phone='0678120308',contact_phone_alt='+380 96 772 0761')
        form=SavedPlaceForm(data,instance=place);self.assertTrue(form.is_valid(),form.errors)
        self.assertEqual(form.cleaned_data['contact_phone'],'+380678120308');self.assertEqual(form.cleaned_data['contact_phone_alt'],'+380967720761')
        form.save();place.refresh_from_db()
        copied={f:getattr(point,f) if getattr(point,f) is not None else '' for f in StopForm.Meta.fields}
        for field in ['contact_name','contact_phone','contact_phone_alt']:copied[field]=getattr(place,field)
        form=StopForm(copied,instance=point);self.assertTrue(form.is_valid(),form.errors);form.save()
        response=self.client.get(reverse('saved_place_search'),{'q':place.name})
        self.assertEqual(response.json()['results'][0]['contact_phone_alt'],'+380967720761')
        place.contact_phone_alt='+380501111111';place.save();point.refresh_from_db()
        self.assertEqual(point.contact_phone_alt,'+380967720761')

    def test_invalid_or_duplicate_extra_number_is_rejected(self):
        from fleet.route_forms import StopForm
        point=self.stop();data={f:getattr(point,f) if getattr(point,f) is not None else '' for f in StopForm.Meta.fields}
        for value in ['invalid',point.contact_phone]:
            data['contact_phone_alt']=value;form=StopForm(data,instance=point);self.assertFalse(form.is_valid());self.assertIn('contact_phone_alt',form.errors)

    def test_both_phone_links_visible_only_after_driver_acceptance_and_survive_copy(self):
        from fleet.route_forms import initial_stops
        trip=self.trip(route_enabled=True,status='planned');point=self.stop(trip);point.contact_phone_alt='+380967720761';point.save()
        self.assertEqual(initial_stops(trip)[0]['contact_phone_alt'],point.contact_phone_alt)
        self.client.force_login(self.driver)
        response=self.client.get(reverse('trip_detail',args=[trip.pk]));self.assertNotContains(response,'tel:'+point.contact_phone_alt)
        trip.accepted_at=timezone.now();trip.accepted_by=self.driver;trip.save()
        response=self.client.get(reverse('trip_detail',args=[trip.pk]));self.assertContains(response,'tel:'+point.contact_phone);self.assertContains(response,'tel:'+point.contact_phone_alt)

    def test_document_archive_has_short_places_and_requires_all_active_unloads_for_green(self):
        from fleet.driver_views import document_trip_overview
        trip=self.trip(route_enabled=True);load=self.stop(trip)
        unload=TripStop.objects.create(trip=trip,position=2,kind='unload',address='м. Гребінка, пров. Пирятинський, 23, Гребінківська громада, Лубенський район, Полтавська область',settlement_name='м. Гребінка',region='Полтавська область',district='Лубенський район',community='Гребінківська громада',cargo='Добрива',planned_tons=24,signed_complete=True)
        extra=TripStop.objects.create(trip=trip,position=3,kind='unload',address='Київ',cargo='Добрива',planned_tons=1)
        for point,kind in [(load,'ttn'),(unload,'signed_ttn')]:
            doc=self.document(trip);doc.kind=kind;doc.stop=point;doc.save()
        overview=document_trip_overview(trip)
        self.assertEqual(overview['origin'],'м. Вінниця');self.assertEqual(overview['destination'],'Київ')
        self.assertEqual(overview['state'],'missing');self.assertEqual(overview['point_count'],3)
        self.assertEqual(overview['stop_id'],extra.pk);self.assertEqual(overview['kind'],'signed_ttn')
        extra.state='cancelled';extra.save();overview=document_trip_overview(trip)
        self.assertEqual(overview['destination'],'м. Гребінка');self.assertEqual(overview['state'],'complete')
        self.assertEqual(overview['stop_id'],unload.pk)
        trip.status='cancelled';trip.save();self.assertEqual(document_trip_overview(trip)['state'],'neutral')

    def test_document_archive_uses_only_own_other_trips_and_short_route_labels(self):
        selected=self.trip();other=self.trip(route_enabled=True);self.stop(other)
        TripStop.objects.create(trip=other,position=2,kind='unload',address='Гребінка, пров. Пирятинський, 23',settlement_name='м. Гребінка',cargo='Добрива',planned_tons=24)
        foreign=self.trip();foreign.driver=self.other;foreign.save()
        self.client.force_login(self.driver)
        response=self.client.get(reverse('driver_documents'),{'trip':selected.pk})
        rows=response.context['other_document_trips'];self.assertEqual([row['trip'].pk for row in rows],[other.pk])
        self.assertEqual(rows[0]['destination'],'м. Гребінка')
        self.assertContains(response,'Додати ТТН з підписом');self.assertNotContains(response,'пров. Пирятинський, 23')
        self.assertEqual(self.client.get(reverse('driver_documents'),{'trip':foreign.pk}).status_code,404)

    def test_document_overview_keeps_cancelled_route_names_and_legacy_signed_state(self):
        from fleet.driver_views import document_trip_overview
        trip=self.trip(status='cancelled',route_enabled=True);stop=self.stop(trip);stop.state='cancelled';stop.save()
        self.assertEqual(document_trip_overview(trip)['origin'],'м. Вінниця')
        legacy=self.trip();self.assertEqual(document_trip_overview(legacy)['state'],'missing')
        doc=self.document(legacy);doc.kind='signed_ttn';doc.save()
        self.assertEqual(document_trip_overview(legacy)['state'],'missing')
        loading=self.document(legacy);loading.kind='ttn';loading.save()
        self.assertEqual(document_trip_overview(legacy)['state'],'complete')

    def test_document_archive_uses_prefetched_stops_and_documents(self):
        from fleet.driver_views import document_trip_overview
        self.trip();self.stop(self.trip(route_enabled=True))
        trips=list(Trip.objects.all().prefetch_related('stops','documents'))
        with self.assertNumQueries(0):
            for trip in trips:document_trip_overview(trip)
