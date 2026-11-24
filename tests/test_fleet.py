import csv, tempfile, uuid
from io import BytesIO, StringIO
from datetime import timedelta
from pathlib import Path
from decimal import Decimal
from PIL import Image
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command, CommandError
from django.db import IntegrityError, transaction
from fleet.models import User, Vehicle, Trip, Document, AuditEvent, LoginThrottle, normalize_phone

@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, STORAGES={"default":{"BACKEND":"django.core.files.storage.FileSystemStorage"},"staticfiles":{"BACKEND":"django.contrib.staticfiles.storage.StaticFilesStorage"}})
class FleetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password='Correct-Test-Password-8276!'
        cls.admin=User.objects.create_superuser('+380501111111',cls.password,full_name='Адміністратор Тест')
        cls.logistic=User.objects.create_user('+380502222222',cls.password,full_name='Логіст Тест',role='admin',must_change_password=False)
        cls.driver=User.objects.create_user('+380504444444',cls.password,full_name='Водій Перший',must_change_password=False)
        cls.other=User.objects.create_user('+380505555555',cls.password,full_name='Водій Другий',must_change_password=False)
        cls.spare=User.objects.create_user('+380506666666',cls.password,full_name='Підмінний Тест',must_change_password=False)
        cls.vehicle=Vehicle.objects.create(plate='TEST0001',trailer_plate='TEST1001',kind='tent',default_driver=cls.driver)
        cls.vehicle2=Vehicle.objects.create(plate='TEST0002',trailer_plate='TEST1002',kind='grain',default_driver=cls.other)
        cls.trip=Trip.objects.create(vehicle=cls.vehicle,driver=cls.driver,origin='Гнівань',destination='Чоп',cargo='Кукурудза',planned_tons='24.2',planned_date=timezone.localdate(),created_by=cls.logistic)
        cls.trip2=Trip.objects.create(vehicle=cls.vehicle2,driver=cls.other,origin='Любомль',destination='Рівне',cargo='Пшениця',planned_tons='23',planned_date=timezone.localdate(),created_by=cls.logistic)
    def setUp(self):
        self.media=tempfile.TemporaryDirectory()
        self.override=override_settings(MEDIA_ROOT=self.media.name); self.override.enable()
        self.addCleanup(self.override.disable);self.addCleanup(self.media.cleanup)
    def login_as(self,user): self.client.force_login(user)
    def test_trip_list_shows_trip_vehicle_and_handover_driver(self):
        from fleet.models import TripTransfer
        TripTransfer.objects.create(trip=self.trip, kind='substitution', previous_driver=self.other, next_driver=self.driver, from_vehicle=self.vehicle, to_vehicle=self.vehicle, previous_status='road', next_status='road', moved_by=self.logistic)
        self.login_as(self.logistic)
        response = self.client.get(reverse('trip_list'))
        self.assertContains(response, 'Рейс №%s' % self.trip.pk)
        self.assertContains(response, self.vehicle.plate)
        self.assertContains(response, self.vehicle.trailer_plate)
        self.assertContains(response, self.driver.full_name)
        self.assertContains(response, 'До підміни')
        self.assertContains(response, self.other.full_name)
    def test_trip_points_save_drive_to_coordinates_and_are_copied_with_route(self):
        from urllib.parse import urlparse,parse_qs
        self.trip.origin_lat=Decimal('49.233083');self.trip.origin_lon=Decimal('28.468217')
        self.trip.destination_lat=Decimal('50.450100');self.trip.destination_lon=Decimal('30.523400');self.trip.save()
        self.assertEqual(parse_qs(urlparse(self.trip.loading_directions_url).query)['destination'],['49.233083,28.468217'])
        self.login_as(self.driver);response=self.client.get('/')
        self.assertContains(response,'destination=49.233083%2C28.468217');self.assertContains(response,'data-location-preview',count=2)
        self.assertNotContains(response,'Пошук за адресою')
        self.login_as(self.other);self.assertNotContains(self.client.get('/'),'49.233083')
        self.login_as(self.logistic);response=self.client.get('/trips/new/',{'copy_from':self.trip.pk})
        self.assertEqual(response.context['form'].initial['origin_lat'],Decimal('49.233083'))
    def test_point_editor_checks_pairs_ranges_and_address_confirmation(self):
        self.login_as(self.logistic);url=reverse('trip_quick_edit',args=[self.trip.pk,'origin'])
        data={'origin':self.trip.origin,'origin_lat':'49.233083','origin_lon':'28.468217','revision':self.trip.updated_at.isoformat()}
        for updates in [{'origin_lon':''},{'origin_lat':'91'},{'origin_lon':'181'},{'origin_lat':'NaN'}]:
            response=self.client.post(url,{**data,**updates});self.assertEqual(response.status_code,200);self.assertTrue(response.context['form'].errors)
        self.assertEqual(self.client.post(url,data).status_code,302);self.trip.refresh_from_db()
        data.update(origin='Інший склад',revision=self.trip.updated_at.isoformat())
        self.assertContains(self.client.post(url,data),'Адресу змінено')
        self.trip.refresh_from_db();self.assertEqual(self.trip.origin,'Гнівань')
        self.assertEqual(self.client.post(url,{**data,'origin_point_confirmed':'True'}).status_code,302)
        self.trip.refresh_from_db();self.assertEqual(self.trip.origin,'Інший склад')
        self.assertEqual(self.trip.origin_lat,Decimal('49.233083'))
        self.assertTrue(self.trip.events.filter(action='Оновлено інформацію рейсу',detail__contains='Широта').exists())
    def test_map_search_permissions_cache_rate_and_outage(self):
        from unittest.mock import patch,MagicMock
        from fleet.models import MapSearchState,MapSearchCache
        self.assertEqual(self.client.get('/maps/search/?q=Вінниця').status_code,302)
        self.login_as(self.driver);self.assertEqual(self.client.get('/maps/search/?q=Вінниця').status_code,403)
        self.login_as(self.logistic)
        payload=b'[{"lat":"49.233083","lon":"28.468217","display_name":"Vinnytsia, Ukraine"}]'
        upstream=MagicMock();upstream.__enter__.return_value.read.return_value=payload
        with patch('fleet.locations.urlopen',return_value=upstream) as fetch:
            response=self.client.get('/maps/search/',{'q':'Вінниця'});self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['results'][0]['lat'],'49.233083')
            self.assertIn('MakoshFleet',fetch.call_args.args[0].get_header('User-agent'))
            self.assertEqual(self.client.get('/maps/search/',{'q':'Вінниця'}).json(),response.json());self.assertEqual(fetch.call_count,1)
            self.assertEqual(self.client.get('/maps/search/',{'q':'Київ'}).status_code,429);self.assertEqual(fetch.call_count,1)
        MapSearchState.objects.filter(pk=1).update(last_requested_at=timezone.now()-timedelta(seconds=2))
        with patch('fleet.locations.urlopen',side_effect=OSError('offline')):
            self.assertEqual(self.client.get('/maps/search/',{'q':'Київ'}).status_code,503)
        self.assertEqual(MapSearchCache.objects.count(),1)
        self.assertEqual(self.client.get('/maps/search/?q=a').status_code,400)
        self.assertEqual(self.client.post('/maps/search/',{'q':'Вінниця'}).status_code,405)
        policy=self.client.get('/').headers['Content-Security-Policy']
        self.assertIn('https://tile.openstreetmap.org',policy);self.assertIn("script-src 'self'",policy);self.assertNotIn('unsafe-inline',policy)
    def test_report_address_correction_clears_stale_point_and_audits_it(self):
        self.finish();self.trip.origin_lat=Decimal('49.233083');self.trip.origin_lon=Decimal('28.468217');self.trip.save()
        self.login_as(self.logistic);data=self.report_edit_data();data['origin']='Нова адреса'
        self.assertEqual(self.client.post(self.url('report_edit',self.trip.pk),data).status_code,302)
        self.trip.refresh_from_db();self.assertIsNone(self.trip.origin_lat);self.assertIsNone(self.trip.origin_lon)
        self.assertTrue(self.trip.events.filter(action='Знято точку на карті').exists())
    def test_copy_trip_prefills_only_reusable_fields_and_get_creates_nothing(self):
        self.trip.actual_tons=Decimal('23.8');self.trip.distance_km=420;self.trip.ttn_number='OLD-TTN';self.trip.original_received=True
        self.trip.loading_contact_name='Іван';self.trip.loading_contact_phone='+380501234567';self.trip.notes='Телефонувати перед в’їздом';self.trip.save()
        self.login_as(self.logistic);self.upload();before_count=Trip.objects.count()
        self.trip.refresh_from_db()
        response=self.client.get('/trips/new/',{'copy_from':self.trip.pk})
        self.assertEqual(Trip.objects.count(),before_count)
        initial=response.context['form'].initial
        for field in ['origin','destination','cargo','planned_tons','notes','loading_contact_name','loading_contact_phone']:
            self.assertEqual(initial[field],getattr(self.trip,field))
        for field in ['vehicle','driver','ttn_number','actual_tons','distance_km','accepted_at','completed_at']:
            self.assertNotIn(field,initial)
        self.assertEqual(initial['planned_date'],timezone.localdate())
        self.assertContains(response,'name="copy_from"');self.assertNotContains(response,'OLD-TTN')
        self.login_as(self.driver);self.assertEqual(self.client.get('/trips/new/',{'copy_from':self.trip.pk}).status_code,403)
    def copy_data(self):
        return {'copy_from':self.trip.pk,'vehicle':self.vehicle.pk,'driver':self.driver.pk,'origin':self.trip.origin,'destination':self.trip.destination,'cargo':self.trip.cargo,'planned_tons':'24.2','planned_date':timezone.localdate().isoformat(),'scheduling':'current'}
    def test_copy_save_creates_fresh_trip_and_records_provenance_without_changing_source(self):
        self.finish();self.trip.actual_tons=Decimal('23.8');self.trip.distance_km=420;self.trip.ttn_number='OLD';self.trip.original_received=True;self.trip.save()
        self.login_as(self.logistic);self.upload();original_updated=self.trip.updated_at
        data={**self.copy_data(),'actual_tons':'11','distance_km':'999','status':'completed','accepted_at':timezone.now().isoformat(),'original_received':'on'}
        response=self.client.post('/trips/new/',data);self.assertEqual(response.status_code,302)
        created=Trip.objects.latest('pk');self.assertNotEqual(created.pk,self.trip.pk)
        self.assertEqual(created.status,'planned');self.assertIsNone(created.actual_tons);self.assertIsNone(created.distance_km)
        self.assertEqual(created.ttn_number,'');self.assertFalse(created.original_received)
        self.assertIsNone(created.accepted_at);self.assertIsNone(created.completed_at);self.assertFalse(created.documents.exists())
        self.assertFalse(created.cancellation_notice_required);self.assertFalse(created.transfers.exists())
        self.assertIn(f'№{self.trip.pk}',created.events.get(action='Скопійовано рейс').detail)
        self.trip.refresh_from_db();self.assertEqual(self.trip.updated_at,original_updated);self.assertEqual(self.trip.documents.count(),1)
    def test_copy_respects_queue_validation_and_preserves_source_on_invalid_submission(self):
        self.login_as(self.logistic);data=self.copy_data()
        response=self.client.post('/trips/new/',data)
        self.assertEqual(response.status_code,200);self.assertTrue(response.context['form'].errors)
        self.assertEqual(response.context['copy_from'].pk,self.trip.pk)
        self.assertContains(response,f'name="copy_from" value="{self.trip.pk}"')
        self.assertEqual(Trip.objects.count(),2)
        self.assertEqual(self.client.post('/trips/new/',{**data,'scheduling':'queued'}).status_code,302)
        self.assertEqual(Trip.objects.latest('pk').status,'queued')
        for value in ['bad','99999999']:
            self.assertEqual(self.client.get('/trips/new/',{'copy_from':value}).status_code,404)

    def test_queued_trip_creation_shows_confirmation_link(self):
        self.login_as(self.logistic)
        response=self.client.post('/trips/new/',{**self.copy_data(),'scheduling':'queued'},follow=True)
        self.assertContains(response,'Рейс додано в чергу')
        self.assertContains(response,f'/#vehicle-{self.vehicle.pk}')
    def complete_report_for_attention_test(self, *trips):
        from fleet.models import TripEconomics
        for trip in trips:
            Trip.objects.filter(pk=trip.pk).update(distance_km=100, fuel_used_l=30); trip.refresh_from_db()
            TripEconomics.objects.create(trip=trip, **{field: 0 for field in ['price','daily_rate','days','bonus_per_km','fuel_price','adblue_price','adblue_rate','depreciation_per_km','credit_per_km','extra_cost']})

    def test_attention_counts_unique_trips_and_resolves_weight_and_document_issues(self):
        self.finish();self.finish(self.trip2);self.complete_report_for_attention_test(self.trip,self.trip2)
        self.login_as(self.logistic)
        response=self.client.get('/attention/')
        self.assertEqual(response.context['affected_count'],2);self.assertEqual(response.context['active_count'],2)
        self.assertEqual({g['key']:g['count'] for g in response.context['categories']},{'cancellation':0,'acceptance':0,'documents':2,'report':0})
        self.upload();self.upload()
        self.trip.actual_tons=Decimal('24');self.trip.save()
        response=self.client.get('/attention/')
        self.assertEqual(response.context['affected_count'],1);self.assertEqual(response.context['active_count'],1)
        self.login_as(self.logistic);self.upload(self.trip2)
        self.trip2.actual_tons=Decimal('23');self.trip2.save()
        response=self.client.get('/attention/?live=1')
        self.assertEqual(response.json()['count'],0)
        self.assertNotContains(self.client.get('/attention/'),'id="attention-title"')
    def test_attention_acceptance_matches_current_driver_and_cancellation_requires_ack(self):
        self.trip2.status='road';self.trip2.save()
        self.trip.accepted_at=timezone.now();self.trip.accepted_by=self.other;self.trip.save()
        self.login_as(self.logistic)
        self.assertEqual(self.client.get('/attention/').context['affected_count'],1)
        self.login_as(self.driver);self.accept()
        self.login_as(self.logistic);self.assertEqual(self.client.get('/attention/').context['affected_count'],0)
        self.cancel_trip('Зміна плану')
        response=self.client.get('/attention/')
        self.assertEqual([g['key'] for g in response.context['categories'] if g['count']],['cancellation'])
        self.login_as(self.driver);self.client.post(self.url('trip_ack_cancel',self.trip.pk))
        self.login_as(self.logistic);self.assertEqual(self.client.get('/attention/').context['affected_count'],0)
        self.trip2.status='cancelled';self.trip2.save()
        self.assertEqual(self.client.get('/attention/').context['affected_count'],0)
        self.login_as(self.driver);self.assertEqual(self.client.get('/attention/').status_code,403)
        self.assertEqual(self.client.get('/?live=1').status_code,403)
    def test_attention_previews_are_bounded_and_all_matching_trips_are_paginated(self):
        self.finish();self.finish(self.trip2)
        Trip.objects.bulk_create([Trip(vehicle=self.vehicle,driver=self.driver,origin='A',destination='B',cargo='X',planned_tons=20,planned_date=timezone.localdate(),status='completed',completed_at=timezone.now(),created_by=self.logistic) for _ in range(51)])
        self.login_as(self.logistic);response=self.client.get('/attention/')
        self.assertEqual(response.context['affected_count'],53)
        self.assertEqual(len(response.context['issue_page']),18)
        response=self.client.get('/trips/',{'attention':'documents','page':2})
        self.assertEqual(response.context['attention_page'].paginator.count,53);self.assertEqual(len(response.context['trips']),3)
        for trip in response.context['trips']: self.assertIsNone(trip.actual_tons)
        self.assertContains(response,'attention=documents')
        self.assertEqual(len(self.client.get('/trips/?attention=invalid').context['trips']),0)
    def attention_issue(self,key,trip=None):
        from fleet.attention import issue_snapshot
        return next(i for i in issue_snapshot(Trip.objects.filter(pk=(trip or self.trip).pk)) if i['key']==key)

    def attention_post(self,key,mode='skip',trip=None,**extra):
        issue=self.attention_issue(key,trip)
        return self.client.post(reverse('attention_action',args=[(trip or self.trip).pk,key]),{'action':mode,'episode':issue['episode'],'skip_version':issue['skip_version'],**extra})

    def test_attention_skip_is_per_issue_shared_audited_and_restorable(self):
        from fleet.models import AttentionSkip
        self.finish();self.finish(self.trip2);self.complete_report_for_attention_test(self.trip,self.trip2);self.login_as(self.logistic)
        before=self.trip.updated_at
        self.assertEqual(self.attention_post('documents',note='Отримано на папері').status_code,302)
        response=self.client.get('/attention/')
        self.assertEqual(response.context['active_count'],1);self.assertEqual(response.context['skipped_count'],1)
        self.assertEqual([i['key'] for i in response.context['issue_page'] if i['trip'].pk==self.trip.pk],[])
        self.trip.refresh_from_db();self.assertEqual(self.trip.updated_at,before);self.assertIsNone(self.trip.actual_tons)
        self.assertEqual(AttentionSkip.objects.count(),1)
        self.assertEqual(self.trip.events.filter(action='Пропущено питання уваги').count(),1)
        self.login_as(self.admin)
        response=self.client.get('/attention/?tab=skipped')
        self.assertContains(response,'Отримано на папері');self.assertContains(response,self.logistic.full_name)
        self.assertEqual(self.attention_post('documents',mode='restore').status_code,302)
        self.assertEqual(self.client.get('/attention/count/').json()['count'],2)
        self.assertEqual(self.trip.events.filter(action='Повернуто питання до уваги').count(),1)
        self.assertTrue(AttentionSkip.objects.get().restored_at)
        self.attention_post('documents');self.assertEqual(AttentionSkip.objects.count(),1)

    def test_missing_actual_weight_never_creates_attention_or_blocks_completion(self):
        from fleet.attention import issue_snapshot,filter_attention
        self.finish();self.finish(self.trip2);self.complete_report_for_attention_test(self.trip,self.trip2);self.login_as(self.logistic)
        self.upload();self.upload(self.trip2)
        self.assertIsNone(Trip.objects.get(pk=self.trip.pk).actual_tons)
        self.assertEqual(issue_snapshot(),[])
        self.assertFalse(filter_attention(Trip.objects.all(),'weight').exists())
        self.assertEqual(self.client.get('/attention/count/').json()['count'],0)
        self.assertNotContains(self.client.get('/attention/'),'Не внесена фактична вага')

    def test_attention_skip_rejects_stale_actions_and_driver_access(self):
        from fleet.models import AttentionSkip
        self.login_as(self.logistic);issue=self.attention_issue('acceptance')
        url=reverse('attention_action',args=[self.trip.pk,'acceptance'])
        payload={'action':'skip','episode':issue['episode'],'skip_version':issue['skip_version']}
        self.client.post(url,payload);self.client.post(url,payload)
        self.assertEqual(self.trip.events.filter(action='Пропущено питання уваги').count(),1)
        stale=self.attention_issue('acceptance')
        self.attention_post('acceptance',mode='restore');self.attention_post('acceptance')
        self.client.post(url,{'action':'restore','episode':stale['episode'],'skip_version':stale['skip_version']})
        self.assertIsNone(AttentionSkip.objects.get().restored_at)
        self.client.post(url,{**payload,'episode':'bad'})
        self.assertEqual(self.client.get(url).status_code,405)
        self.login_as(self.driver)
        for path in ['/attention/','/attention/count/','/attention/?live=1']: self.assertEqual(self.client.get(path).status_code,403)
        self.assertEqual(self.client.post(url,payload).status_code,403)
        self.assertNotContains(self.client.get('/'),'class="attention-nav')

    def test_attention_filters_pagination_live_and_navigation(self):
        self.finish();self.finish(self.trip2);self.complete_report_for_attention_test(self.trip,self.trip2);self.login_as(self.logistic)
        response=self.client.get('/attention/',{'category':'documents','q':self.vehicle.plate})
        self.assertEqual(response.context['result_count'],1)
        response=self.client.get('/attention/?live=1&category=documents')
        self.assertEqual(response.json()['count'],2);self.assertIn('Немає підписаної ТТН',response.json()['html'])
        self.assertEqual(response['Cache-Control'],'no-store')
        response=self.client.get('/')
        self.assertContains(response,'href="/attention/"');self.assertNotContains(response,'id="fleet-attention"')
        self.assertNotIn('attention',self.client.get('/?live=1').json())
        self.assertEqual(self.client.get('/attention/',{'page':'bad','category':'bad'}).status_code,200)

    def test_attention_acceptance_matches_stage_rules_after_driver_handover(self):
        from fleet.attention import filter_attention
        from fleet.models import TripTransfer
        for status in ['planned','queued','loading','road','unloading','completed','cancelled']:
            self.trip.status=status;self.trip.save()
            self.assertEqual(filter_attention(Trip.objects.filter(pk=self.trip.pk),'acceptance').exists(),self.trip.requires_acceptance)
        self.trip.status='road';self.trip.save()
        TripTransfer.objects.create(trip=self.trip,kind='substitution',previous_driver=self.other,next_driver=self.driver,from_vehicle=self.vehicle,to_vehicle=self.vehicle,previous_status='road',next_status='road',moved_by=self.logistic)
        self.assertFalse(filter_attention(Trip.objects.filter(pk=self.trip.pk),'acceptance').exists())
        self.trip.accepted_by=self.driver;self.trip.accepted_at=timezone.now();self.trip.save()
        self.assertFalse(filter_attention(Trip.objects.filter(pk=self.trip.pk),'acceptance').exists())
    def test_quick_edit_changes_only_selected_field_and_preserves_stage(self):
        self.trip.status='road';self.trip.actual_tons=Decimal('23.8');self.trip.distance_km=400;self.trip.save()
        start=self.trip.status_changed_at
        self.login_as(self.logistic)
        url=reverse('trip_quick_edit',args=[self.trip.pk,'cargo'])
        response=self.client.get(url)
        self.assertEqual(set(response.context['form'].fields),{'cargo','revision'})
        response=self.client.post(url,{'cargo':'Пшениця','revision':self.trip.updated_at.isoformat(),'status':'completed','driver':self.other.pk,'actual_tons':'1','distance_km':'1','notes':'unwanted'})
        self.assertEqual(response.status_code,302);self.trip.refresh_from_db()
        self.assertEqual(self.trip.cargo,'Пшениця');self.assertEqual(self.trip.driver,self.driver)
        self.assertEqual(self.trip.status,'road');self.assertEqual(self.trip.status_changed_at,start)
        self.assertEqual(self.trip.actual_tons,Decimal('23.8'));self.assertEqual(self.trip.distance_km,400);self.assertEqual(self.trip.notes,'')
        self.assertEqual(self.trip.events.count(),1);self.assertIn('Пшениця',self.trip.events.get().detail)
    def test_quick_contact_editor_requires_complete_contact_and_checks_revision(self):
        self.login_as(self.logistic);url=reverse('trip_quick_edit',args=[self.trip.pk,'loading_contact'])
        data={'loading_contact_name':'Іван','loading_contact_phone':'','revision':self.trip.updated_at.isoformat()}
        self.assertContains(self.client.post(url,data),'Додайте телефон контактної особи')
        data['loading_contact_phone']='0501234567'
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.trip.refresh_from_db();self.assertEqual(self.trip.loading_contact_phone,'+380501234567')
        self.assertContains(self.client.post(url,{**data,'loading_contact_name':'Петро'}),'Рейс уже змінився')
        self.trip.refresh_from_db();self.assertEqual(self.trip.loading_contact_name,'Іван')
    def test_quick_fact_edit_validates_weight_and_disallows_driver_access(self):
        self.login_as(self.logistic);url=reverse('trip_quick_edit',args=[self.trip.pk,'actual_tons'])
        data={'actual_tons':'61','revision':self.trip.updated_at.isoformat()}
        response=self.client.post(url,data);self.assertTrue(response.context['form'].errors)
        self.assertEqual(self.client.get(reverse('trip_quick_edit',args=[self.trip.pk,'driver'])).status_code,404)
        data['actual_tons']='24.10';self.assertEqual(self.client.post(url,data).status_code,302)
        self.trip.refresh_from_db();self.assertEqual(self.trip.actual_tons,Decimal('24.10'))
        csrf=Client(enforce_csrf_checks=True);csrf.force_login(self.logistic)
        self.assertEqual(csrf.post(url,data).status_code,403)
        self.login_as(self.driver);self.assertEqual(self.client.get(url).status_code,403);self.assertEqual(self.client.post(url,data).status_code,403)
    def test_logistic_can_edit_trip_facts_without_changing_stage_or_its_clock(self):
        self.login_as(self.logistic)
        before=self.trip.status_changed_at
        self.assertContains(self.client.get(self.url('trip_detail',self.trip.pk)),'Змінити: фактична вага')
        url=self.url('trip_facts',self.trip.pk)
        data={'actual_tons':'23.80','distance_km':'420','ttn_number':'ТТН-123','revision':self.trip.updated_at.isoformat()}
        self.assertRedirects(self.client.post(url,data),self.url('trip_detail',self.trip.pk),fetch_redirect_response=False)
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.actual_tons,Decimal('23.80'));self.assertEqual(self.trip.distance_km,420);self.assertEqual(self.trip.ttn_number,'ТТН-123')
        self.assertEqual(self.trip.status,'planned');self.assertEqual(self.trip.status_changed_at,before)
        self.assertEqual(self.trip.events.filter(action='Уточнено дані рейсу').count(),3)
        self.assertContains(self.client.get(self.url('trip_detail',self.trip.pk)),'ТТН-123')
        data.update(actual_tons='',distance_km='',ttn_number='',revision=self.trip.updated_at.isoformat())
        self.client.post(url,data);self.trip.refresh_from_db()
        self.assertIsNone(self.trip.actual_tons);self.assertIsNone(self.trip.distance_km);self.assertEqual(self.trip.ttn_number,'')
    def test_trip_facts_validate_values_staleness_and_permissions(self):
        self.login_as(self.logistic);url=self.url('trip_facts',self.trip.pk)
        data={'actual_tons':'22','distance_km':'300','ttn_number':'42','revision':self.trip.updated_at.isoformat()}
        for bad in [{'actual_tons':'61'},{'distance_km':'-1'},{'distance_km':'20001'},{'revision':''}]:
            response=self.client.post(url,{**data,**bad});self.assertEqual(response.status_code,200);self.assertTrue(response.context['facts_form'].errors)
        self.trip.notes='Оновлено';self.trip.save()
        self.assertContains(self.client.post(url,data),'Рейс уже змінився')
        self.trip.refresh_from_db();self.assertIsNone(self.trip.actual_tons);self.assertFalse(self.trip.events.exists())
        self.login_as(self.driver)
        self.assertEqual(self.client.get(url).status_code,403);self.assertEqual(self.client.post(url,data).status_code,403)
        self.assertNotContains(self.client.get(self.url('trip_detail',self.trip.pk)),'Змінити: фактична вага')
        csrf=Client(enforce_csrf_checks=True);csrf.force_login(self.logistic)
        self.assertEqual(csrf.post(url,data).status_code,403)
    def test_stage_clock_uses_recorded_time_and_only_resets_on_stage_change(self):
        from fleet.trip_stages import elapsed_label
        now=timezone.now()
        self.assertEqual(elapsed_label(now-timedelta(hours=3,minutes=20),now),'3 год 20 хв')
        self.assertEqual(elapsed_label(now-timedelta(days=2,hours=4),now),'2 дн 4 год')
        self.assertEqual(elapsed_label(None,now),'')
        self.assertEqual(elapsed_label(now+timedelta(minutes=10),now),'щойно')
        self.trip.status='road';self.trip.status_changed_at=now-timedelta(hours=3,minutes=20);self.trip.save()
        start=self.trip.status_changed_at
        self.login_as(self.logistic)
        self.assertContains(self.client.get('/'),'3 год 20 хв')
        self.assertContains(self.client.get(self.url('trip_detail',self.trip.pk)),'data-stage-since')
        self.upload();self.trip.refresh_from_db();self.assertEqual(self.trip.status_changed_at,start)
        self.client.post(self.url('trip_status',self.trip.pk),{'status':'unloading','actual_tons':'23.5'})
        self.trip.refresh_from_db();self.assertGreater(self.trip.status_changed_at,start)
        self.assertIn('23.5',self.trip.events.get(action='Оновлено фактичну вагу').detail)
        self.assertEqual(self.trip.stage_elapsed_display,'щойно')
        self.login_as(self.driver);self.assertContains(self.client.get('/'),'data-stage-since')
        self.trip.status='completed';self.trip.save()
        self.assertEqual(self.trip.stage_elapsed_display,'')
        self.login_as(self.logistic);self.assertNotContains(self.client.get(self.url('trip_detail',self.trip.pk)),'data-stage-since')
    def test_unified_history_keeps_older_events_and_is_private_to_logistics(self):
        for i in range(43): AuditEvent.objects.create(actor=self.logistic,trip=self.trip,action=f'Запис {i}')
        AuditEvent.objects.create(actor=self.logistic,trip=self.trip2,action='Чужа подія')
        self.login_as(self.logistic)
        response=self.client.get(self.url('trip_detail',self.trip.pk))
        self.assertContains(response,'Історія рейсу');self.assertContains(response,'Запис 42');self.assertNotContains(response,'Чужа подія')
        self.assertEqual(response.context['history_page'].paginator.count,43)
        self.assertEqual(len(response.context['events']),40)
        response=self.client.get(self.url('trip_detail',self.trip.pk)+'?history_page=2')
        self.assertContains(response,'Запис 0');self.assertEqual(len(response.context['events']),3)
        self.login_as(self.driver);self.assertNotContains(self.client.get(self.url('trip_detail',self.trip.pk)),'Запис 42')
        self.login_as(self.other);self.assertEqual(self.client.get(self.url('trip_detail',self.trip.pk)).status_code,404)
    def test_create_form_groups_all_fields_and_ignores_removed_distance(self):
        self.login_as(self.logistic)
        response=self.client.get(self.url('trip_create'))
        self.assertNotIn('distance_km',response.context['form'].fields)
        for field in ['vehicle','driver','planned_date','scheduling','notes']: self.assertContains(response,f'name="{field}"',count=1)
        self.assertEqual(len(response.context['route_formset'].forms),2)
        self.assertContains(response,'data-route-editor')
        data={'vehicle':self.vehicle.pk,'driver':self.driver.pk,'scheduling':'queued','origin':'A','destination':'B','cargo':'X','planned_tons':'24','planned_date':timezone.localdate().isoformat(),'distance_km':'420'}
        self.assertEqual(self.client.post(self.url('trip_create'),data).status_code,302)
        created=Trip.objects.latest('pk');self.assertIsNone(created.distance_km)
        self.assertIn(self.driver.full_name,created.events.get().detail)
        data['loading_contact_name']='Контакт';data['loading_contact_phone']='bad'
        response=self.client.post(self.url('trip_create'),data)
        self.assertEqual(response.status_code,200);self.assertContains(response,'Введіть номер у форматі')
        self.assertContains(response,'value="Контакт"')
    def cancel_trip(self, reason='', trip=None, **extra):
        self.login_as(self.logistic)
        return self.client.post(self.url('trip_status',(trip or self.trip).pk),{'status':'cancelled','cancellation_reason':reason,**extra})
    def test_shared_progress_matches_driver_and_dispatcher_and_cannot_forge_acceptance(self):
        for status in ['planned','loading','road','unloading']:
            self.trip.status=status;self.trip.save()
            self.login_as(self.logistic)
            dispatcher=self.client.get(self.url('trip_detail',self.trip.pk))
            self.login_as(self.driver)
            driver=self.client.get('/')
            self.assertEqual(dispatcher.context['stage_steps'],driver.context['steps'])
            self.assertEqual([s['label'] for s in driver.context['steps']],['Призначено','Прийнято','Завантаження','У дорозі','Розвантаження','Завершено'])
            self.assertFalse(driver.context['steps'][1]['done'])
        self.login_as(self.logistic)
        self.client.post(self.url('trip_status',self.trip.pk),{'status':'accepted'})
        self.trip.refresh_from_db();self.assertEqual(self.trip.status,'unloading');self.assertFalse(self.trip.is_accepted)
        self.trip.status='planned';self.trip.save();self.login_as(self.driver);self.accept()
        response=self.client.get('/');self.assertTrue(response.context['steps'][1]['done']);self.assertTrue(response.context['steps'][1]['current'])
    def test_cancellation_notice_survives_reload_and_new_trip_until_confirmed(self):
        self.trip.status='road';self.trip.save()
        self.cancel_trip('Завантаження перенесли')
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status,'cancelled');self.assertEqual(self.trip.cancelled_by,self.logistic)
        self.assertIsNotNone(self.trip.cancelled_at);self.assertTrue(self.trip.cancellation_notice_required)
        self.assertIsNone(self.trip.cancellation_acknowledged_at)
        self.queue_trip()
        self.assertContains(self.client.get(self.url('trip_detail',self.trip.pk)),'Очікуємо підтвердження водія')
        self.login_as(self.driver)
        for _ in range(2):
            response=self.client.get('/')
            self.assertContains(response,'Завантаження перенесли');self.assertContains(response,'Зрозуміло, рейс скасовано')
        self.assertTrue(self.client.get(self.url('driver_updates')).json()['cancellation_pending'])
        request_id=str(uuid.uuid4())
        data={'action':'ack_cancel','request_id':request_id,'expected_revision':self.trip.updated_at.isoformat()}
        first=self.client.post(self.url('driver_action',self.trip.pk),data).json()
        self.assertEqual(first['outcome'],'confirmed')
        self.trip.refresh_from_db();acknowledged=self.trip.cancellation_acknowledged_at
        self.assertIsNotNone(acknowledged)
        self.assertEqual(self.client.post(self.url('driver_action',self.trip.pk),data).json(),first)
        self.client.post(self.url('trip_ack_cancel',self.trip.pk))
        self.trip.refresh_from_db();self.assertEqual(acknowledged,self.trip.cancellation_acknowledged_at)
        self.assertEqual(self.trip.events.filter(action='Водій підтвердив скасування').count(),1)
        self.assertNotContains(self.client.get('/'),'Зрозуміло, рейс скасовано')
        self.assertFalse(self.client.get(self.url('driver_updates')).json()['cancellation_pending'])
        self.login_as(self.logistic)
        self.assertContains(self.client.get(self.url('trip_detail',self.trip.pk)),'Водій підтвердив ознайомлення')
    def test_cancellation_optional_reason_queue_legacy_and_scope(self):
        queued=self.queue_trip()
        self.cancel_trip(trip=queued)
        queued.refresh_from_db();self.assertTrue(queued.cancellation_notice_required);self.assertEqual(queued.cancellation_reason,'')
        self.login_as(self.driver);self.assertContains(self.client.get('/'),'Зрозуміло, рейс скасовано')
        self.login_as(self.other)
        self.assertNotContains(self.client.get('/'),'Зрозуміло, рейс скасовано')
        self.assertFalse(self.client.get(self.url('driver_updates')).json()['cancellation_pending'])
        self.assertEqual(self.client.post(self.url('trip_ack_cancel',queued.pk)).status_code,404)
        self.login_as(self.logistic);self.assertEqual(self.client.post(self.url('trip_ack_cancel',queued.pk)).status_code,403)
        self.login_as(self.driver);self.assertEqual(self.client.get(self.url('trip_ack_cancel',queued.pk)).status_code,405)
        self.client.post(self.url('trip_ack_cancel',queued.pk))
        self.trip.status='cancelled';self.trip.save()
        self.assertNotContains(self.client.get('/'),'Зрозуміло, рейс скасовано')
    def test_invalid_or_stale_cancellation_and_stale_driver_action_do_not_commit(self):
        revision=self.trip.updated_at.isoformat()
        self.cancel_trip('X'*501)
        self.trip.refresh_from_db();self.assertEqual(self.trip.status,'planned');self.assertFalse(self.trip.cancellation_notice_required)
        self.trip.notes='Оновлено';self.trip.save()
        self.cancel_trip('Причина',revision=revision)
        self.trip.refresh_from_db();self.assertEqual(self.trip.status,'planned')
        before_cancel=self.trip.updated_at.isoformat();self.cancel_trip('Лише для цього водія')
        self.login_as(self.driver)
        response=self.client.post(self.url('driver_action',self.trip.pk),{'action':'status','status':'loading','request_id':str(uuid.uuid4()),'expected_revision':before_cancel})
        self.assertEqual(response.json()['outcome'],'rejected')
        self.trip.refresh_from_db();self.assertEqual(self.trip.status,'cancelled');self.assertIsNone(self.trip.cancellation_acknowledged_at)
        self.login_as(self.other);self.assertNotContains(self.client.get('/'),'Лише для цього водія')
    def accept(self,trip=None):
        return self.client.post(self.url('trip_accept',(trip or self.trip).pk),{'acceptance_location':'м. Вінниця, база','acceptance_odometer_km':'150000'})
    def test_substitution_keeps_loaded_trip_vehicle_stage_documents_and_audits_handover(self):
        from fleet.models import TripTransfer
        Vehicle.objects.create(plate='SPARE001',trailer_plate='SPARE101',kind='tent',default_driver=self.spare)
        self.trip.status='road';self.trip.actual_tons=Decimal('24.10');self.trip.ttn_number='ТТН-123';self.trip.save()
        self.login_as(self.driver);self.accept();self.upload()
        document=self.trip.documents.get()
        self.login_as(self.logistic);url=self.url('driver_substitution',self.trip.pk)
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertContains(self.client.get('/'),'Підміна водія')
        revision=response.context['form'].initial['revision']
        effective=timezone.now()
        response=self.client.post(url,{'driver':self.spare.pk,'revision':revision,'location':'Вінниця, стоянка','note':'Попередній водій їде відпочивати','effective_at':effective.isoformat()})
        self.assertRedirects(response,self.url('trip_detail',self.trip.pk),fetch_redirect_response=False)
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.driver_id,self.spare.pk)
        self.assertEqual(self.trip.vehicle_id,self.vehicle.pk)
        self.assertEqual(self.trip.status,'road')
        self.assertEqual(self.trip.actual_tons,Decimal('24.10'))
        self.assertEqual(self.trip.ttn_number,'ТТН-123')
        self.assertEqual(self.trip.documents.get().pk,document.pk)
        transfer=TripTransfer.objects.get(trip=self.trip)
        self.assertEqual(transfer.kind,'substitution')
        self.assertEqual(transfer.from_vehicle_id,transfer.to_vehicle_id)
        self.assertEqual(transfer.previous_driver,self.driver)
        self.assertEqual(transfer.next_driver,self.spare)
        self.assertEqual(transfer.previous_status,'road')
        self.assertEqual(transfer.next_status,'road')
        self.assertEqual(transfer.effective_at,effective)
        self.assertEqual(transfer.location,'Вінниця, стоянка')
        self.assertTrue(self.trip.events.filter(action='Підміна водія',actor=self.logistic,detail__contains='Вінниця').exists())
        response=self.client.get(self.url('vehicle_history',self.vehicle.pk))
        self.assertContains(response,'Вінниця, стоянка');self.assertContains(response,'У дорозі')
        self.vehicle.refresh_from_db();self.assertEqual(self.vehicle.default_driver_id,self.driver.pk)
        self.login_as(self.driver);self.assertEqual(self.client.get(self.url('trip_detail',self.trip.pk)).status_code,404)
        self.login_as(self.spare)
        self.assertContains(self.client.get('/'),'Прийняти рейс')
        self.accept();self.client.post(self.url('trip_status',self.trip.pk),{'status':'unloading'})
        self.trip.refresh_from_db();self.assertEqual(self.trip.status,'unloading')
    def test_substitution_rejects_busy_driver_and_driver_permissions(self):
        self.trip.status='road';self.trip.save();self.login_as(self.logistic)
        url=self.url('driver_substitution',self.trip.pk);response=self.client.get(url)
        self.assertNotIn(self.other,response.context['form'].fields['driver'].queryset)
        data={'driver':self.other.pk,'revision':response.context['form'].initial['revision']}
        self.assertEqual(self.client.post(url,data).status_code,200)
        self.trip.refresh_from_db();self.assertEqual(self.trip.driver_id,self.driver.pk)
        self.assertEqual(self.trip.status,'road');self.assertFalse(self.trip.transfers.exists())
        self.login_as(self.driver)
        self.assertEqual(self.client.get(url).status_code,403)
        self.assertEqual(self.client.post(url,data).status_code,403)
    def test_substitution_stale_form_and_invalid_dates_do_not_change_trip(self):
        self.login_as(self.logistic);url=self.url('driver_substitution',self.trip.pk)
        revision=self.client.get(url).context['form'].initial['revision']
        data={'driver':self.spare.pk,'revision':revision}
        for value in [(timezone.now()+timedelta(days=1)).isoformat(),(self.trip.created_at-timedelta(days=1)).isoformat()]:
            self.assertEqual(self.client.post(url,{**data,'effective_at':value}).status_code,200)
            self.assertFalse(self.trip.transfers.exists())
        self.trip.notes='Рейс уже оновлено';self.trip.save()
        self.assertContains(self.client.post(url,data),'Рейс уже змінився')
        self.trip.refresh_from_db();self.assertEqual(self.trip.driver_id,self.driver.pk)
        self.finish()
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertFalse(self.trip.transfers.exists())
    def test_substitution_double_submission_does_not_duplicate_history(self):
        self.login_as(self.logistic);url=self.url('driver_substitution',self.trip.pk)
        revision=self.client.get(url).context['form'].initial['revision']
        data={'driver':self.spare.pk,'revision':revision}
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.client.post(url,data)
        self.assertEqual(self.trip.transfers.filter(kind='substitution').count(),1)
    def test_dispatcher_edits_contacts_and_driver_gets_new_version_without_status_reset(self):
        self.login_as(self.driver);self.accept()
        self.client.post(self.url('trip_status',self.trip.pk),{'status':'loading'})
        self.trip.refresh_from_db();changed=self.trip.status_changed_at;accepted=self.trip.accepted_at
        version=self.client.get('/driver/updates/').json()['version']
        self.login_as(self.logistic)
        url=self.url('trip_edit',self.trip.pk)
        response=self.client.get(url);self.assertEqual(response.status_code,200)
        self.assertNotIn('vehicle',response.context['form'].fields)
        self.assertNotIn('driver',response.context['form'].fields)
        self.assertNotIn('scheduling',response.context['form'].fields)
        data={key:value if value is not None else '' for key,value in response.context['form'].initial.items()}
        data.update(contact_name='Новий контакт',contact_phone='050 777 77 77',loading_contact_name='Склад контакт',loading_contact_phone='+380508888888',notes='Нова інструкція',status='completed',driver=self.other.pk)
        self.assertRedirects(self.client.post(url,data),self.url('trip_detail',self.trip.pk),fetch_redirect_response=False)
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.contact_phone,'+380507777777')
        self.assertEqual(self.trip.status,'loading')
        self.assertEqual(self.trip.driver_id,self.driver.pk)
        self.assertEqual(self.trip.status_changed_at,changed)
        self.assertEqual(self.trip.accepted_at,accepted)
        event=self.trip.events.get(action='Оновлено інформацію рейсу',detail__contains='Телефон на розвантаженні')
        self.assertEqual(event.actor,self.logistic)
        self.assertIn('+380507777777',event.detail)
        self.login_as(self.driver)
        self.assertNotEqual(self.client.get('/driver/updates/').json()['version'],version)
        response=self.client.get('/')
        self.assertContains(response,'Новий контакт')
        self.assertContains(response,'href="tel:+380507777777"')
        self.assertContains(response,'Нова інструкція')
        self.assertEqual(self.client.get(url).status_code,403)
        self.assertEqual(self.client.post(url,data).status_code,403)
    def test_stale_dispatcher_form_does_not_overwrite_newer_edit(self):
        self.login_as(self.logistic);url=self.url('trip_edit',self.trip.pk)
        form=self.client.get(url).context['form']
        data={key:value if value is not None else '' for key,value in form.initial.items()}
        self.trip.notes='Новіше оновлення';self.trip.save()
        data['notes']='Старі дані'
        response=self.client.post(url,data)
        self.assertContains(response,'Рейс уже змінився')
        self.trip.refresh_from_db();self.assertEqual(self.trip.notes,'Новіше оновлення')
        self.assertFalse(self.trip.events.filter(action='Оновлено інформацію рейсу').exists())
    def test_dispatcher_edit_preserves_queue_and_rejects_invalid_contact(self):
        queued=self.queue_trip();self.login_as(self.logistic);url=self.url('trip_edit',queued.pk)
        form=self.client.get(url).context['form']
        data={key:value if value is not None else '' for key,value in form.initial.items()}
        data.update(contact_name='Контакт без телефону',contact_phone='')
        response=self.client.post(url,data)
        self.assertContains(response,'Додайте телефон контактної особи')
        queued.refresh_from_db();self.assertEqual(queued.contact_name,'')
        data.update(contact_phone='0507777777')
        self.assertEqual(self.client.post(url,data).status_code,302)
        queued.refresh_from_db();self.assertEqual(queued.status,'queued')
    def test_dispatcher_long_note_edit_is_audited_and_same_data_does_not_trigger_update(self):
        self.login_as(self.logistic);url=self.url('trip_edit',self.trip.pk)
        form=self.client.get(url).context['form']
        data={key:value if value is not None else '' for key,value in form.initial.items()}
        before=self.trip.updated_at
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.trip.refresh_from_db();self.assertEqual(self.trip.updated_at,before)
        data['notes']='А'*3000
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.trip.refresh_from_db();self.assertEqual(len(self.trip.notes),3000)
        self.assertLessEqual(len(self.trip.events.get(action='Оновлено інформацію рейсу').detail),1500)
    def test_driver_updates_detect_new_trip_when_home_was_empty(self):
        self.login_as(self.spare)
        response=self.client.get('/')
        self.assertIsNone(response.context['trip'])
        before=response.context['driver_update_version']
        self.assertEqual(self.client.get('/driver/updates/').json()['version'],before)
        self.assertContains(response,'data-updates-url="/driver/updates/"')
        queued=self.queue_trip(driver=self.spare)
        response=self.client.get('/driver/updates/')
        self.assertEqual(response['Cache-Control'],'private, no-store')
        self.assertNotEqual(response.json()['version'],before)
        self.assertEqual(self.client.get('/').context['trip'],queued)
    def test_driver_update_token_is_private_and_ignores_unrelated_driver_changes(self):
        self.login_as(self.driver)
        before=self.client.get('/driver/updates/').json()['version']
        self.trip2.notes='Зміни іншому водію';self.trip2.save()
        self.assertEqual(self.client.get('/driver/updates/').json()['version'],before)
        self.assertEqual(set(self.client.get('/driver/updates/').json()),{'version','cancellation_pending','route_change_pending','assignment_pending'})
        self.trip.notes='Нова інструкція';self.trip.save()
        self.assertNotEqual(self.client.get('/driver/updates/').json()['version'],before)
        self.login_as(self.logistic)
        self.assertEqual(self.client.get('/driver/updates/').status_code,403)
        self.client.logout();self.assertEqual(self.client.get('/driver/updates/').status_code,302)
    def test_driver_updates_detect_documents_added_to_history(self):
        self.finish();self.login_as(self.driver)
        before=self.client.get('/driver/updates/').json()['version']
        self.upload()
        self.assertNotEqual(self.client.get('/driver/updates/').json()['version'],before)
    def test_mobile_action_receipt_retries_once_after_lost_response(self):
        from fleet.models import DriverRequest
        self.login_as(self.driver)
        payload={'action':'accept','acceptance_location':'м. Вінниця, база','acceptance_odometer_km':'150000','request_id':str(uuid.uuid4()),'expected_revision':self.trip.updated_at.isoformat()}
        url=self.url('driver_action',self.trip.pk)
        response=self.client.post(url,payload)
        self.assertEqual(response.json()['outcome'],'confirmed')
        self.trip.refresh_from_db();accepted=self.trip.accepted_at
        self.assertEqual(self.client.post(url,payload).json(),response.json())
        self.trip.refresh_from_db();self.assertEqual(self.trip.accepted_at,accepted)
        self.assertEqual(self.trip.events.filter(action='Водій прийняв рейс').count(),1)
        payload={'action':'status','status':'loading','request_id':str(uuid.uuid4()),'expected_revision':self.trip.updated_at.isoformat()}
        self.assertEqual(self.client.post(url,payload).json()['outcome'],'confirmed')
        self.assertEqual(self.client.post(url,payload).json()['outcome'],'confirmed')
        self.assertEqual(self.trip.events.filter(action='Змінено статус').count(),1)
        self.assertEqual(DriverRequest.objects.count(),2)
        response=self.client.post(url,{**payload,'status':'road'})
        self.assertEqual(response.status_code,400)
        self.trip.refresh_from_db();self.assertEqual(self.trip.status,'loading')
    def test_mobile_action_stale_page_cannot_override_dispatcher_and_is_private(self):
        self.login_as(self.driver)
        payload={'action':'accept','request_id':str(uuid.uuid4()),'expected_revision':self.trip.updated_at.isoformat()}
        self.trip.status='cancelled';self.trip.save()
        response=self.client.post(self.url('driver_action',self.trip.pk),payload)
        self.assertEqual(response.json()['outcome'],'rejected')
        self.trip.refresh_from_db();self.assertFalse(self.trip.is_accepted)
        self.assertEqual(self.client.post(self.url('driver_action',self.trip2.pk),payload).status_code,404)
        self.login_as(self.logistic)
        self.assertEqual(self.client.post(self.url('driver_action',self.trip.pk),payload).status_code,403)
    def test_acceptance_requires_and_records_current_location(self):
        self.login_as(self.driver)
        page=self.client.get('/')
        self.assertContains(page,'Де ви зараз стоїте')
        url=self.url('driver_action',self.trip.pk)
        payload={'action':'accept','request_id':str(uuid.uuid4()),'expected_revision':self.trip.updated_at.isoformat()}
        self.assertEqual(self.client.post(url,payload).json()['outcome'],'rejected')
        self.trip.refresh_from_db(); self.assertFalse(self.trip.is_accepted)
        for bad in ['', '-1', '10000000', 'abc']:
            invalid={**payload,'request_id':str(uuid.uuid4()),'acceptance_location':'м. Вінниця, автобаза','acceptance_odometer_km':bad}
            self.assertEqual(self.client.post(url,invalid).json()['outcome'],'rejected')
            self.trip.refresh_from_db();self.assertFalse(self.trip.is_accepted)
        payload.update(request_id=str(uuid.uuid4()),acceptance_location='м. Вінниця, автобаза',acceptance_odometer_km='150000')
        result=self.client.post(url,payload).json();self.assertEqual(result['outcome'],'confirmed')
        self.trip.refresh_from_db();self.assertEqual(self.trip.acceptance_location,'м. Вінниця, автобаза')
        self.assertEqual(self.trip.acceptance_odometer_km,150000)
        self.assertEqual(self.client.post(url,payload).json(),result)
        self.login_as(self.logistic)
        self.assertContains(self.client.get(self.url('trip_detail',self.trip.pk)),'м. Вінниця, автобаза')

    def test_mobile_completion_reminder_and_confirmation_are_receipted(self):
        self.trip.status='unloading';self.trip.save();self.login_as(self.driver)
        url=self.url('driver_action',self.trip.pk)
        payload={'action':'status','empty_distance_km':'0','loaded_distance_km':'420','fuel_used_l':'135','status':'completed','actual_tons':'24.10','request_id':str(uuid.uuid4()),'expected_revision':self.trip.updated_at.isoformat()}
        response=self.client.post(url,payload)
        self.assertEqual(response.json()['outcome'],'reminder')
        self.assertEqual(self.client.post(url,payload).json(),response.json())
        self.trip.refresh_from_db();self.assertEqual(self.trip.status,'unloading')
        response=self.client.get(response.json()['redirect'])
        self.assertContains(response,'Підписаної ТТН ще немає')
        self.upload(kind='signed_ttn')
        self.trip.refresh_from_db()
        payload.update(request_id=str(uuid.uuid4()),expected_revision=self.trip.updated_at.isoformat(),confirm_without_signed_ttn='1')
        self.assertEqual(self.client.post(url,payload).json()['outcome'],'confirmed')
        self.assertEqual(self.client.post(url,payload).json()['outcome'],'confirmed')
        self.assertEqual(self.trip.events.filter(action='Змінено статус').count(),1)
    def test_multiple_documents_validate_whole_batch_and_retry_without_duplicates(self):
        self.login_as(self.driver);self.accept()
        url=self.url('document_upload',self.trip.pk);key=str(uuid.uuid4())
        def batch(files): return self.client.post(url,{'kind':'signed_ttn','file':files,'request_id':key},HTTP_ACCEPT='application/json')
        self.assertEqual(batch([self.pdf(),SimpleUploadedFile('empty.bin',b'')]).status_code,400)
        self.assertFalse(Document.objects.exists())
        response=batch([self.pdf(),SimpleUploadedFile('unknown.dat',b'unknown contents')])
        self.assertEqual(response.json()['outcome'],'confirmed')
        self.assertEqual(Document.objects.count(),2)
        self.assertEqual(Document.objects.filter(content_type='application/octet-stream').count(),1)
        self.assertEqual(batch([self.pdf(),SimpleUploadedFile('unknown.dat',b'unknown contents')]).json(),response.json())
        self.assertEqual(Document.objects.count(),2)
        self.assertEqual(self.trip.events.filter(action='Додано документ').count(),2)
    def test_multiple_documents_limits_and_encoded_map_links(self):
        self.login_as(self.driver);self.accept()
        response=self.client.post(self.url('document_upload',self.trip.pk),{'kind':'ttn','file':[self.pdf() for _ in range(11)]})
        self.assertEqual(response.status_code,302);self.assertFalse(Document.objects.exists())
        self.trip.origin='Вінниця & склад №1';self.trip.save()
        response=self.client.get('/')
        from urllib.parse import quote
        self.assertIn('query='+quote(self.trip.origin),self.trip.loading_directions_url)
        self.assertNotContains(response,'Пошук за адресою')
        self.assertNotContains(response,'data-location-preview')
    def test_batch_storage_failure_rolls_back_all_documents_and_cleans_files(self):
        from unittest.mock import patch
        self.login_as(self.driver);self.accept()
        original=Document.save
        def fail_second(document,*args,**kwargs):
            if Document.objects.exists(): raise OSError('Storage unavailable')
            return original(document,*args,**kwargs)
        with patch.object(Document,'save',fail_second):
            with self.assertRaises(OSError):
                self.client.post(self.url('document_upload',self.trip.pk),{'kind':'signed_ttn','file':[self.pdf(),self.pdf()],'request_id':str(uuid.uuid4())},HTTP_ACCEPT='application/json')
        self.assertFalse(Document.objects.exists())
        self.assertFalse(self.trip.events.filter(action='Додано документ').exists())
        self.assertEqual([path for path in Path(self.media.name).rglob('*') if path.is_file()],[])
    def test_mobile_action_csrf_and_invalid_weight_never_confirm_success(self):
        self.login_as(self.driver);self.accept();self.trip.refresh_from_db()
        payload={'action':'status','status':'loading','actual_tons':'NaN','request_id':str(uuid.uuid4()),'expected_revision':self.trip.updated_at.isoformat()}
        url=self.url('driver_action',self.trip.pk)
        response=self.client.post(url,payload)
        self.assertEqual(response.json()['outcome'],'rejected')
        self.trip.refresh_from_db();self.assertEqual(self.trip.status,'planned')
        client=Client(enforce_csrf_checks=True);client.force_login(self.driver)
        self.assertEqual(client.post(url,payload).status_code,403)
    def test_finish_reminds_about_signed_ttn_and_preserves_entered_weight(self):
        self.trip.status='unloading';self.trip.save();self.login_as(self.driver)
        changed=self.trip.status_changed_at
        self.upload(kind='ttn')
        response=self.client.post(self.url('trip_status',self.trip.pk),{'empty_distance_km':'0','loaded_distance_km':'420','fuel_used_l':'135','status':'completed','actual_tons':'24.10'})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Підписаної ТТН ще немає')
        self.assertContains(response,'Додати підписану ТТН')
        self.assertContains(response,'спочатку завантажте відсутні документи')
        self.assertContains(response,'value="24.10"')
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status,'unloading');self.assertIsNone(self.trip.completed_at)
        self.assertEqual(self.trip.status_changed_at,changed)
        self.assertIsNone(self.trip.actual_tons)
        self.assertFalse(self.trip.events.filter(action='Змінено статус').exists())
        response=self.client.get('/driver/documents/',{'trip':self.trip.pk,'kind':'signed_ttn'})
        self.assertEqual(response.context['upload_form'].initial['kind'],'signed_ttn')
        self.upload(kind='signed_ttn')
        response=self.client.post(self.url('trip_status',self.trip.pk),{'empty_distance_km':'0','loaded_distance_km':'420','fuel_used_l':'135','status':'completed','actual_tons':'24.10'})
        self.assertEqual(response.status_code,302)
        self.trip.refresh_from_db();self.assertEqual(self.trip.status,'completed')
    def test_finish_can_confirm_late_signed_ttn_and_remains_yellow(self):
        self.trip.status='unloading';self.trip.save();self.login_as(self.driver)
        response=self.client.post(self.url('trip_status',self.trip.pk),{'empty_distance_km':'0','loaded_distance_km':'420','fuel_used_l':'135','actual_tons':'24','status':'completed','confirm_without_signed_ttn':'1'})
        self.assertEqual(response.status_code,200)
        self.trip.refresh_from_db();self.assertEqual(self.trip.status,'unloading')
        self.assertFalse(self.trip.events.filter(action='Змінено статус').exists())
    def test_contact_visible_only_to_assigned_driver_after_acceptance_and_through_route(self):
        self.trip.contact_name='Контактний Представник';self.trip.contact_phone='+380507777777'
        self.trip.loading_contact_name='Представник Складу';self.trip.loading_contact_phone='+380508888888';self.trip.save()
        self.login_as(self.driver)
        self.assertNotContains(self.client.get('/'),'Контактний Представник')
        self.assertNotContains(self.client.get('/'),'Представник Складу')
        self.assertNotContains(self.client.get(self.url('trip_detail',self.trip.pk)),'+380507777777')
        self.accept()
        for status in ['planned','loading','road','unloading']:
            if status!='planned': self.client.post(self.url('trip_status',self.trip.pk),{'status':status})
            response=self.client.get('/')
            self.assertContains(response,'Контактний Представник')
            self.assertContains(response,'href="tel:+380507777777"')
            self.assertContains(response,'Представник Складу')
            self.assertContains(response,'href="tel:+380508888888"')
        self.login_as(self.other)
        self.assertNotContains(self.client.get('/'),'Контактний Представник')
        self.assertNotContains(self.client.get('/'),'Представник Складу')
        self.assertEqual(self.client.get(self.url('trip_detail',self.trip.pk)).status_code,404)
        self.login_as(self.logistic)
        self.assertContains(self.client.get(self.url('trip_detail',self.trip.pk)),'Контактний Представник')
        self.assertContains(self.client.get(self.url('trip_detail',self.trip.pk)),'Представник Складу')
    def test_trip_contact_creation_normalizes_phone_and_validates_pair(self):
        from fleet.forms import TripForm
        data={'vehicle':self.vehicle.pk,'driver':self.driver.pk,'scheduling':'queued','origin':'A','destination':'B','cargo':'X','planned_tons':'24','planned_date':timezone.localdate().isoformat(),'contact_name':'Контактний Представник','contact_phone':'+380 (50) 777-77-77','loading_contact_name':'Представник Складу','loading_contact_phone':'050 888 88 88'}
        form=TripForm(data)
        self.assertTrue(form.is_valid(),form.errors)
        self.assertEqual(form.cleaned_data['contact_phone'],'+380507777777')
        self.login_as(self.logistic)
        response=self.client.post('/trips/new/',data)
        self.assertEqual(response.status_code,302)
        trip=Trip.objects.latest('pk')
        self.assertEqual(trip.contact_name,'Контактний Представник')
        self.assertEqual(trip.contact_phone,'+380507777777')
        self.assertEqual(trip.loading_contact_name,'Представник Складу')
        self.assertEqual(trip.loading_contact_phone,'+380508888888')
        for phone,valid in [('',False),('wrong',False),('0507777777',True),('+48 123 456 789',True)]:
            form=TripForm({**data,'contact_phone':phone})
            self.assertEqual(form.is_valid(),valid,form.errors)
        form=TripForm({**data,'contact_name':''})
        self.assertFalse(form.is_valid());self.assertIn('contact_name',form.errors)
        form=TripForm({**data,'contact_name':'','contact_phone':''})
        self.assertTrue(form.is_valid(),form.errors)
        for changes,field in [({'loading_contact_phone':''},'loading_contact_phone'),({'loading_contact_name':''},'loading_contact_name'),({'loading_contact_phone':'bad'},'loading_contact_phone')]:
            form=TripForm({**data,**changes})
            self.assertFalse(form.is_valid());self.assertIn(field,form.errors)
    def test_driver_upload_requires_acceptance_on_home_documents_and_post(self):
        self.login_as(self.driver)
        self.assertNotContains(self.client.get('/'),'Фото / ТТН')
        response=self.client.get('/driver/documents/',{'trip':self.trip.pk})
        self.assertContains(response,'Спочатку прийміть рейс')
        self.assertNotContains(response,'data-driver-upload')
        self.assertEqual(self.upload().status_code,403)
        self.assertFalse(Document.objects.exists())
        self.accept()
        self.assertContains(self.client.get('/'),'Фото / ТТН')
        self.assertContains(self.client.get('/driver/documents/',{'trip':self.trip.pk}),'data-driver-upload')
        self.assertEqual(self.upload().status_code,302)
        queued=self.queue_trip()
        self.assertEqual(self.upload(queued).status_code,403)
        self.accept(queued)
        self.assertEqual(self.upload(queued).status_code,403)
    def rest(self, state='start', **extra):
        self.driver.refresh_from_db()
        return self.client.post('/driver/rest/action/',{'rest':state,'request_id':str(uuid.uuid4()),
            'expected_revision':self.driver.rest_changed_at.isoformat(),'return_to':'/',**extra})

    def test_driver_rest_preserves_trip_and_is_visible_in_live_fleet_and_history(self):
        self.trip.status='road';self.trip.save();revision=self.trip.updated_at;changed=self.trip.status_changed_at
        self.login_as(self.driver)
        version=self.client.get('/driver/updates/').json()['version']
        result=self.rest().json();self.assertEqual(result['outcome'],'confirmed')
        self.driver.refresh_from_db();self.assertIsNotNone(self.driver.rest_started_at)
        self.trip.refresh_from_db();self.assertEqual(self.trip.status,'road')
        self.assertEqual(self.trip.updated_at,revision);self.assertEqual(self.trip.status_changed_at,changed)
        self.assertNotEqual(self.client.get('/driver/updates/').json()['version'],version)
        self.assertContains(self.client.get('/'),'Ви відпочиваєте')
        self.assertContains(self.client.get('/'),'Завершити відпочинок і продовжити роботу')
        self.login_as(self.logistic)
        self.assertIn('Відпочиває',self.client.get('/?live=1').json()['cards'])
        self.assertContains(self.client.get(self.url('trip_detail',self.trip.pk)),'Початок відпочинку')
        self.login_as(self.driver);self.assertEqual(self.rest('end').json()['outcome'],'confirmed')
        self.driver.refresh_from_db();self.assertIsNone(self.driver.rest_started_at)
        self.assertTrue(self.trip.events.filter(action='Завершення відпочинку').exists())
        self.login_as(self.logistic);self.assertNotIn('Відпочиває',self.client.get('/?live=1').json()['cards'])

    def test_rest_retries_do_not_duplicate_or_override_later_resume(self):
        self.login_as(self.driver)
        data={'rest':'start','request_id':str(uuid.uuid4()),'expected_revision':self.driver.rest_changed_at.isoformat(),'return_to':'/'}
        first=self.client.post('/driver/rest/action/',data).json()
        self.assertEqual(self.client.post('/driver/rest/action/',data).json(),first)
        self.assertEqual(self.trip.events.filter(action='Початок відпочинку').count(),1)
        self.assertEqual(self.rest('end').json()['outcome'],'confirmed')
        self.assertEqual(self.client.post('/driver/rest/action/',data).json(),first)
        self.driver.refresh_from_db();self.assertIsNone(self.driver.rest_started_at)
        data['request_id']=str(uuid.uuid4())
        self.assertEqual(self.client.post('/driver/rest/action/',data).json()['outcome'],'rejected')

    def test_rest_without_trip_private_receipts_and_safe_redirect(self):
        self.trip.status='completed';self.trip.save()
        self.login_as(self.driver)
        result=self.rest(return_to='https://example.com/').json()
        self.assertEqual(result['redirect'],'/')
        self.assertTrue(AuditEvent.objects.filter(actor=self.driver,trip=None,action='Початок відпочинку').exists())
        self.assertContains(self.client.get('/driver/documents/'),'Завершити відпочинок і продовжити роботу')
        from fleet.models import DriverRequest
        receipt=DriverRequest.objects.filter(user=self.driver,kind='rest').get()
        self.login_as(self.other)
        result=self.client.post('/driver/rest/action/',{'request_id':str(receipt.pk),'rest':'start',
            'expected_revision':self.driver.rest_changed_at.isoformat()}).json()
        self.assertEqual(result['outcome'],'rejected')
        self.other.refresh_from_db();self.assertIsNone(self.other.rest_started_at)
        self.login_as(self.logistic);self.assertEqual(self.rest().status_code,403)
        self.login_as(self.driver);self.assertEqual(self.client.get('/driver/rest/action/').status_code,405)

    def test_dispatcher_can_set_every_stage_on_legacy_trip(self):
        self.login_as(self.logistic)
        for stage in ['accepted','unloading','loading','road','completed','planned']:
            self.trip.refresh_from_db()
            self.assertEqual(self.client.post(f'/trips/{self.trip.pk}/stage/',{
                'stage':stage,'revision':self.trip.updated_at.isoformat()}).status_code,302)
            self.trip.refresh_from_db()
            self.assertEqual(self.trip.status,'planned' if stage=='accepted' else stage)
            if stage=='accepted':
                self.assertTrue(self.trip.is_accepted);self.assertEqual(self.trip.accepted_by,self.logistic)
            if stage=='planned':
                self.assertFalse(self.trip.is_accepted);self.assertIsNone(self.trip.completed_at)

    def test_driver_history_is_chronological_across_completed_and_cancelled(self):
        now=timezone.now()
        attrs=dict(vehicle=self.vehicle,driver=self.driver,origin='A',destination='B',cargo='X',planned_tons=20,
                   planned_date=timezone.localdate()-timedelta(days=10),created_by=self.logistic)
        oldest=Trip.objects.create(**attrs,status='cancelled',cancelled_at=now-timedelta(days=4))
        completed=Trip.objects.create(**attrs,status='completed',completed_at=now-timedelta(days=2))
        cancelled=Trip.objects.create(**attrs,status='cancelled',cancelled_at=now-timedelta(days=1))
        newest=Trip.objects.create(**attrs,status='completed',completed_at=now)
        # Later edits to an old trip must not move it above newer history.
        oldest.notes='Уточнено документи';oldest.save()
        self.login_as(self.driver)
        response=self.client.get('/')
        self.assertEqual([t.pk for t in response.context['history_trips']],
                         [newest.pk,cancelled.pk,completed.pk,oldest.pk])
        self.assertContains(response,f'{timezone.localtime(cancelled.cancelled_at):%d.%m.%Y}')
        legacy=Trip.objects.create(**attrs,status='cancelled')
        response=self.client.get('/')
        self.assertEqual(list(response.context['history_trips'])[-1].pk,legacy.pk)

    def test_driver_history_color_requires_signed_ttn_not_any_document(self):
        self.finish();self.login_as(self.driver)
        self.assertContains(self.client.get('/'),'history-missing')
        self.upload(kind='ttn')
        self.assertContains(self.client.get('/'),'history-missing')
        self.assertNotContains(self.client.get('/'),'history-signed')
        self.upload(kind='signed_ttn')
        self.assertContains(self.client.get('/'),'history-signed')
        self.assertContains(self.client.get('/'),'✓ Підписана ТТН є')
        self.assertNotContains(self.client.get('/'),'history-missing')
    def test_acceptance_is_explicit_private_idempotent_and_reset_on_reassignment(self):
        self.login_as(self.driver)
        self.assertContains(self.client.get('/'),'Прийняти рейс')
        self.client.post(self.url('trip_status',self.trip.pk),{'status':'loading'})
        self.trip.refresh_from_db();self.assertEqual(self.trip.status,'planned')
        self.assertIsNone(self.trip.accepted_at)
        self.assertEqual(self.accept(self.trip2).status_code,404)
        self.assertEqual(self.client.get(self.url('trip_accept',self.trip.pk)).status_code,405)
        self.accept();self.trip.refresh_from_db()
        accepted=self.trip.accepted_at
        self.assertTrue(self.trip.is_accepted)
        self.assertContains(self.client.get('/'),'Приїхав на завантаження')
        self.accept();self.trip.refresh_from_db()
        self.assertEqual(self.trip.accepted_at,accepted)
        self.assertEqual(self.trip.events.filter(action='Водій прийняв рейс').count(),1)
        self.login_as(self.logistic)
        self.assertIn('Рейс прийнято',self.client.get('/?live=1').json()['cards'])
        self.assertEqual(self.accept().status_code,403)
        self.client.post(self.url('assign_driver',self.trip.pk),{'driver':self.spare.pk})
        self.trip.refresh_from_db();self.assertFalse(self.trip.is_accepted)
        self.login_as(self.spare)
        self.assertContains(self.client.get('/'),'Прийняти рейс')
    def test_completed_trip_is_collapsed_into_own_history(self):
        self.finish();self.login_as(self.driver)
        response=self.client.get('/')
        self.assertIsNone(response.context['trip'])
        self.assertContains(response,'Історія рейсів · 1')
        self.assertNotContains(response,'class="driver-task')
        self.assertContains(response,'Додати підписану ТТН')
        self.assertNotContains(response,'TEST0002')
        response=self.client.get(self.url('trip_detail',self.trip.pk))
        self.assertContains(response,'driver-history-selected')
        self.assertNotContains(response,'class="driver-task')
    def test_queue_cannot_be_accepted_while_current_trip_is_running(self):
        queued=self.queue_trip();self.login_as(self.driver)
        self.accept(queued);queued.refresh_from_db()
        self.assertFalse(queued.is_accepted)
        self.assertEqual(queued.status,'queued')
        self.assertIsNone(queued.completed_at)
        self.assertEqual(self.client.get('/').context['trip'],self.trip)
    def test_dispatcher_live_fleet_is_private_and_reflects_driver_updates(self):
        self.assertEqual(self.client.get('/?live=1').status_code,302)
        self.login_as(self.driver)
        self.assertEqual(self.client.get('/?live=1').status_code,403)
        self.accept()
        self.trip.refresh_from_db()
        before=self.trip.status_changed_at
        self.client.post(self.url('trip_status',self.trip.pk),{'status':'loading'})
        self.trip.refresh_from_db()
        self.assertGreater(self.trip.status_changed_at,before)
        changed=self.trip.status_changed_at
        self.login_as(self.logistic)
        response=self.client.get('/?live=1')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response['Cache-Control'],'private, no-store')
        self.assertIn('badge loading',response.json()['cards'])
        from fleet.views import fleet_event_time
        self.assertIn(fleet_event_time(changed),response.json()['cards'])
        self.assertIn('Завантажуюсь',response.json()['cards'])
        self.client.post(self.url('trip_status',self.trip.pk),{'status':'loading'})
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status_changed_at,changed)
        self.upload()
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status_changed_at,changed)
    def test_live_fleet_completion_shows_free_since_completion(self):
        self.login_as(self.logistic)
        self.client.post(self.url('trip_status',self.trip.pk),{'empty_distance_km':'0','loaded_distance_km':'420','fuel_used_l':'135','status':'completed'})
        self.trip.refresh_from_db()
        response=self.client.get('/?live=1')
        self.assertIn('badge free',response.json()['cards'])
        from fleet.views import fleet_event_time
        self.assertIn(fleet_event_time(self.trip.status_changed_at),response.json()['cards'])
        self.assertIn('Вільні авто',response.json()['metrics'])
    def test_mobile_driver_home_keeps_current_trip_and_one_next_action(self):
        queued=Trip.objects.create(vehicle=self.vehicle,driver=self.driver,origin='Наступне місто',destination='Київ',cargo='Пшениця',planned_tons=24,planned_date=timezone.localdate(),created_by=self.logistic,status='queued')
        self.login_as(self.driver)
        response=self.client.get('/')
        self.assertEqual(response.context['trip'],self.trip)
        self.assertContains(response,'Прийняти рейс')
        self.assertContains(response,'Наступне місто')
        self.assertContains(response,'driver-bottom-nav')
        self.assertNotContains(response,'Користувачі')
        self.assertNotContains(response,'TEST0002')
        self.accept()
        response=self.client.post(self.url('trip_status',self.trip.pk),{'status':'loading','driver_mobile':'1'})
        self.assertRedirects(response,'/',fetch_redirect_response=False)
        self.assertContains(self.client.get('/'),'Завантажився — виїжджаю')
        self.assertEqual(self.client.get(self.url('trip_detail',queued.pk)).context['trip'],queued)
    def test_mobile_documents_are_private_and_upload_returns_to_selected_trip(self):
        self.login_as(self.driver)
        self.accept()
        response=self.client.get('/driver/documents/',{'trip':self.trip.pk})
        self.assertContains(response,'capture="environment"')
        self.assertContains(response,'TEST0001')
        self.assertNotContains(response,'TEST0002')
        for value in [str(self.trip2.pk),'invalid']:
            self.assertEqual(self.client.get('/driver/documents/',{'trip':value}).status_code,404)
        response=self.upload()
        self.assertRedirects(response,f'/driver/documents/?trip={self.trip.pk}',fetch_redirect_response=False)
        self.assertContains(self.client.get('/driver/documents/',{'trip':self.trip.pk}),'✓ Підписана ТТН')
        self.assertTrue(self.trip.documents.filter(original_name='signed.pdf').exists())
        self.login_as(self.logistic)
        self.assertEqual(self.client.get('/driver/documents/').status_code,403)
    def url(self,name,pk=None): return reverse(name,args=[pk] if pk is not None else [])
    def pdf(self): return SimpleUploadedFile('signed.pdf',b'%PDF-1.4\nTest document\n%%EOF',content_type='application/pdf')
    def upload(self,trip=None,kind='signed_ttn',file=None):
        return self.client.post(self.url('document_upload',(trip or self.trip).pk),{'kind':kind,'file':file or self.pdf()})
    def finish(self,trip=None):
        t=trip or self.trip;t.status='completed';t.completed_at=timezone.now();t.save();return t
    def test_phone_normalization_and_password_hash(self):
        self.assertEqual(normalize_phone('050 444-44-44'),self.driver.phone)
        self.assertNotEqual(self.driver.password,self.password)
        self.assertTrue(self.driver.check_password(self.password))
    def test_anonymous_redirects_from_private_pages(self):
        for path in ['/', '/trips/', f'/trips/{self.trip.pk}/','/accounts/','/report/']:
            self.assertEqual(self.client.get(path).status_code,302)
    def test_phone_login(self):
        r=self.client.post('/login/',{'username':'050 444 44 44','password':self.password})
        self.assertRedirects(r,'/',fetch_redirect_response=False)
        self.assertEqual(int(self.client.session['_auth_user_id']),self.driver.pk)
    def test_login_throttle_persistent_and_success_not_consumed(self):
        for i in range(10): self.assertEqual(self.client.post('/login/',{'username':self.driver.phone,'password':'wrong'}).status_code,200)
        self.assertEqual(self.client.post('/login/',{'username':self.driver.phone,'password':self.password}).status_code,429)
        LoginThrottle.objects.all().update(window_start=timezone.now()-timedelta(minutes=16))
        self.assertEqual(self.client.post('/login/',{'username':self.driver.phone,'password':self.password}).status_code,302)
        self.assertEqual(LoginThrottle.objects.get().failures,0)
    def test_forced_password_change(self):
        self.spare.must_change_password=True;self.spare.save()
        self.login_as(self.spare)
        self.assertRedirects(self.client.get('/'),'/password/',fetch_redirect_response=False)
        self.assertRedirects(self.client.post(self.url('document_upload',self.trip.pk),{}),'/password/',fetch_redirect_response=False)
        r=self.client.post('/password/',{'old_password':self.password,'new_password1':'My-New-Private-Password-9382!','new_password2':'My-New-Private-Password-9382!'})
        self.assertEqual(r.status_code,302)
        self.spare.refresh_from_db();self.assertFalse(self.spare.must_change_password)
    def test_driver_only_sees_own_trip(self):
        self.login_as(self.driver)
        r=self.client.get('/trips/');self.assertContains(r,f'?trip={self.trip.pk}');self.assertNotContains(r,f'?trip={self.trip2.pk}')
        self.assertEqual(self.client.get(self.url('trip_detail',self.trip2.pk)).status_code,404)
        self.assertEqual(self.client.post(self.url('trip_status',self.trip2.pk),{'status':'loading'}).status_code,404)
        self.assertEqual(self.upload(self.trip2).status_code,404)
    def test_driver_cannot_create_reassign_manage_users_or_repairs(self):
        self.login_as(self.driver)
        self.assertEqual(self.client.get('/trips/new/').status_code,403)
        self.assertEqual(self.client.get('/accounts/').status_code,403)
        self.assertEqual(self.client.post(self.url('assign_driver',self.trip.pk),{'driver':self.spare.pk}).status_code,403)
        self.assertEqual(self.client.post(self.url('vehicle_repair',self.vehicle.pk)).status_code,403)
    def test_logistic_has_full_administrator_access(self):
        self.login_as(self.logistic)
        self.assertEqual(self.client.get('/accounts/').status_code,200)
        self.assertEqual(self.client.post(self.url('trip_status',self.trip.pk),{'status':'road'}).status_code,302)
        self.trip.refresh_from_db();self.assertEqual(self.trip.status,'road')
    def test_supported_roles(self):
        from fleet.forms import AccountForm
        self.assertEqual(set(dict(User.Role.choices)), {'admin', 'moderator', 'driver'})
        self.assertEqual(set(dict(AccountForm().fields['role'].choices)) - {''}, {'admin', 'moderator', 'driver'})
        for role in ['manager', 'logistic', 'unknown']:
            with self.assertRaises(IntegrityError), transaction.atomic():
                User.objects.filter(pk=self.spare.pk).update(role=role)
    def test_driver_does_not_receive_other_people_or_admin_data(self):
        AuditEvent.objects.create(actor=self.logistic, trip=self.trip, action='Змінено водія', detail=self.other.full_name)
        self.login_as(self.driver)
        for path in ['/', '/trips/', self.url('trip_detail', self.trip.pk)]:
            response=self.client.get(path)
            self.assertEqual(response.status_code,200)
            for text in [self.other.full_name, self.other.phone, self.logistic.full_name, 'TEST0002', 'Користувачі', 'Історія дій']:
                self.assertNotContains(response,text)
        response=self.client.get(self.url('trip_detail', self.trip.pk))
        self.assertIsNone(response.context['assignment_form'])
        self.assertEqual(response.context['events'], [])
        response=self.client.get('/')
        self.assertEqual(response.context['vehicle_count'],0)
    def test_driver_role_does_not_gain_access_from_superuser_flag(self):
        self.driver.is_superuser=True;self.driver.save(update_fields=['is_superuser'])
        self.login_as(self.driver)
        self.assertEqual(self.client.get('/accounts/').status_code,403)
        self.assertEqual(self.client.get(self.url('trip_detail',self.trip2.pk)).status_code,404)
    def test_driver_does_not_see_previous_driver_or_uploader(self):
        self.login_as(self.logistic);self.upload()
        self.client.post(self.url('assign_driver',self.trip.pk),{'driver':self.spare.pk})
        self.login_as(self.spare)
        response=self.client.get(self.url('trip_detail',self.trip.pk))
        self.assertNotContains(response,self.driver.full_name)
        self.assertNotContains(response,self.logistic.full_name)
        self.assertEqual(response.status_code,200)
    def test_driver_must_follow_sequence(self):
        self.login_as(self.driver)
        self.client.post(self.url('trip_status',self.trip.pk),{'empty_distance_km':'0','loaded_distance_km':'420','fuel_used_l':'135','status':'completed'})
        self.trip.refresh_from_db();self.assertEqual(self.trip.status,'planned')
        self.accept()
        self.upload(kind='signed_ttn')
        for status in ['loading','road','unloading','completed']:
            self.client.post(self.url('trip_status',self.trip.pk),{'status':status,'actual_tons':'24.10','confirm_without_signed_ttn':'1','empty_distance_km':'0','loaded_distance_km':'420','fuel_used_l':'135'})
        self.trip.refresh_from_db();self.assertEqual(self.trip.status,'completed');self.assertEqual(self.trip.actual_tons,Decimal('24.10'));self.assertIsNotNone(self.trip.completed_at)
        self.assertEqual(self.trip.events.filter(action='Змінено статус').count(),4)
        self.assertEqual(self.trip.events.filter(action='Водій прийняв рейс').count(),1)
        self.assertEqual(self.trip.events.filter(action='Оновлено фактичну вагу').count(),1)
    def test_invalid_weight_is_not_saved(self):
        self.login_as(self.driver)
        self.accept()
        for weight in ['NaN','Infinity','-2','61','24.1234']:
            self.client.post(self.url('trip_status',self.trip.pk),{'status':'loading','actual_tons':weight})
            self.trip.refresh_from_db();self.assertEqual(self.trip.status,'planned');self.assertIsNone(self.trip.actual_tons)
    def test_reassignment_revokes_old_driver_access(self):
        self.login_as(self.logistic)
        self.assertEqual(self.client.post(self.url('assign_driver',self.trip.pk),{'driver':self.spare.pk}).status_code,302)
        self.login_as(self.driver);self.assertEqual(self.client.get(self.url('trip_detail',self.trip.pk)).status_code,404)
        self.login_as(self.spare);self.assertEqual(self.client.get(self.url('trip_detail',self.trip.pk)).status_code,200)
    def test_busy_driver_receives_transfer_as_next_queue_on_his_current_vehicle(self):
        existing=self.queue_trip(vehicle=self.vehicle2,driver=self.other,days=0)
        self.trip.planned_date=timezone.localdate()+timedelta(days=10);self.trip.save()
        self.login_as(self.driver);old_version=self.client.get('/driver/updates/').json()['version']
        self.login_as(self.other);new_version=self.client.get('/driver/updates/').json()['version']
        self.login_as(self.logistic);self.client.post(self.url('assign_driver',self.trip.pk),{'driver':self.other.pk})
        self.trip.refresh_from_db();self.trip2.refresh_from_db()
        self.assertEqual(self.trip.driver_id,self.other.pk)
        self.assertEqual(self.trip.vehicle_id,self.vehicle2.pk)
        self.assertEqual(self.trip.status,'queued')
        self.assertEqual(self.trip2.status,'planned')
        self.assertEqual(Trip.ordered_queue(self.vehicle2.trips.filter(status='queued')).first(),self.trip)
        self.assertFalse(self.trip.is_accepted)
        self.assertEqual(self.trip.transfers.count(),1)
        cards=self.client.get('/').context['cards']
        self.assertIsNone(next(card for card in cards if card['vehicle']==self.vehicle)['trip'])
        self.assertEqual(next(card for card in cards if card['vehicle']==self.vehicle2)['trip'],self.trip2)
        self.login_as(self.driver)
        self.assertNotEqual(self.client.get('/driver/updates/').json()['version'],old_version)
        self.assertEqual(self.client.get(self.url('trip_detail',self.trip.pk)).status_code,404)
        self.login_as(self.other)
        self.assertNotEqual(self.client.get('/driver/updates/').json()['version'],new_version)
        self.assertEqual(self.client.get('/').context['trip'],self.trip2)
        self.assertEqual(self.client.get('/').context['queued_trips'][0],self.trip)
        self.finish(self.trip2)
        from fleet.queue_promotion import promote_ready
        promote_ready();self.accept(self.trip)
        self.client.post(self.url('trip_start',existing.pk));existing.refresh_from_db();self.assertEqual(existing.status,'queued')
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status,'planned');self.assertIsNone(self.trip.queue_priority_at)
    def test_free_driver_transfer_moves_actual_vehicle_and_keeps_old_vehicle_document_history(self):
        from fleet.models import TripTransfer
        self.finish(self.trip2)
        self.login_as(self.logistic);self.upload()
        self.client.post(self.url('assign_driver',self.trip.pk),{'driver':self.other.pk})
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.driver_id,self.other.pk)
        self.assertEqual(self.trip.vehicle_id,self.vehicle2.pk)
        self.assertEqual(self.trip.status,'planned')
        self.assertEqual(self.trip.documents.count(),1)
        transfer=TripTransfer.objects.get(trip=self.trip)
        self.assertEqual(transfer.previous_driver,self.driver)
        self.assertEqual(transfer.from_vehicle,self.vehicle)
        self.assertEqual(transfer.to_vehicle,self.vehicle2)
        self.assertEqual(transfer.moved_by,self.logistic)
        response=self.client.get(self.url('vehicle_history',self.vehicle.pk))
        self.assertIn(self.trip,list(response.context['trips']))
        self.assertContains(response,'Передачі рейсів')
        cards=self.client.get('/').context['cards']
        active_card=next(card for card in cards if card['vehicle']==self.vehicle2)
        self.assertEqual(active_card['trip'],self.trip)
        self.vehicle.refresh_from_db();self.vehicle2.refresh_from_db()
        self.assertEqual(self.vehicle.default_driver_id,self.driver.pk)
        self.assertEqual(self.vehicle2.default_driver_id,self.other.pk)
    def test_reassigning_same_driver_is_noop_and_in_progress_transfer_requires_acknowledgement(self):
        self.login_as(self.driver);self.accept()
        self.client.post(self.url('trip_status',self.trip.pk),{'status':'loading'})
        self.trip.refresh_from_db();accepted=self.trip.accepted_at;changed=self.trip.updated_at
        self.login_as(self.logistic)
        self.client.post(self.url('assign_driver',self.trip.pk),{'driver':self.driver.pk})
        self.trip.refresh_from_db();self.assertEqual(self.trip.updated_at,changed)
        self.assertEqual(self.trip.accepted_at,accepted)
        self.assertFalse(self.trip.transfers.exists())
        self.client.post(self.url('assign_driver',self.trip.pk),{'driver':self.spare.pk})
        self.login_as(self.spare)
        self.assertContains(self.client.get('/'),'Прийняти рейс')
        self.client.post(self.url('trip_status',self.trip.pk),{'status':'road'})
        self.trip.refresh_from_db();self.assertEqual(self.trip.status,'loading')
        self.assertEqual(self.upload().status_code,403)
        self.accept()
        self.client.post(self.url('trip_status',self.trip.pk),{'status':'road'})
        self.trip.refresh_from_db();self.assertEqual(self.trip.status,'road')
    def test_transfer_to_repair_vehicle_is_queued_and_older_request_cannot_override_transfer(self):
        self.finish(self.trip2)
        self.vehicle2.in_repair=True;self.vehicle2.save()
        self.login_as(self.driver)
        payload={'action':'accept','request_id':str(uuid.uuid4()),'expected_revision':self.trip.updated_at.isoformat()}
        self.login_as(self.logistic)
        self.client.post(self.url('assign_driver',self.trip.pk),{'driver':self.other.pk})
        self.trip.refresh_from_db();self.assertEqual(self.trip.status,'queued')
        self.login_as(self.driver)
        self.assertEqual(self.client.post(self.url('driver_action',self.trip.pk),payload).status_code,404)
        self.login_as(self.other);self.accept()
        self.client.post(self.url('trip_start',self.trip.pk))
        self.trip.refresh_from_db();self.assertEqual(self.trip.status,'queued')
    def test_db_blocks_duplicate_active_vehicle_and_driver(self):
        attrs=dict(origin='A',destination='B',cargo='X',planned_tons=20,planned_date=timezone.localdate(),created_by=self.logistic)
        with self.assertRaises(IntegrityError),transaction.atomic(): Trip.objects.create(vehicle=self.vehicle,driver=self.spare,**attrs)
        new=Vehicle.objects.create(plate='TEST0003',trailer_plate='TEST1003',kind='tent')
        with self.assertRaises(IntegrityError),transaction.atomic(): Trip.objects.create(vehicle=new,driver=self.driver,**attrs)
    def test_create_trip_on_free_vehicle(self):
        self.finish();self.login_as(self.logistic)
        r=self.client.post('/trips/new/',{'vehicle':self.vehicle.pk,'driver':self.driver.pk,'origin':'Гнівань','destination':'Одеса','cargo':'Добрива','planned_tons':'24','planned_date':timezone.localdate().isoformat(),'distance_km':'420','ttn_number':'42','notes':'Дзвонити перед заїздом'})
        self.assertEqual(r.status_code,302)
        self.assertEqual(Trip.objects.filter(vehicle=self.vehicle).count(),2)
    def test_upload_private_file_and_access(self):
        self.login_as(self.driver);self.accept();r=self.upload();self.assertEqual(r.status_code,302)
        d=Document.objects.get();self.assertTrue(Path(d.file.path).is_file());self.assertNotIn('signed.pdf',d.file.name)
        r=self.client.get(self.url('document_download',d.pk));self.assertEqual(r.status_code,200);self.assertIn('attachment',r['Content-Disposition'])
        # Consume the test client's streaming wrapper so request cleanup keeps
        # the enclosing TestCase transaction open on PostgreSQL.
        self.assertTrue(b''.join(r.streaming_content).startswith(b'%PDF-'))
        self.login_as(self.other);self.assertEqual(self.client.get(self.url('document_download',d.pk)).status_code,404)
        self.client.logout();self.assertEqual(self.client.get(self.url('document_download',d.pk)).status_code,302)
        self.assertEqual(self.client.get('/private-media/'+d.file.name).status_code,404)
    def test_upload_real_image_and_reject_fake_extension(self):
        self.login_as(self.driver)
        self.accept()
        data=BytesIO();Image.new('RGB',(10,10)).save(data,format='PNG')
        self.upload(file=SimpleUploadedFile('weight.png',data.getvalue(),content_type='image/png'))
        self.assertEqual(Document.objects.count(),1)
        self.upload(file=SimpleUploadedFile('evil.png',b'<script>bad()</script>',content_type='image/png'))
        self.upload(file=SimpleUploadedFile('evil.html',b'%PDF-fake',content_type='application/pdf'))
        self.assertEqual(Document.objects.filter(content_type='application/octet-stream').count(),2)
    def test_upload_iphone_images_with_unreliable_filename_and_heic(self):
        import pillow_heif
        self.login_as(self.driver);self.accept()
        image=Image.new('RGB',(10,10),'white')
        jpeg=BytesIO();image.save(jpeg,format='JPEG')
        self.upload(file=SimpleUploadedFile('image',jpeg.getvalue(),content_type='application/octet-stream'))
        heic=BytesIO();pillow_heif.register_heif_opener();image.save(heic,format='HEIF')
        self.upload(file=SimpleUploadedFile('IMG_1234.HEIC',heic.getvalue(),content_type='image/heic'))
        docs=list(Document.objects.order_by('pk'))
        self.assertEqual(len(docs),2)
        for doc in docs:
            self.assertEqual(doc.content_type,'image/jpeg')
            self.assertTrue(doc.original_name.lower().endswith(('.jpg','.jpeg')))
            with Image.open(doc.file) as saved: self.assertEqual(saved.format,'JPEG')
    def test_jpeg_upload_without_extension(self):
        self.login_as(self.driver);self.accept()
        data=BytesIO();Image.new('RGB',(10,10)).save(data,format='JPEG')
        self.upload(file=SimpleUploadedFile('image',data.getvalue(),content_type='application/octet-stream'))
        doc=Document.objects.get()
        self.assertEqual(doc.content_type,'image/jpeg')
        self.assertTrue(doc.original_name.endswith('.jpg'))
    def test_unknown_file_is_download_only_and_can_be_removed(self):
        self.login_as(self.driver);self.accept()
        payload=b'unknown contents from phone'
        self.upload(file=SimpleUploadedFile('IMG7596.jpeg',payload,content_type='image/jpeg'))
        doc=Document.objects.get()
        self.assertEqual(doc.content_type,'application/octet-stream')
        self.assertEqual(self.client.get(self.url('document_inline',doc.pk)).status_code,404)
        response=self.client.get(self.url('document_download',doc.pk))
        self.assertIn('attachment',response['Content-Disposition'])
        self.assertEqual(b''.join(response.streaming_content),payload)
        self.assertContains(self.client.get(self.url('document_preview',doc.pk)),'попередній перегляд недоступний')
        self.assertEqual(self.client.post(self.url('driver_remove_photo',doc.pk)).status_code,302)
    def test_iphone_photo_retry_does_not_create_duplicate(self):
        import pillow_heif
        self.login_as(self.driver);self.accept()
        pillow_heif.register_heif_opener()
        image=BytesIO();Image.new('RGB',(10,10),'white').save(image,format='HEIF')
        request_id=str(uuid.uuid4())
        def send():
            return self.client.post(self.url('document_upload',self.trip.pk),{'kind':'ttn','request_id':request_id,'file':SimpleUploadedFile('IMG_1234.HEIC',image.getvalue(),content_type='image/heic')},HTTP_ACCEPT='application/json')
        first=send();second=send()
        self.assertEqual(first.status_code,200)
        self.assertEqual(second.json(),first.json())
        self.assertEqual(Document.objects.count(),1)
    @override_settings(MAX_DOCUMENT_BYTES=10)
    def test_reject_large_file(self):
        self.login_as(self.driver);self.accept();self.upload();self.assertFalse(Document.objects.exists())
    def test_completed_trip_accepts_late_ttn_and_clears_missing(self):
        self.finish();self.login_as(self.driver)
        self.assertContains(self.client.get('/trips/?status=missing'),f'?trip={self.trip.pk}')
        self.upload()
        self.assertNotContains(self.client.get('/trips/?status=missing'),f'?trip={self.trip.pk}')
    def test_cancelled_trip_cannot_upload(self):
        self.trip.status='cancelled';self.trip.save();self.login_as(self.driver)
        self.assertEqual(self.upload().status_code,403)
    def test_csrf_required_on_mutations(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.driver)
        self.assertEqual(client.post(self.url('trip_status',self.trip.pk),{'status':'loading'}).status_code,403)
        self.assertEqual(client.post('/logout/').status_code,403)
    def test_report_totals_scope_and_csv_formula_escape(self):
        t=self.finish();t.actual_tons=Decimal('24.10');t.origin='=HYPERLINK("bad")';t.distance_km=120;t.save()
        self.finish(self.trip2)
        self.login_as(self.driver)
        r=self.client.get('/report/');self.assertEqual(r.context['count'],1);self.assertEqual(r.context['totals']['tons'],Decimal('24.10'))
        r=self.client.get('/report/?format=csv');text=r.content.decode('utf-8-sig');self.assertIn("'=HYPERLINK",text);self.assertNotIn('TEST0002',text)
        self.login_as(self.logistic);self.assertEqual(self.client.get('/report/').context['count'],2)

    def test_report_distance_and_fuel_totals_keep_missing_values_distinct(self):
        trip=self.finish();trip.distance_km=540;trip.fuel_used_l=Decimal('125.50');trip.save()
        self.finish(self.trip2)
        self.login_as(self.logistic)
        response=self.client.get('/report/')
        self.assertEqual(response.context['totals']['distance'],540)
        self.assertEqual(response.context['totals']['fuel'],Decimal('125.50'))
        self.assertEqual(response.context['totals']['missing_distance'],1)
        self.assertEqual(response.context['totals']['missing_fuel'],1)
        self.assertContains(response,'Пробіг, км')
        self.assertContains(response,'Пальне, л')
        filtered=self.client.get('/report/',{'vehicle':self.vehicle2.pk})
        self.assertIsNone(filtered.context['totals']['distance'])
        self.assertIsNone(filtered.context['totals']['fuel'])
    def test_invalid_report_dates(self):
        self.login_as(self.driver)
        for query in ['?start=bad','?start=2026-10-25&end=2026-10-20']:
            self.assertEqual(self.client.get('/report/'+query).status_code,200)
    def test_report_filters_totals_and_csv_match_without_duplicate_documents(self):
        self.finish();self.finish(self.trip2)
        self.trip.actual_tons=Decimal('24.10');self.trip.original_received=True;self.trip.save()
        self.login_as(self.logistic)
        self.upload(kind='signed_ttn');self.upload(kind='signed_ttn')
        response=self.client.get('/report/')
        self.assertEqual(response.context['count'],2)
        self.assertEqual(response.context['totals']['tons'],Decimal('24.10'))
        self.assertEqual(response.context['totals']['missing_weight'],1)
        self.assertEqual(response.context['totals']['missing_signed'],1)
        filters={'driver':self.driver.pk,'vehicle':self.vehicle.pk,'kind':'tent','q':'Гнівань','signed':'yes','original':'yes','weight':'yes'}
        response=self.client.get('/report/',filters)
        self.assertEqual(response.context['count'],1)
        self.assertEqual(response.context['totals']['tons'],Decimal('24.10'))
        response=self.client.get('/report/',{**filters,'format':'csv'})
        rows=list(csv.reader(StringIO(response.content.decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(len(rows),2);self.assertEqual(rows[1][0],str(self.trip.pk))
        self.assertEqual(rows[1][10],'24,100')
        response=self.client.get('/report/',{'weight':'no','signed':'no','original':'no','kind':'grain'})
        self.assertEqual(response.context['count'],1);self.assertIsNone(response.context['totals']['tons'])
        self.assertContains(response,'Не внесена');self.assertContains(response,reverse('analytics'))
    def test_removed_original_control_does_not_filter_or_change_existing_data(self):
        self.finish(); self.finish(self.trip2)
        self.trip.original_received=True; self.trip.save()
        self.login_as(self.logistic)
        for path in ['/report/','/report/analytics/']:
            response=self.client.get(path,{'original':'yes'})
            self.assertEqual(response.status_code,200)
            self.assertNotContains(response,'Паперовий оригінал')
            self.assertNotContains(response,'Оригінал:')
        self.assertEqual(self.client.get('/report/',{'original':'yes'}).context['count'],2)
        response=self.client.get('/report/',{'format':'csv'})
        self.assertNotIn('Оригінал',response.content.decode('utf-8-sig'))
        self.assertNotContains(self.client.get(self.url('trip_detail',self.trip.pk)),'Позначити отриманим')
        self.assertEqual(self.client.post(self.url('original_received',self.trip.pk)).status_code,410)
        self.trip.refresh_from_db();self.assertTrue(self.trip.original_received)

    def test_loading_and_signed_ttn_statuses_stay_separate_everywhere(self):
        from fleet.routes import fleet_ttn_summary
        self.finish()
        for kinds,expected in [([],['missing','missing']),(['ttn'],['complete','missing']),(['signed_ttn'],['missing','complete']),(['ttn','signed_ttn'],['complete','complete'])]:
            with self.subTest(kinds=kinds):
                Document.objects.filter(trip=self.trip).delete()
                for kind in kinds:
                    Document.objects.create(trip=self.trip,kind=kind,file='test.pdf',original_name='test.pdf',size=1,content_type='application/pdf',uploaded_by=self.logistic)
                self.trip.refresh_from_db()
                self.assertEqual([x['state'] for x in fleet_ttn_summary(self.trip)],expected)
                self.login_as(self.logistic)
                for url in [reverse('trip_detail',args=[self.trip.pk]),reverse('trip_list'),reverse('report')]:
                    page=self.client.get(url)
                    self.assertContains(page,'ТТН завантаження')
                    self.assertContains(page,'Підписана ТТН розвантаження')
                response=self.client.get('/report/',{'format':'csv'})
                rows=list(csv.reader(StringIO(response.content.decode('utf-8-sig')),delimiter=';'))
                row=next(x for x in rows[1:] if x[0]==str(self.trip.pk))
                self.assertEqual([row[12],row[13]],['Завантажено' if x=='complete' else 'Не завантажено' for x in expected])
                self.login_as(self.driver)
                docs=self.client.get('/driver/documents/',{'trip':self.trip.pk})
                self.assertEqual([x['state'] for x in docs.context['document_ttn_summary']],expected)
                self.assertContains(docs,'ТТН завантаження');self.assertContains(docs,'Підписана ТТН розвантаження')
                self.assertContains(self.client.get('/'),'ТТН завантаження')
                self.assertContains(self.client.get('/'),'Підписана ТТН розвантаження')

    def test_report_shows_loading_and_unloading_documents_separately(self):
        from fleet.models import TripStop
        self.finish(); self.trip.route_enabled=True; self.trip.save()
        load=TripStop.objects.create(trip=self.trip,position=1,kind='load',address='Склад',cargo='Зерно',planned_tons=10)
        TripStop.objects.create(trip=self.trip,position=2,kind='unload',address='Отримувач',cargo='Зерно',planned_tons=10)
        Document.objects.create(trip=self.trip,stop=load,kind='ttn',file='test.pdf',original_name='test.pdf',content_type='application/pdf',size=1,uploaded_by=self.logistic)
        self.login_as(self.logistic)
        response=self.client.get('/report/')
        documents=response.context['trips'][0].report_documents
        self.assertEqual([(d['state'],d['text']) for d in documents],[('complete','Завантажено'),('missing','Не завантажено')])
        self.assertContains(response,'ТТН завантаження')
        self.assertContains(response,'Підписана ТТН розвантаження')

    def test_report_invalid_filters_never_export_or_expose_other_driver_choices(self):
        self.finish();self.finish(self.trip2);self.login_as(self.driver)
        response=self.client.get('/report/')
        self.assertEqual(list(response.context['form'].fields['driver'].queryset),[self.driver])
        self.assertNotContains(response,'Редагувати')
        for data in [{'driver':self.other.pk},{'vehicle':self.vehicle2.pk},{'kind':'bad'},{'weight':'bad'},{'start':'bad'}]:
            response=self.client.get('/report/',{**data,'format':'csv'})
            self.assertTrue(response.context['error']);self.assertEqual(response.context['count'],0)
            self.assertNotIn('Content-Disposition',response)
    def report_edit_data(self):
        self.trip.refresh_from_db()
        return {field:getattr(self.trip,field) if getattr(self.trip,field) is not None else '' for field in ['origin','destination','cargo','planned_tons','actual_tons','completed_at','ttn_number']} | {'revision':self.trip.updated_at.isoformat()}
    def test_report_edit_updates_source_totals_audit_and_returns_to_filters(self):
        self.finish();self.login_as(self.logistic)
        data=self.report_edit_data()
        back='/report/?driver='+str(self.driver.pk)+'&weight=no&page=1'
        data.update(actual_tons='23.80',origin='Нова адреса',original_received='on',return_url=back)
        response=self.client.post(self.url('report_edit',self.trip.pk),data)
        self.assertRedirects(response,back,fetch_redirect_response=False)
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.actual_tons,Decimal('23.80'));self.assertEqual(self.trip.origin,'Нова адреса')
        self.assertFalse(self.trip.original_received)
        self.assertEqual(self.trip.events.filter(action='Уточнено дані звіту').count(),2)
        self.assertEqual(self.client.get('/report/').context['totals']['tons'],Decimal('23.80'))
        self.assertEqual(self.client.get('/report/?weight=no').context['count'],0)
        self.assertContains(self.client.get(self.url('trip_detail',self.trip.pk)),'Уточнено дані звіту')
    def test_completed_report_keeps_vehicle_and_driver_identity_after_changes(self):
        self.finish(); self.login_as(self.logistic)
        old_plate,old_trailer,old_name=self.vehicle.plate,self.vehicle.trailer_plate,self.driver.full_name
        self.vehicle.plate='NEW9001'; self.vehicle.trailer_plate='NEW9002'; self.vehicle.kind='grain'; self.vehicle.default_driver=self.spare; self.vehicle.save()
        self.driver.full_name='Змінене ім’я водія'; self.driver.save()
        self.trip.refresh_from_db()
        self.assertEqual((self.trip.report_tractor_plate,self.trip.report_trailer_plate,self.trip.report_driver_name),(old_plate,old_trailer,old_name))
        response=self.client.get('/report/',{'kind':'tent'})
        self.assertEqual(response.context['count'],1)
        for value in (old_plate,old_trailer,old_name): self.assertContains(response,value)
        self.assertNotContains(response,'<td><b>NEW9001</b>')
        rows=list(csv.reader(StringIO(self.client.get('/report/',{'kind':'tent','format':'csv'}).content.decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(rows[1][2:6],[old_plate,old_trailer,'Тент',old_name])
        response=self.client.get(reverse('analytics'),{'kind':'tent'})
        self.assertContains(response,old_plate); self.assertContains(response,old_trailer); self.assertContains(response,old_name)
        self.assertEqual(response.context['total']['trips'],1)
        self.assertEqual(self.client.get(reverse('analytics'),{'kind':'grain'}).context['total']['trips'],0)

    def test_transfer_existing_trailer_preserves_completed_reports(self):
        from fleet.models import Trailer,TrailerTransfer
        from fleet.views import queue_block_reason
        self.finish(); self.finish(self.trip2); self.login_as(self.logistic)
        old_trailer=self.vehicle.trailer_plate; selected=self.vehicle2.trailer
        queued=Trip.objects.create(vehicle=self.vehicle2,driver=self.other,origin='А',destination='Б',cargo='Тест',planned_tons=10,planned_date=timezone.localdate(),created_by=self.logistic,status='queued',accepted_at=timezone.now(),accepted_by=self.other)
        url=reverse('vehicle_trailer_change',args=[self.vehicle.pk])
        self.assertContains(self.client.get('/'),'Змінити причіп')
        self.assertContains(self.client.get(url),selected.plate)
        result=self.client.post(url,{'previous_trailer':old_trailer,'trailer':selected.pk,'note':'Поломка'})
        self.assertEqual(result.status_code,302)
        self.vehicle.refresh_from_db(); self.vehicle2.refresh_from_db(); self.trip.refresh_from_db(); self.trip2.refresh_from_db()
        self.assertEqual(self.vehicle.trailer_plate,selected.plate); self.assertIsNone(self.vehicle2.trailer_plate)
        self.assertEqual(self.vehicle.kind,selected.kind)
        self.assertIsNone(self.vehicle2.trailer_id); self.assertEqual(self.vehicle.trailer_id,selected.pk)
        self.assertEqual(self.trip.report_trailer_plate,old_trailer)
        self.assertEqual(self.trip2.report_trailer_plate,selected.plate)
        self.assertTrue(Trailer.objects.filter(plate=old_trailer,vehicle__isnull=True).exists())
        self.assertEqual(TrailerTransfer.objects.filter(trailer=selected,from_vehicle=self.vehicle2,to_vehicle=self.vehicle).count(),1)
        self.assertIn('не закріплено причіп',queue_block_reason(queued))
        from fleet.queue_promotion import promote_trip
        self.assertFalse(promote_trip(queued.pk)); queued.refresh_from_db(); self.assertEqual(queued.status,'queued')
        self.assertContains(self.client.get('/'),'Не закріплено')
        self.assertContains(self.client.get('/report/'),old_trailer)
        result=self.client.post(url,{'previous_trailer':old_trailer,'trailer':selected.pk})
        self.assertContains(result,'Причіп уже змінився')
        self.assertContains(self.client.get(reverse('vehicle_history',args=[self.vehicle2.pk])),selected.plate)
        self.login_as(self.driver); self.assertEqual(self.client.get(url).status_code,403)

    def test_trailer_change_forbids_active_target_or_source(self):
        self.login_as(self.logistic); url=reverse('vehicle_trailer_change',args=[self.vehicle.pk]); selected=self.vehicle2.trailer
        result=self.client.post(url,{'previous_trailer':self.vehicle.trailer_plate,'trailer':selected.pk})
        self.assertContains(result,'після завершення поточного рейсу')
        self.finish(); result=self.client.post(url,{'previous_trailer':self.vehicle.trailer_plate,'trailer':selected.pk})
        self.assertContains(result,'Причіп закріплений за тягачем у рейсі')
        self.vehicle.refresh_from_db(); self.vehicle2.refresh_from_db()
        self.assertNotEqual(self.vehicle.trailer_id,selected.pk); self.assertEqual(self.vehicle2.trailer_id,selected.pk)

    def test_create_trailer_independently_and_choose_it(self):
        from fleet.models import Trailer
        self.login_as(self.logistic); self.finish()
        response=self.client.post(reverse('trailer_create'),{'plate':'АВ 1234 ХG','kind':'grain','vehicle':self.vehicle.pk})
        self.assertRedirects(response,reverse('vehicle_trailer_change',args=[self.vehicle.pk]),fetch_redirect_response=False)
        trailer=Trailer.objects.get(plate='AB1234XG')
        result=self.client.post(reverse('vehicle_trailer_change',args=[self.vehicle.pk]),{'previous_trailer':self.vehicle.trailer_plate,'trailer':trailer.pk})
        self.assertEqual(result.status_code,302); self.vehicle.refresh_from_db(); self.assertEqual(self.vehicle.trailer_id,trailer.pk); self.assertEqual(self.vehicle.kind,'grain')

    def test_report_edit_hides_originals_and_preserves_stored_values(self):
        from fleet.models import TripStop
        self.finish(); self.trip.route_enabled=True; self.trip.original_received=True; self.trip.save()
        point=TripStop.objects.create(trip=self.trip,position=1,kind='unload',address='Пункт',cargo='Зерно',planned_tons=10,original_received=True)
        self.login_as(self.logistic); url=self.url('report_edit',self.trip.pk)
        self.assertNotContains(self.client.get(url),'оригінал',html=False)
        data=self.report_edit_data(); data['distance_km']='200'; data[f'original_stop_{point.pk}']=''
        self.assertEqual(self.client.post(url,data).status_code,302)
        point.refresh_from_db(); self.trip.refresh_from_db()
        self.assertTrue(point.original_received);self.assertTrue(self.trip.original_received)
        self.assertEqual(self.trip.distance_km,200)

    def test_report_edit_rejects_stale_invalid_and_unauthorized_changes(self):
        self.login_as(self.logistic)
        self.assertEqual(self.client.get(self.url('report_edit',self.trip.pk)).status_code,404)
        self.finish();data=self.report_edit_data();url=self.url('report_edit',self.trip.pk)
        for updates in [{'actual_tons':'61'},{'completed_at':(timezone.now()+timedelta(days=1)).isoformat()},{'completed_at':(self.trip.created_at-timedelta(days=1)).isoformat()}]:
            response=self.client.post(url,{**data,**updates})
            self.assertEqual(response.status_code,200);self.assertTrue(response.context['form'].errors)
        self.trip.notes='Інша зміна';self.trip.save()
        self.assertContains(self.client.post(url,{**data,'actual_tons':'22'}),'Рейс уже змінився')
        self.trip.refresh_from_db();self.assertIsNone(self.trip.actual_tons)
        self.login_as(self.driver);self.assertEqual(self.client.get(url).status_code,403)
        self.assertEqual(self.client.post(url,data).status_code,403)
        csrf_client=Client(enforce_csrf_checks=True);csrf_client.force_login(self.logistic)
        self.assertEqual(csrf_client.post(url,data).status_code,403)
    def test_report_edit_noop_keeps_completion_precision_and_blocks_external_redirect(self):
        self.finish();self.login_as(self.logistic);data=self.report_edit_data()
        original=self.trip.completed_at
        data['completed_at']=timezone.localtime(original).strftime('%Y-%m-%dT%H:%M:%S')
        data['return_url']='https://example.org/'
        response=self.client.post(self.url('report_edit',self.trip.pk),data)
        self.assertRedirects(response,'/report/',fetch_redirect_response=False)
        self.trip.refresh_from_db();self.assertEqual(self.trip.completed_at,original)
        self.assertFalse(self.trip.events.filter(action='Уточнено дані звіту').exists())
    def test_report_pagination_keeps_filters_and_csv_exports_every_match(self):
        self.finish();self.login_as(self.logistic)
        Trip.objects.bulk_create([Trip(vehicle=self.vehicle,driver=self.driver,origin='Гнівань',destination='Порт',cargo='X',planned_tons=20,planned_date=timezone.localdate(),status='completed',completed_at=timezone.now(),created_by=self.logistic) for _ in range(51)])
        response=self.client.get('/report/',{'kind':'tent','page':2})
        self.assertEqual(response.context['count'],52);self.assertEqual(len(response.context['trips']),2)
        self.assertIn('kind=tent',response.context['filter_query'])
        response=self.client.get('/report/',{'kind':'tent','page':2,'format':'csv'})
        self.assertEqual(len(list(csv.reader(StringIO(response.content.decode('utf-8-sig')),delimiter=';'))),53)
    def test_admin_account_creation_and_reset(self):
        self.login_as(self.admin)
        r=self.client.post('/accounts/',{'full_name':'Новий Тест','phone':'0507777777','role':'driver'})
        self.assertEqual(r.status_code,200);u=User.objects.get(phone='+380507777777');self.assertTrue(u.must_change_password);self.assertTrue(u.check_password(r.context['temporary']))
        old=u.password;r=self.client.post(self.url('account_action',u.pk),{'action':'reset'});u.refresh_from_db();self.assertNotEqual(u.password,old);self.assertEqual(r['Cache-Control'],'no-store')
        self.assertEqual(self.client.post(self.url('account_action',self.admin.pk),{'action':'reset'}).status_code,403)
    def test_cannot_disable_driver_with_active_trip(self):
        self.login_as(self.admin);self.client.post(self.url('account_action',self.driver.pk),{'action':'toggle'})
        self.driver.refresh_from_db();self.assertTrue(self.driver.is_active)
        self.client.post(self.url('account_action',self.spare.pk),{'action':'toggle'})
        self.spare.refresh_from_db();self.assertFalse(self.spare.is_active)
    def test_original_ttn_and_repair_control(self):
        self.login_as(self.logistic)
        self.assertEqual(self.client.post(self.url('original_received',self.trip.pk)).status_code,410);self.trip.refresh_from_db();self.assertFalse(self.trip.original_received)
        self.client.post(self.url('vehicle_repair',self.vehicle.pk));self.vehicle.refresh_from_db();self.assertFalse(self.vehicle.in_repair)
        self.finish();self.client.post(self.url('vehicle_repair',self.vehicle.pk),{'reason':'Планове ТО','operation':'start'});self.vehicle.refresh_from_db();self.assertTrue(self.vehicle.in_repair)
    def test_post_only_actions(self):
        self.login_as(self.admin)
        for name,pk in [('trip_status',self.trip.pk),('assign_driver',self.trip.pk),('document_upload',self.trip.pk),('account_action',self.driver.pk),('trip_start',self.trip.pk),('original_received',self.trip.pk)]: self.assertEqual(self.client.get(self.url(name,pk)).status_code,405)
        self.assertEqual(self.client.get('/logout/').status_code,405)
    def test_health(self): self.assertEqual(self.client.get('/health/').json(),{'status':'ok'})
    def test_admin_templates(self):
        self.login_as(self.admin)
        for path in ['/','/trips/','/trips/new/','/accounts/','/report/',self.url('trip_detail',self.trip.pk)]: self.assertEqual(self.client.get(path).status_code,200)
    def test_import_atomic_and_repeatable(self):
        with tempfile.TemporaryDirectory() as folder:
            f=Path(folder)/'roster.csv';f.write_text('full_name,phone,vehicle_plate,trailer_plate,kind\nІмпорт Тест,0508888888,TEST0008,TEST1008,grain\n',encoding='utf-8')
            call_command('import_fleet',str(f),stdout=StringIO());call_command('import_fleet',str(f),stdout=StringIO())
            self.assertEqual(Vehicle.objects.filter(plate='TEST0008').count(),1);self.assertFalse(User.objects.get(phone='+380508888888').has_usable_password())
            f.write_text('full_name,phone,vehicle_plate,trailer_plate,kind\nТест,0509999999,,,\nТест,bad,,,\n',encoding='utf-8')
            with self.assertRaises(CommandError): call_command('import_fleet',str(f),stdout=StringIO())
            self.assertFalse(User.objects.filter(phone='+380509999999').exists())
    def test_password_reset_invalidates_existing_driver_session(self):
        driver_client=Client();driver_client.force_login(self.spare)
        self.assertEqual(driver_client.get('/').status_code,200)
        self.login_as(self.admin)
        self.client.post(self.url('account_action',self.spare.pk),{'action':'reset'})
        self.assertEqual(driver_client.get('/').status_code,302)
    def test_login_rejects_external_redirect(self):
        r=self.client.post('/login/?next=https://example.com/steal',{'username':self.driver.phone,'password':self.password})
        self.assertEqual(r.url,'/')

    def test_admin_can_add_vehicle_and_assign_driver(self):
        self.login_as(self.admin)
        response=self.client.post('/vehicles/new/', {'plate':'АВ 1234 КХ','trailer_plate':'АВ 5678 ХG','kind':'grain','default_driver':self.spare.pk})
        self.assertRedirects(response,'/accounts/',fetch_redirect_response=False)
        vehicle=Vehicle.objects.get(plate='AB1234KX')
        self.assertEqual(vehicle.trailer_plate,'AB5678XG')
        self.assertEqual(vehicle.default_driver_id,self.spare.pk)
        self.assertContains(self.client.get('/accounts/'), 'AB1234KX')
    def test_vehicle_management_restricted_to_admin(self):
        self.assertEqual(self.client.get('/vehicles/new/').status_code,302)
        self.login_as(self.driver)
        for path in ['/vehicles/new/',f'/vehicles/{self.vehicle.pk}/edit/']:
            self.assertEqual(self.client.get(path).status_code,403)
            self.assertEqual(self.client.post(path,{}).status_code,403)
    def test_default_driver_change_preserves_current_trip(self):
        self.login_as(self.logistic)
        response=self.client.post(f'/vehicles/{self.vehicle.pk}/edit/',{'plate':self.vehicle.plate,'trailer_plate':self.vehicle.trailer_plate,'kind':'tent','default_driver':self.spare.pk})
        self.assertEqual(response.status_code,302)
        self.vehicle.refresh_from_db();self.trip.refresh_from_db()
        self.assertEqual(self.vehicle.default_driver_id,self.spare.pk)
        self.assertEqual(self.trip.driver_id,self.driver.pk)
        self.login_as(self.spare)
        self.assertEqual(self.client.get(self.url('trip_detail',self.trip.pk)).status_code,404)
    def test_vehicle_form_rejects_occupied_driver_and_duplicate_plates(self):
        self.login_as(self.admin)
        base={'plate':'TEST0009','trailer_plate':'TEST1009','kind':'tent','default_driver':self.driver.pk}
        self.assertEqual(self.client.post('/vehicles/new/',base).status_code,200)
        self.assertFalse(Vehicle.objects.filter(plate='TEST0009').exists())
        for field,value in [('plate',self.vehicle.plate),('trailer_plate',self.vehicle.trailer_plate)]:
            data={**base,'default_driver':'',field:value}
            response=self.client.post('/vehicles/new/',data)
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.context['form'].errors)
        with self.assertRaises(IntegrityError),transaction.atomic():
            Vehicle.objects.create(plate='TEST0009',trailer_plate='TEST1009',kind='tent',default_driver=self.driver)
    def test_vehicle_can_be_unassigned_and_added_without_driver(self):
        self.login_as(self.admin)
        self.client.post(f'/vehicles/{self.vehicle.pk}/edit/', {'plate':self.vehicle.plate,'trailer_plate':self.vehicle.trailer_plate,'kind':'tent','default_driver':''})
        self.vehicle.refresh_from_db();self.assertIsNone(self.vehicle.default_driver_id)
        response=self.client.post('/vehicles/new/',{'plate':'TEST0009','trailer_plate':'TEST1009','kind':'grain','default_driver':''})
        self.assertEqual(response.status_code,302)
    def test_vehicle_numbers_in_historical_trips_are_preserved(self):
        self.login_as(self.admin)
        response=self.client.post(f'/vehicles/{self.vehicle.pk}/edit/',{'plate':'TEST0009','trailer_plate':self.vehicle.trailer_plate,'kind':'tent','default_driver':self.driver.pk})
        self.assertEqual(response.status_code,200)
        self.vehicle.refresh_from_db();self.assertEqual(self.vehicle.plate,'TEST0001')
    def test_new_trip_suggests_permanent_driver_but_allows_substitute(self):
        self.finish();self.login_as(self.admin)
        response=self.client.get(f'/trips/new/?vehicle={self.vehicle.pk}')
        self.assertEqual(response.context['form'].initial['driver'],self.driver)
        self.assertContains(response,f'data-driver="{self.driver.pk}"')
        response=self.client.post('/trips/new/',{'vehicle':self.vehicle.pk,'driver':self.spare.pk,'origin':'A','destination':'B','cargo':'X','planned_tons':'24','planned_date':timezone.localdate().isoformat()})
        self.assertEqual(response.status_code,302)
        self.assertEqual(Trip.objects.latest('id').driver_id,self.spare.pk)
        self.vehicle.refresh_from_db();self.assertEqual(self.vehicle.default_driver_id,self.driver.pk)

    def test_dispatcher_fleet_groups_and_availability(self):
        Vehicle.objects.create(plate='TEST0003',trailer_plate='TEST1003',kind='tent',in_repair=True)
        Vehicle.objects.create(plate='TEST0004',trailer_plate='TEST1004',kind='grain')
        self.login_as(self.logistic)
        response=self.client.get('/')
        groups=response.context['fleet_groups']
        self.assertEqual([group['label'] for group in groups],['Тенти','Зерновози'])
        self.assertEqual([{card['vehicle'].plate for card in group['cards']} for group in groups],[{'TEST0001','TEST0003'},{'TEST0002','TEST0004'}])
        self.assertEqual(response.context['free_count'],1)
        self.assertEqual(response.context['repair_count'],1)
        self.assertEqual(response.context['active_count'],2)
        for text in ['ОПЕРАТИВНИЙ ОГЛЯД','fleet-welcome','sidebar-note','За кожним рейсом','fleet-landscape.svg']:
            self.assertNotContains(response,text)

    def change_vehicle_driver(self, driver, effective_at=None, note=''):
        return self.client.post(f'/vehicles/{self.vehicle.pk}/edit/',{
            'plate':self.vehicle.plate,'trailer_plate':self.vehicle.trailer_plate,'kind':'tent',
            'default_driver':driver.pk if driver else '',
            'effective_at':timezone.localtime(effective_at).strftime('%Y-%m-%dT%H:%M') if effective_at else '',
            'assignment_note':note})
    def test_assignment_history_keeps_dates_actor_and_documents(self):
        from fleet.models import VehicleAssignment
        self.login_as(self.admin);self.upload()
        document=Document.objects.get()
        original_path=document.file.name
        handover=timezone.now().replace(second=0,microsecond=0)-timedelta(days=7)
        self.assertEqual(self.change_vehicle_driver(self.spare,handover,'Відпустка основного водія').status_code,302)
        rows=list(self.vehicle.assignments.order_by('id'))
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0].driver_id,self.driver.pk)
        self.assertIsNone(rows[0].started_at)
        self.assertEqual(rows[0].ended_at,handover)
        self.assertEqual(rows[0].ended_by_id,self.admin.pk)
        self.assertEqual(rows[1].started_at,handover)
        self.assertEqual(rows[1].recorded_by_id,self.admin.pk)
        self.assertEqual(rows[1].note,'Відпустка основного водія')
        self.trip.refresh_from_db();document.refresh_from_db()
        self.assertEqual(self.trip.driver_id,self.driver.pk)
        self.assertEqual(document.file.name,original_path)
        self.assertTrue(Path(document.file.path).is_file())
        response=self.client.get(f'/vehicles/{self.vehicle.pk}/history/')
        self.assertContains(response,self.driver.full_name)
        self.assertContains(response,self.spare.full_name)
        self.assertContains(response,'Документів: 1')
        self.assertEqual(VehicleAssignment.objects.filter(ended_at__isnull=True).count(),1)
    def test_assignment_history_does_not_duplicate_unchanged_driver(self):
        self.login_as(self.admin)
        self.change_vehicle_driver(self.spare)
        self.change_vehicle_driver(self.spare)
        self.assertEqual(self.vehicle.assignments.count(),2)
    def test_invalid_handover_dates_roll_back_driver_and_history(self):
        self.login_as(self.admin)
        first=timezone.now().replace(second=0,microsecond=0)-timedelta(days=2)
        self.change_vehicle_driver(self.spare,first)
        for invalid in [first-timedelta(days=1),timezone.now()+timedelta(days=1)]:
            response=self.change_vehicle_driver(self.driver,invalid)
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.context['form'].errors)
            self.vehicle.refresh_from_db();self.assertEqual(self.vehicle.default_driver_id,self.spare.pk)
            self.assertEqual(self.vehicle.assignments.count(),2)
            self.assertEqual(self.vehicle.assignments.filter(ended_at__isnull=True).get().driver_id,self.spare.pk)
    def test_unassigning_keeps_reason_and_rejects_overlapping_return(self):
        self.login_as(self.admin)
        self.change_vehicle_driver(self.spare,timezone.now()-timedelta(days=4))
        end=timezone.now().replace(second=0,microsecond=0)-timedelta(days=1)
        self.assertEqual(self.change_vehicle_driver(None,end,'Пересів на інший тягач').status_code,302)
        last=self.vehicle.assignments.latest('id')
        self.assertEqual(last.ended_note,'Пересів на інший тягач')
        self.assertEqual(last.ended_at,end)
        self.assertFalse(self.vehicle.assignments.filter(ended_at__isnull=True).exists())
        response=self.change_vehicle_driver(self.driver,end-timedelta(days=1))
        self.assertEqual(response.status_code,200)
        self.vehicle.refresh_from_db();self.assertIsNone(self.vehicle.default_driver_id)
    def test_vehicle_history_private_to_logistics(self):
        self.assertEqual(self.client.get(f'/vehicles/{self.vehicle.pk}/history/').status_code,302)
        self.login_as(self.driver)
        self.assertEqual(self.client.get(f'/vehicles/{self.vehicle.pk}/history/').status_code,403)
    def test_driver_cannot_be_backdated_onto_overlapping_other_vehicle(self):
        from fleet.assignment_history import record_assignment
        self.login_as(self.admin)
        start=timezone.now()-timedelta(days=5)
        self.change_vehicle_driver(self.spare,start)
        end=timezone.now()-timedelta(days=1)
        self.change_vehicle_driver(None,end)
        response=self.client.post(f'/vehicles/{self.vehicle2.pk}/edit/',{
            'plate':self.vehicle2.plate,'trailer_plate':self.vehicle2.trailer_plate,'kind':'grain',
            'default_driver':self.spare.pk,'effective_at':timezone.localtime(end-timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')})
        self.assertEqual(response.status_code,200)
        self.vehicle2.refresh_from_db();self.assertEqual(self.vehicle2.default_driver_id,self.other.pk)
        self.assertFalse(self.vehicle2.assignments.exists())

    def queue_trip(self, vehicle=None, driver=None, days=1):
        return Trip.objects.create(vehicle=vehicle or self.vehicle,driver=driver or self.driver,
            origin='Наступне місто',destination='Порт',cargo='Добрива',planned_tons='22',
            planned_date=timezone.localdate()+timedelta(days=days),status='queued',created_by=self.logistic)
    def test_repair_requires_reason_and_keeps_start_finish_history(self):
        from fleet.models import VehicleRepair
        self.finish();self.login_as(self.admin)
        url=self.url('vehicle_repair',self.vehicle.pk)
        self.assertEqual(self.client.get(url).status_code,200)
        self.assertEqual(self.client.post(url,{'reason':''}).status_code,200)
        self.vehicle.refresh_from_db();self.assertFalse(self.vehicle.in_repair)
        self.assertFalse(VehicleRepair.objects.exists())
        start=timezone.now().replace(second=0,microsecond=0)-timedelta(days=3)
        self.assertEqual(self.client.post(url,{'operation':'start','reason':'Заміна зчеплення','started_at':timezone.localtime(start).strftime('%Y-%m-%dT%H:%M')}).status_code,302)
        repair=VehicleRepair.objects.get()
        self.assertEqual(repair.created_by_id,self.admin.pk)
        self.assertEqual(repair.started_at,start)
        self.assertEqual(self.client.post(url,{'operation':'start','reason':'Повторний запит'}).status_code,200)
        self.vehicle.refresh_from_db();self.assertTrue(self.vehicle.in_repair)
        self.assertEqual(VehicleRepair.objects.count(),1)
        self.assertEqual(self.client.post(url,{'operation':'finish','result':'Зчеплення замінено'}).status_code,302)
        repair.refresh_from_db();self.vehicle.refresh_from_db()
        self.assertFalse(self.vehicle.in_repair);self.assertIsNotNone(repair.finished_at)
        self.assertEqual(repair.finished_by_id,self.admin.pk)
        self.assertEqual(repair.result,'Зчеплення замінено')
        self.assertContains(self.client.get(self.url('vehicle_history',self.vehicle.pk)),'Заміна зчеплення')
    def test_repair_dates_and_busy_vehicle_are_protected(self):
        from fleet.models import VehicleRepair
        self.login_as(self.admin)
        url=self.url('vehicle_repair',self.vehicle.pk)
        self.client.post(url,{'reason':'Проблема з гальмами'})
        self.vehicle.refresh_from_db();self.assertFalse(self.vehicle.in_repair)
        self.finish()
        future=timezone.localtime(timezone.now()+timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        self.client.post(url,{'reason':'ТО','started_at':future})
        self.assertFalse(VehicleRepair.objects.exists())
        self.client.post(url,{'reason':'ТО'})
        before=timezone.localtime(timezone.now()-timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        self.client.post(url,{'operation':'finish','finished_at':before})
        self.vehicle.refresh_from_db();self.assertTrue(self.vehicle.in_repair)
        self.assertIsNone(VehicleRepair.objects.get().finished_at)
    def test_logistic_can_queue_busy_vehicle_and_driver(self):
        self.login_as(self.logistic)
        response=self.client.post('/trips/new/',{'vehicle':self.vehicle.pk,'driver':self.driver.pk,
            'origin':'Наступне місто','destination':'Порт','cargo':'X','planned_tons':'22',
            'planned_date':timezone.localdate().isoformat(),'scheduling':'queued'})
        self.assertEqual(response.status_code,302)
        queued=Trip.objects.latest('id');self.assertEqual(queued.status,'queued')
        self.assertEqual(Trip.objects.filter(vehicle=self.vehicle,status__in=Trip.ACTIVE).count(),1)
        self.login_as(self.driver)
        response=self.client.get('/')
        self.assertContains(response,'Наступне місто')
        self.assertContains(response,'Наступні рейси')
        self.assertNotContains(response,self.other.full_name)
        self.login_as(self.other)
        self.assertEqual(self.client.get(self.url('trip_detail',queued.pk)).status_code,404)
        self.assertEqual(self.client.post(self.url('trip_start',queued.pk)).status_code,404)
    def test_queued_trip_starts_only_after_current_and_in_order(self):
        first=self.queue_trip(days=1);second=self.queue_trip(days=2)
        self.login_as(self.driver)
        self.accept(first);self.accept(second)
        self.client.post(self.url('trip_start',first.pk));first.refresh_from_db();self.assertEqual(first.status,'queued')
        self.finish()
        from fleet.queue_promotion import promote_ready
        promote_ready()
        self.client.post(self.url('trip_start',second.pk));second.refresh_from_db();self.assertEqual(second.status,'queued')
        self.accept(first)
        first.refresh_from_db();self.assertEqual(first.status,'planned')
        self.client.post(self.url('trip_start',first.pk));self.assertEqual(Trip.objects.filter(vehicle=self.vehicle,status__in=Trip.ACTIVE).count(),1)
    def test_queue_waits_for_driver_on_another_vehicle(self):
        self.finish();queued=self.queue_trip(driver=self.other)
        self.login_as(self.logistic)
        self.client.post(self.url('trip_start',queued.pk))
        queued.refresh_from_db();self.assertEqual(queued.status,'queued')
    def test_queue_cannot_start_during_repair_but_can_be_planned(self):
        self.finish();self.login_as(self.admin)
        self.client.post(self.url('vehicle_repair',self.vehicle.pk),{'reason':'ТО'})
        response=self.client.post('/trips/new/',{'vehicle':self.vehicle.pk,'driver':self.driver.pk,
            'origin':'Після ремонту','destination':'Порт','cargo':'X','planned_tons':'22',
            'planned_date':timezone.localdate().isoformat(),'scheduling':'queued'})
        self.assertEqual(response.status_code,302)
        queued=Trip.objects.latest('id')
        self.client.post(self.url('trip_start',queued.pk));queued.refresh_from_db();self.assertEqual(queued.status,'queued')
        self.client.post(self.url('vehicle_repair',self.vehicle.pk),{'operation':'finish','result':'ТО виконано'})
        self.client.post(self.url('trip_start',queued.pk));queued.refresh_from_db();self.assertEqual(queued.status,'planned')
    def test_queue_cannot_bypass_start_via_status_and_can_be_cancelled(self):
        queued=self.queue_trip();self.login_as(self.logistic)
        self.client.post(self.url('trip_status',queued.pk),{'status':'road'})
        queued.refresh_from_db();self.assertEqual(queued.status,'queued')
        self.client.post(self.url('trip_status',queued.pk),{'status':'cancelled'})
        queued.refresh_from_db();self.assertEqual(queued.status,'cancelled')
    def test_queued_driver_can_be_reassigned_but_not_disabled(self):
        queued=self.queue_trip(driver=self.spare);self.login_as(self.admin)
        self.client.post(self.url('account_action',self.spare.pk),{'action':'toggle'})
        self.spare.refresh_from_db();self.assertTrue(self.spare.is_active)
        self.client.post(self.url('assign_driver',queued.pk),{'driver':self.driver.pk})
        queued.refresh_from_db();self.assertEqual(queued.driver_id,self.driver.pk)
    def test_queue_detail_shows_start_block_and_does_not_leak_driver_list(self):
        queued=self.queue_trip();self.login_as(self.driver)
        self.accept(queued)
        response=self.client.get(self.url('trip_detail',queued.pk))
        self.assertContains(response,'Прийняти його можна, коли він стане поточним')
        self.assertNotContains(response,'Почати рейс із черги')
        self.assertContains(response,'Авто ще виконує поточний рейс')
        self.assertNotContains(response,self.other.full_name)
