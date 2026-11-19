from decimal import Decimal
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from fleet.models import User, Vehicle, Trip
from fleet.finish_details import DriverFinishForm


@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False,STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class WaybillTests(TestCase):
    def setUp(self):
        self.admin=User.objects.create_user('+380501111101',role='admin',full_name='Логіст',must_change_password=False)
        self.driver=User.objects.create_user('+380501111102',role='driver',full_name='Водій',must_change_password=False)
        self.vehicle=Vehicle.objects.create(plate='WAY1234',trailer_plate='WAY5678',kind='tent',default_driver=self.driver)
        self.trip=Trip.objects.create(vehicle=self.vehicle,driver=self.driver,created_by=self.admin,origin='Київ',destination='Вінниця',cargo='Добрива',planned_tons=Decimal('20'),status='completed',completed_at=timezone.now(),planned_date=timezone.localdate(),acceptance_location='м. Житомир',distance_km=300,fuel_used_l=Decimal('100'))

    def test_completed_trip_has_printable_waybill_and_unavailable_mileage_is_not_guessed(self):
        self.client.force_login(self.admin)
        page=self.client.get(reverse('waybill_list'))
        self.assertContains(page,'WAY1234')
        response=self.client.get(reverse('waybill_detail',args=[self.trip.pk]))
        self.assertContains(response,f'ПОДОРОЖНІЙ ЛИСТ № {self.trip.pk}')
        self.assertContains(response,'ТОВ «Макош Мінерал»')
        self.assertContains(response,'SCANIA')
        self.assertContains(response,'м. Житомир')
        self.assertContains(response,'Київ')
        self.assertContains(response,'Вінниця')
        self.assertContains(response,'Без вантажу')
        self.assertContains(response,'300')
        self.client.force_login(self.driver)
        self.assertEqual(self.client.get(reverse('waybill_detail',args=[self.trip.pk])).status_code,403)

    def test_waybill_filters_search_snapshots_points_and_period(self):
        from fleet.models import TripStop
        self.trip.completed_driver_name='Попередній Водій';self.trip.completed_tractor_plate='OLD123';self.trip.completed_trailer_plate='OLD456';self.trip.route_enabled=True;self.trip.save()
        TripStop.objects.create(trip=self.trip,position=1,kind='load',address='Львів, склад',cargo='Добрива',planned_tons=20)
        TripStop.objects.create(trip=self.trip,position=2,kind='unload',address='Одеса, порт',cargo='Добрива',planned_tons=20)
        TripStop.objects.create(trip=self.trip,position=3,kind='unload',address='Скасоване місто',cargo='Добрива',planned_tons=20,state='cancelled')
        self.client.force_login(self.admin);url=reverse('waybill_list')
        for params in [{'q':f'№{self.trip.pk}'},{'q':'OLD456'},{'driver':'Попередній Водій'},{'vehicle':'OLD123'},{'loading':'Львів','unloading':'Одеса'},{'start':timezone.localdate().isoformat(),'end':timezone.localdate().isoformat()}]:
            response=self.client.get(url,params);self.assertContains(response,f'№{self.trip.pk}',msg_prefix=str(params))
        self.assertNotContains(self.client.get(url,{'unloading':'Скасоване місто'}),f'№{self.trip.pk}')
        self.assertContains(self.client.get(url,{'loading':'Невідоме місто'}),'рейсів не знайдено')
        self.assertContains(self.client.get(url,{'start':'2026-12-31','end':'2026-01-01'}),'Початок періоду')
        self.client.force_login(self.driver);self.assertEqual(self.client.get(url).status_code,403)

    def test_waybill_pagination_preserves_filters(self):
        Trip.objects.bulk_create([Trip(vehicle=self.vehicle,driver=self.driver,created_by=self.admin,origin='Луцьк',destination='Одеса',cargo='Добрива',planned_tons=20,status='completed',completed_at=timezone.now(),planned_date=timezone.localdate()) for _ in range(51)])
        self.client.force_login(self.admin)
        response=self.client.get(reverse('waybill_list'),{'loading':'Луцьк'})
        self.assertEqual(response.context['page'].paginator.count,51)
        self.assertContains(response,'page=2')
        response=self.client.get(reverse('waybill_list'),{'loading':'Луцьк','page':2})
        self.assertEqual(len(response.context['trips']),1)
        self.assertContains(response,'page=1')

    def test_return_to_filtered_list_survives_detail_and_edit(self):
        from urllib.parse import urlencode,parse_qs
        self.client.force_login(self.admin)
        filters={'loading':'Київ','unloading':'Вінниця','vehicle':'WAY1234','page':'2'}
        query=urlencode(filters)
        page=self.client.get(reverse('waybill_list'),filters)
        self.assertContains(page,'return_query=')
        detail=self.client.get(reverse('waybill_detail',args=[self.trip.pk]),{'return_query':query})
        self.assertEqual(parse_qs(detail.context['return_query']),parse_qs(query))
        self.assertContains(detail,'loading=%D0%9A%D0%B8%D1%97%D0%B2')
        edit_url=reverse('waybill_edit',args=[self.trip.pk])
        edit=self.client.get(edit_url,{'return_query':query})
        self.assertEqual(parse_qs(edit.context['return_query']),parse_qs(query))
        data={field.name:field.value() or '' for field in edit.context['form']}
        data.update({'return_query':query,'legs-TOTAL_FORMS':'0','legs-INITIAL_FORMS':'0','legs-MIN_NUM_FORMS':'0','legs-MAX_NUM_FORMS':'30'})
        result=self.client.post(edit_url,data)
        self.assertEqual(result.status_code,302)
        returned=self.client.get(result.url)
        self.assertEqual(parse_qs(returned.context['return_query']),parse_qs(query))
        unsafe=self.client.get(reverse('waybill_detail',args=[self.trip.pk]),{'return_query':'next=https://example.com&loading=Київ'})
        self.assertNotIn('next=',unsafe.context['return_query'])

    def test_tractor_is_selected_only_in_vehicle_filter(self):
        self.client.force_login(self.admin)
        url=reverse('waybill_list')
        self.assertContains(self.client.get(url,{'vehicle':'WAY1234'}),f'№{self.trip.pk}')
        self.assertNotContains(self.client.get(url,{'q':'WAY1234'}),f'№{self.trip.pk}')
        self.assertContains(self.client.get(url,{'q':'WAY5678'}),f'№{self.trip.pk}')
        self.assertContains(self.client.get(url,{'driver':self.trip.report_driver_name}),f'№{self.trip.pk}')
        self.assertNotContains(self.client.get(url,{'q':self.trip.report_driver_name}),f'№{self.trip.pk}')

    def test_final_distance_is_sum_of_empty_and_loaded_distances(self):
        form=DriverFinishForm({'empty_distance_km':'71','loaded_distance_km':'1286','fuel_used_l':'410','actual_tons':'20'},trip=self.trip)
        self.assertTrue(form.is_valid(),form.errors)
        self.assertEqual(form.cleaned_data['distance_km'],1357)
        form=DriverFinishForm({'empty_distance_km':'71','fuel_used_l':'410','actual_tons':'20'},trip=self.trip)
        self.assertFalse(form.is_valid())

    def test_waybill_has_empty_and_loaded_legs_in_order(self):
        self.trip.empty_distance_km=71;self.trip.loaded_distance_km=229;self.trip.save()
        self.client.force_login(self.admin)
        response=self.client.get(reverse('waybill_detail',args=[self.trip.pk]))
        self.assertEqual([(row['origin'],row['destination'],row['distance'],row['label']) for row in response.context['legs']],
                         [('м. Житомир','Київ',71,'Без вантажу'),('Київ','Вінниця',229,'З вантажем')])
        self.assertContains(response,'Місце призначення та/або розвантаження вантажу')
        self.assertContains(response,'Завдання водію')

    def test_missing_start_keeps_empty_leg_without_inventing_location(self):
        self.trip.acceptance_location='';self.trip.empty_distance_km=12;self.trip.save()
        self.client.force_login(self.admin)
        legs=self.client.get(reverse('waybill_detail',args=[self.trip.pk])).context['legs']
        self.assertEqual((legs[0]['origin'],legs[0]['destination'],legs[0]['distance']),('Не вказано','Київ',12))

    def test_driver_fields_use_completed_trip_identity_and_dispatch_address_is_blank(self):
        self.trip.completed_driver_name='Перший Водій';self.trip.save(update_fields=['completed_driver_name'])
        self.driver.full_name='Інший Водій';self.driver.save(update_fields=['full_name'])
        self.client.force_login(self.admin)
        response=self.client.get(reverse('waybill_detail',args=[self.trip.pk]))
        self.assertContains(response,'Перший Водій',count=3)
        self.assertContains(response,'Адреса подачі <span class="line">________________________</span>',html=True)

    def test_start_and_calculated_end_odometer_use_trip_snapshot_and_legs(self):
        self.trip.acceptance_odometer_km=150000;self.trip.empty_distance_km=71;self.trip.loaded_distance_km=229;self.trip.save()
        self.vehicle.odometer_km=900000;self.vehicle.save(update_fields=['odometer_km'])
        self.client.force_login(self.admin)
        response=self.client.get(reverse('waybill_detail',args=[self.trip.pk]))
        self.assertEqual(response.context['end_odometer_km'],150300)
        self.assertContains(response,'150000');self.assertContains(response,'150300')
        self.trip.empty_distance_km=None;self.trip.save(update_fields=['empty_distance_km'])
        self.assertIsNone(self.client.get(reverse('waybill_detail',args=[self.trip.pk])).context['end_odometer_km'])

    def test_reaccepting_during_trip_does_not_replace_start_reading(self):
        self.trip.status='road';self.trip.acceptance_odometer_km=150000;self.trip.accepted_at=None;self.trip.accepted_by=None;self.trip.save()
        self.client.force_login(self.driver)
        self.client.post(reverse('trip_accept',args=[self.trip.pk]),{'acceptance_location':'Умань','acceptance_odometer_km':'150250'})
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.acceptance_odometer_km,150000)
        self.assertEqual(self.trip.acceptance_location,'м. Житомир')

    def test_waybill_edit_is_audited_and_does_not_change_original_trip(self):
        self.client.force_login(self.admin)
        url=reverse('waybill_edit',args=[self.trip.pk])
        page=self.client.get(url)
        self.assertEqual(page.status_code,200)
        data={field.name:field.value() or '' for field in page.context['form']}
        data.update({'revision':self.trip.updated_at.isoformat(),'brand':'SCANIA','dispatch_address':'Автобаза','driver_name':'Водій виправлений','legs-TOTAL_FORMS':'1','legs-INITIAL_FORMS':'0','legs-MIN_NUM_FORMS':'0','legs-MAX_NUM_FORMS':'30','legs-0-origin':'Стоянка','legs-0-destination':'Любомль','legs-0-distance':'71','legs-0-label':'Без вантажу'})
        response=self.client.post(url,data)
        self.assertEqual(response.status_code,302)
        self.trip.refresh_from_db();self.assertEqual(self.trip.origin,'Київ')
        page=self.client.get(reverse('waybill_detail',args=[self.trip.pk]))
        self.assertContains(page,'Автобаза');self.assertContains(page,'Водій виправлений');self.assertContains(page,'Любомль')
        self.assertTrue(self.trip.events.filter(action='Змінено шляховий лист').exists())
        self.trip.fuel_used_l=Decimal('120');self.trip.save(update_fields=['fuel_used_l'])
        page=self.client.get(reverse('waybill_detail',args=[self.trip.pk]))
        self.assertContains(page,'120')
        self.assertContains(page,'data-waybill-print')
        self.assertContains(page,'waybill_print.js')
        self.assertNotContains(page,'onclick="window.print()"')

    def test_moderator_can_open_edit_screen_and_driver_cannot(self):
        moderator=User.objects.create_user('+380501111103',role='moderator',full_name='Модератор',must_change_password=False)
        url=reverse('waybill_edit',args=[self.trip.pk])
        self.client.force_login(moderator);self.assertEqual(self.client.get(url).status_code,200)
        self.client.force_login(self.driver);self.assertEqual(self.client.get(url).status_code,403)

    def test_edit_date_uses_browser_compatible_format(self):
        self.client.force_login(self.admin)
        response=self.client.get(reverse('waybill_edit',args=[self.trip.pk]))
        self.assertContains(response,f'value="{self.trip.planned_date.isoformat()}"')

    def test_stale_waybill_edit_does_not_overwrite_changes(self):
        self.client.force_login(self.admin);url=reverse('waybill_edit',args=[self.trip.pk])
        form=self.client.get(url).context['form']
        data={field.name:field.value() or '' for field in form}
        self.trip.fuel_used_l=Decimal('77');self.trip.save(update_fields=['fuel_used_l','updated_at'])
        data.update({'legs-TOTAL_FORMS':'0','legs-INITIAL_FORMS':'0','legs-MIN_NUM_FORMS':'0','legs-MAX_NUM_FORMS':'30'})
        response=self.client.post(url,data)
        self.assertContains(response,'Рейс змінився')
        self.trip.refresh_from_db();self.assertEqual(self.trip.waybill_overrides,{})
