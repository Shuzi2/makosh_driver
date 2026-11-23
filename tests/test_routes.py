from django.urls import reverse
import importlib
import tempfile
import uuid
from unittest.mock import patch
from decimal import Decimal
from django.apps import apps
from django.test import TestCase,override_settings
from django.utils import timezone
from django.db import connection
from django.core.files.uploadedfile import SimpleUploadedFile
from fleet.models import User,Vehicle,Trip,TripStop,Document,AuditEvent,AttentionSkip
from fleet.routes import sync_trip


@override_settings(SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False,CSRF_COOKIE_SECURE=False,STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class RouteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin=User.objects.create_user('+380507000001',role='admin',full_name='Логіст маршруту',must_change_password=False)
        cls.driver=User.objects.create_user('+380507000002',full_name='Водій маршруту',must_change_password=False)
        cls.other=User.objects.create_user('+380507000003',full_name='Інший водій',must_change_password=False)
        cls.vehicle=Vehicle.objects.create(plate='ROUTE01',trailer_plate='ROUTE02',kind='tent',default_driver=cls.driver)
    def setUp(self):
        self.media=tempfile.TemporaryDirectory();self.addCleanup(self.media.cleanup)
        override=override_settings(MEDIA_ROOT=self.media.name);override.enable();self.addCleanup(override.disable)
        self.client.force_login(self.admin)
    def payload(self,trip,**extra):
        trip.refresh_from_db()
        return {'action':'stop','stop_mode':'depart','stop_id':trip.current_stop.pk,
                'expected_revision':trip.updated_at.isoformat(),'request_id':str(uuid.uuid4()),**extra}

    def final_trip(self):
        trip=self.create([{'kind':'load','address':'Вінниця','cargo':'Добрива','planned_tons':24},{'kind':'unload','address':'Гайсин','cargo':'Добрива','planned_tons':24}])
        self.accept(trip);self.act(trip,'arrive');self.act(trip,'depart');self.act(trip,'arrive')
        return trip

    def test_completion_requires_documents_and_records_unloading_weight(self):
        trip=self.final_trip(); url=reverse('driver_action',args=[trip.pk])
        unload=trip.stops.last()
        page=self.client.get(reverse('driver_finish',args=[trip.pk]))
        self.assertContains(page,'Переїзд до завантаження, км')
        self.assertContains(page,'Кнопка стане доступною, коли ви завантажите всі відсутні ТТН')
        data={'empty_distance_km':'70','loaded_distance_km':'350','fuel_used_l':'135','actual_tons':'23.75','confirm_without_signed_ttn':'1'}
        self.assertEqual(self.client.post(url,self.payload(trip,**data)).json()['outcome'],'reminder')
        trip.refresh_from_db(); self.assertEqual(trip.status,'unloading')
        for point in trip.stops.all(): self.upload(trip,point,kind='ttn' if point.kind=='load' else 'signed_ttn')
        self.assertEqual(self.client.post(url,self.payload(trip,empty_distance_km='0',loaded_distance_km='420',fuel_used_l='135')).json()['outcome'],'reminder')
        self.assertEqual(self.client.post(url,self.payload(trip,**data)).json()['outcome'],'confirmed')
        trip.refresh_from_db(); unload.refresh_from_db()
        self.assertEqual(unload.actual_tons,Decimal('23.75'))
        self.assertEqual(trip.actual_tons,Decimal('23.75'))
        self.assertEqual((trip.empty_distance_km,trip.loaded_distance_km,trip.distance_km),(70,350,420))

    def test_staff_gets_driver_acceptance_and_unloading_updates_once(self):
        trip=self.create();self.accept(trip);self.accept(trip)
        self.client.force_login(self.admin)
        items=self.client.get(reverse('ttn_notifications')).json()['items']
        self.assertEqual(sum(i['kind']=='Водій прийняв рейс' for i in items),1)
        self.assertEqual(next(i for i in items if i['kind']=='Водій прийняв рейс')['driver'],self.driver.full_name)
        self.client.force_login(self.driver)
        self.act(trip,'arrive');self.act(trip,'depart');self.act(trip,'arrive');self.act(trip,'depart')
        self.act(trip,'arrive');trip.refresh_from_db();self.upload(trip,trip.current_stop)
        self.act(trip,'depart')
        self.client.force_login(self.admin)
        items=self.client.get(reverse('ttn_notifications')).json()['items']
        self.assertTrue(any('Прибув на розвантаження' in i['kind'] and i['driver']==self.driver.full_name for i in items))
        self.assertTrue(any('Завершив розвантаження' in i['kind'] and i['driver']==self.driver.full_name for i in items))

    def test_route_completion_promotes_next_trip_immediately(self):
        trip=self.final_trip()
        future=Trip.objects.create(vehicle=self.vehicle,driver=self.driver,created_by=self.admin,status='queued',origin='А',destination='Б',cargo='Вантаж',planned_tons=1,planned_date=timezone.localdate())
        for point in trip.stops.all(): self.upload(trip,point,kind='ttn' if point.kind=='load' else 'signed_ttn')
        payload=self.payload(trip,empty_distance_km='0',loaded_distance_km='100',fuel_used_l='20',actual_tons='24')
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.post(reverse('driver_action',args=[trip.pk]),payload).json()['outcome'],'confirmed')
        future.refresh_from_db();self.assertEqual(future.status,'planned');self.assertFalse(future.is_accepted)

    def test_completion_sums_actual_weights_for_multiple_unloading_points(self):
        trip=self.create();self.accept(trip);points=list(trip.stops.all())
        for point in points:
            self.upload(trip,point,kind='ttn' if point.kind=='load' else 'signed_ttn')
            self.act(trip,'arrive',point)
            if point.pk!=points[-1].pk: self.act(trip,'depart',point)
        data={'empty_distance_km':'0','loaded_distance_km':'420','fuel_used_l':'135',f'unload_weight_{points[2].pk}':'7.90',f'unload_weight_{points[3].pk}':'15.80'}
        self.assertEqual(self.client.post(reverse('driver_action',args=[trip.pk]),self.payload(trip,**data)).json()['outcome'],'confirmed')
        trip.refresh_from_db();self.assertEqual(trip.actual_tons,Decimal('23.70'))
        points[2].refresh_from_db();points[3].refresh_from_db()
        self.assertEqual(points[2].actual_tons,Decimal('7.90'));self.assertEqual(points[3].actual_tons,Decimal('15.80'))

    def test_final_totals_required_even_with_all_ttn_and_saved_only_on_confirmation(self):
        trip=self.final_trip()
        self.assertContains(self.client.get('/'),f'href="{reverse("driver_finish",args=[trip.pk])}"')
        for point in trip.stops.all(): self.upload(trip,point,kind='ttn' if point.kind=='load' else 'signed_ttn')
        url=reverse('driver_action',args=[trip.pk])
        for extra in [{},{'empty_distance_km':'0','loaded_distance_km':'-1','fuel_used_l':'5'},{'empty_distance_km':'0','loaded_distance_km':'420','fuel_used_l':'NaN'},{'empty_distance_km':'0','loaded_distance_km':'420','fuel_used_l':'20001'}]:
            self.assertEqual(self.client.post(url,self.payload(trip,**extra)).json()['outcome'],'reminder')
            trip.refresh_from_db();self.assertEqual(trip.status,'unloading');self.assertIsNone(trip.distance_km)
        payload=self.payload(trip,empty_distance_km='0',loaded_distance_km='420',fuel_used_l='135.25',actual_tons='24')
        result=self.client.post(url,payload).json();self.assertEqual(result['outcome'],'confirmed')
        self.assertEqual(self.client.post(url,payload).json(),result)
        trip.refresh_from_db();self.assertEqual(trip.status,'completed')
        self.assertEqual(trip.distance_km,420);self.assertEqual(trip.fuel_used_l,Decimal('135.25'))
        self.assertEqual(trip.events.filter(action='Водій вніс підсумки рейсу').count(),1)
        self.client.force_login(self.admin)
        response=self.client.get(reverse('trip_detail',args=[trip.pk]))
        self.assertContains(response,'135,25');self.assertContains(response,'420')

    def test_ttn_reminder_keeps_totals_and_does_not_finish_without_explicit_confirmation(self):
        trip=self.final_trip();url=reverse('driver_action',args=[trip.pk])
        payload=self.payload(trip,empty_distance_km='0',loaded_distance_km='320',fuel_used_l='0')
        result=self.client.post(url,payload).json();self.assertEqual(result['outcome'],'reminder')
        trip.refresh_from_db();self.assertIsNone(trip.distance_km);self.assertIsNone(trip.fuel_used_l)
        response=self.client.get(result['redirect']);self.assertContains(response,'value="320"')
        self.assertContains(response,'Перевірте ТТН усіх точок')
        payload=self.payload(trip,empty_distance_km='0',loaded_distance_km='320',fuel_used_l='0',actual_tons='24',confirm_without_signed_ttn='1')
        self.assertEqual(self.client.post(url,payload).json()['outcome'],'reminder')
        for point in trip.stops.all(): self.upload(trip,point,kind='ttn' if point.kind=='load' else 'signed_ttn')
        payload=self.payload(trip,empty_distance_km='0',loaded_distance_km='320',fuel_used_l='0',actual_tons='24')
        self.assertEqual(self.client.post(url,payload).json()['outcome'],'confirmed')
        trip.refresh_from_db();self.assertEqual(trip.fuel_used_l,Decimal('0'))
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('driver_finish',args=[trip.pk])).status_code,404)

    def test_finishing_trip_adds_distance_to_odometer_once_after_confirmation(self):
        self.vehicle.odometer_km=150000;self.vehicle.save(update_fields=['odometer_km'])
        trip=self.final_trip();url=reverse('driver_action',args=[trip.pk])
        payload=self.payload(trip,empty_distance_km='0',loaded_distance_km='540',fuel_used_l='0')
        self.assertEqual(self.client.post(url,payload).json()['outcome'],'reminder')
        self.vehicle.refresh_from_db();self.assertEqual(self.vehicle.odometer_km,150000)
        for point in trip.stops.all(): self.upload(trip,point,kind='ttn' if point.kind=='load' else 'signed_ttn')
        payload=self.payload(trip,empty_distance_km='0',loaded_distance_km='540',fuel_used_l='0',actual_tons='24')
        result=self.client.post(url,payload).json();self.assertEqual(result['outcome'],'confirmed')
        self.assertEqual(self.client.post(url,payload).json(),result)
        self.vehicle.refresh_from_db();self.assertEqual(self.vehicle.odometer_km,150540)
        self.assertEqual(trip.events.filter(action='Пробіг рейсу додано до одометра').count(),1)

    def test_finishing_trip_with_unknown_odometer_does_not_guess_a_reading(self):
        trip=self.final_trip();url=reverse('driver_action',args=[trip.pk])
        for point in trip.stops.all(): self.upload(trip,point,kind='ttn' if point.kind=='load' else 'signed_ttn')
        payload=self.payload(trip,empty_distance_km='0',loaded_distance_km='540',fuel_used_l='0',actual_tons='24')
        self.assertEqual(self.client.post(url,payload).json()['outcome'],'confirmed')
        self.vehicle.refresh_from_db();self.assertIsNone(self.vehicle.odometer_km)

    def point_data(self):
        return [{'kind':kind,'address':address,'cargo':'Добрива','planned_tons':str(tons)} for kind,address,tons in [('load','Вінниця',10),('load','Умань',14),('unload','Черкаси',8),('unload','Київ',16)]]
    def data(self,points=None,trip=None):
        points=points if points is not None else self.point_data()
        data={'vehicle':self.vehicle.pk,'driver':self.driver.pk,'planned_date':timezone.localdate().isoformat(),'scheduling':'current','notes':'Завдання','stops-TOTAL_FORMS':str(len(points)),'stops-INITIAL_FORMS':str(len(points) if trip else 0),'stops-MIN_NUM_FORMS':'0','stops-MAX_NUM_FORMS':'30'}
        for index,point in enumerate(points):
            for key,value in point.items(): data[f'stops-{index}-{key}']=value
            if not point.get('region') and not point.get('settlement_code'):
                from fleet.settlements import resolve_address
                if not resolve_address(point.get('address','')):
                    for key,value in {'region':'Тестова область','district':'Тестовий район','community':'Тестова громада'}.items(): data.setdefault(f'stops-{index}-{key}',value)
            data.setdefault(f'stops-{index}-ORDER',index+1)
        if trip: data['revision']=trip.updated_at.isoformat()
        return data
    def create(self,points=None):
        response=self.client.post('/trips/new/',self.data(points))
        self.assertEqual(response.status_code,302, getattr(response,'context',None) and str(response.context['route_formset'].errors))
        return Trip.objects.latest('pk')
    def test_dispatcher_can_attach_instruction_for_only_assigned_driver(self):
        pdf=SimpleUploadedFile('port.pdf',b'%PDF-1.4\nexample',content_type='application/pdf')
        response=self.client.post('/trips/new/',{**self.data(),'instructions':[pdf]})
        self.assertEqual(response.status_code,302)
        trip=Trip.objects.latest('pk');doc=trip.documents.get(kind='instruction')
        self.client.force_login(self.driver)
        self.assertContains(self.client.get(reverse('trip_detail',args=[trip.pk])),'port.pdf')
        response=self.client.get(reverse('trip_instruction',args=[doc.pk]))
        self.assertEqual(response.status_code,200);self.assertTrue(b''.join(response.streaming_content))
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('trip_instruction',args=[doc.pk])).status_code,404)

    def test_invalid_instruction_does_not_create_trip(self):
        bad=SimpleUploadedFile('fake.pdf',b'not a pdf',content_type='application/pdf')
        response=self.client.post('/trips/new/',{**self.data(),'instructions':[bad]})
        self.assertEqual(response.status_code,200)
        self.assertFalse(Trip.objects.exists())
    def test_instruction_photo_is_viewable_without_document_upload_permission(self):
        from io import BytesIO
        from PIL import Image
        from fleet.permissions_models import DriverPermissionSettings
        image=BytesIO();Image.new('RGB',(12,12),'white').save(image,format='PNG')
        photo=SimpleUploadedFile('drive.png',image.getvalue(),content_type='image/png')
        self.assertEqual(self.client.post('/trips/new/',{**self.data(),'instructions':[photo]}).status_code,302)
        doc=Document.objects.get(kind='instruction')
        settings,_=DriverPermissionSettings.objects.get_or_create(pk=1)
        settings.upload_documents=False;settings.save()
        self.client.force_login(self.driver)
        response=self.client.get(reverse('trip_instruction',args=[doc.pk]))
        self.assertEqual(response.status_code,200);self.assertEqual(response['Content-Type'],'image/png');self.assertTrue(b''.join(response.streaming_content))
    def test_google_maps_link_on_each_stop_and_coordinate_fallback(self):
        loading='https://maps.app.goo.gl/Example123'
        unloading='https://www.google.com/maps/place/Test/@49.23,28.46,16z'
        points=[{'kind':'load','address':'Вінниця','cargo':'Добрива','planned_tons':24,'google_maps_url':loading},
                {'kind':'unload','address':'Гайсин','cargo':'Добрива','planned_tons':24,'google_maps_url':unloading}]
        trip=self.create(points);load,unload=list(trip.stops.all())
        self.assertEqual(load.google_maps_url,loading);self.assertEqual(unload.google_maps_url,unloading)
        self.assertEqual(load.directions_url,loading);self.assertEqual(unload.directions_url,unloading)
        self.client.force_login(self.driver)
        page=self.client.get(reverse('trip_detail',args=[trip.pk]))
        self.assertContains(page,loading);self.assertContains(page,'Відкрити Google Maps')
        load.google_maps_url='';load.lat=Decimal('49.233083');load.lon=Decimal('28.468217')
        self.assertIn('49.233083%2C28.468217',load.directions_url)
        load.lat=None;load.lon=None;self.assertIn('maps/search/',load.directions_url)

    def test_google_maps_link_rejects_non_google_domains(self):
        from fleet.route_forms import StopForm
        for url in ('https://maps.app.goo.gl.evil.example/test','javascript:alert(1)','https://google.com.evil.example/maps','https://www.google.com.evil.example/maps','https://www.google.com/account'):
            form=StopForm(data={'kind':'load','address':'Вінниця','cargo':'Добрива','planned_tons':'1','google_maps_url':url})
            self.assertFalse(form.is_valid());self.assertIn('google_maps_url',form.errors)

    def test_google_maps_link_can_be_edited_cleared_and_copied(self):
        from fleet.route_forms import initial_stops
        trip=self.create();data=self.edit_data(trip)
        url='https://maps.app.goo.gl/Loading123'
        data['stops-0-google_maps_url']=url
        response=self.client.post(reverse('trip_edit',args=[trip.pk]),data)
        self.assertEqual(response.status_code,302)
        load=trip.stops.order_by('position').first();self.assertEqual(load.google_maps_url,url)
        self.assertEqual(initial_stops(trip)[0]['google_maps_url'],url)
        self.assertTrue(trip.events.filter(action='Оновлено точку маршруту').exists())
        trip.refresh_from_db();data=self.edit_data(trip);data['stops-0-google_maps_url']=''
        self.assertEqual(self.client.post(reverse('trip_edit',args=[trip.pk]),data).status_code,302)
        load.refresh_from_db();self.assertEqual(load.google_maps_url,'')
        self.assertIn('maps/search/',load.directions_url)

    def test_google_maps_link_display_is_scoped_to_own_driver(self):
        trip=self.create();stop=trip.stops.first();stop.google_maps_url='https://maps.app.goo.gl/PrivatePoint';stop.save(update_fields=['google_maps_url'])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('trip_detail',args=[trip.pk])).status_code,404)

    def test_changing_address_requires_google_link_confirmation(self):
        from fleet.route_forms import StopForm
        url='https://maps.app.goo.gl/Loading123'
        data={'kind':'load','address':'Інше місце','region':'Область','district':'Район','community':'Громада','cargo':'Добрива','planned_tons':'1','google_maps_url':url}
        original={'address':'Старе місце','lat':None,'lon':None,'google_maps_url':url}
        form=StopForm(data=data);form.original=original
        self.assertFalse(form.is_valid());self.assertIn('google_maps_url',form.errors)
        form=StopForm(data={**data,'google_maps_confirmed':'on'});form.original=original
        self.assertTrue(form.is_valid(),form.errors)
    def edit_data(self,trip):
        from fleet.route_forms import StopForm
        points=[{**{f:getattr(s,f) if getattr(s,f) is not None else '' for f in StopForm.Meta.fields},'id':s.pk} for s in trip.stops.all()]
        data=self.data(points,trip);data.pop('scheduling');return data
    def act(self,trip,mode,stop=None,**extra):
        trip.refresh_from_db();stop=stop or trip.current_stop
        if mode=='depart' and stop and trip.stops.filter(state__in=['pending','arrived']).count()==1:
            extra={'empty_distance_km':'0','loaded_distance_km':'420','fuel_used_l':'135',**extra}
            unloads=list(trip.stops.filter(kind='unload').exclude(state='cancelled'))
            if len(unloads)>1:
                for point in unloads: extra.setdefault(f'unload_weight_{point.pk}',str(point.actual_tons or point.planned_tons))
            elif unloads: extra.setdefault('actual_tons',str(unloads[0].actual_tons or unloads[0].planned_tons))
        return self.client.post(f'/driver/trips/{trip.pk}/action/',{'action':'stop','stop_id':stop.pk,'stop_mode':mode,'expected_revision':trip.updated_at.isoformat(),'request_id':str(uuid.uuid4()),**extra})
    def accept(self,trip):
        self.client.force_login(self.driver);self.client.post(f'/trips/{trip.pk}/accept/',{'acceptance_location':'м. Вінниця, база','acceptance_odometer_km':'150000'});trip.refresh_from_db()
    def upload(self,trip,stop,number='',kind='signed_ttn'):
        return self.client.post(f'/trips/{trip.pk}/documents/',{'stop':stop.pk,'kind':kind,'ttn_number':number,'file':SimpleUploadedFile('signed.pdf',b'%PDF-1.4\n test',content_type='application/pdf')})

    def test_driver_can_archive_own_photo_with_correct_ttn_status(self):
        from fleet.routes import refresh_signed
        from fleet.models import TtnNotification
        trip=self.create([{'kind':'load','address':'Вінниця','cargo':'Добрива','planned_tons':24},{'kind':'unload','address':'Гайсин','cargo':'Добрива','planned_tons':24}]);self.accept(trip)
        stop=trip.stops.last()
        doc=Document.objects.create(trip=trip,stop=stop,kind='signed_ttn',file='trips/test.jpg',original_name='test.jpg',size=1,content_type='image/jpeg',uploaded_by=self.driver)
        notice=TtnNotification.objects.create(recipient=self.admin,document=doc)
        refresh_signed(stop);stop.refresh_from_db();self.assertTrue(stop.signed_complete)
        page=self.client.get('/driver/documents/',{'trip':trip.pk,'stop':stop.pk})
        self.assertContains(page, f'/documents/{doc.pk}/remove/')
        self.assertContains(page, 'data-confirm-title=')
        response=self.client.post(f'/documents/{doc.pk}/remove/')
        self.assertEqual(response.status_code,302)
        stop.refresh_from_db();self.assertFalse(stop.signed_complete)
        trip.refresh_from_db();self.assertFalse(trip.has_signed_ttn)
        self.assertFalse(Document.objects.filter(pk=doc.pk).exists())
        self.assertTrue(Document.all_objects.filter(pk=doc.pk,deleted_by=self.driver).exists())
        self.assertEqual(self.client.get(f'/documents/{doc.pk}/view/').status_code,404)
        self.assertTrue(AuditEvent.objects.filter(trip=trip,actor=self.driver,action='Водій видалив фото документа').exists())
        self.assertEqual(self.client.post(f'/documents/{doc.pk}/remove/').status_code,404)
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(f'/trips/{trip.pk}/'), f'/documents/{doc.pk}/view/')
        self.assertNotIn(notice.pk,[item['id'] for item in self.client.get('/notifications/ttn/').json()['items']])

    def test_driver_cannot_remove_other_people_documents(self):
        trip=self.create();self.accept(trip);stop=trip.stops.first()
        for owner,content_type in [(self.admin,'image/jpeg'),(self.admin,'application/pdf')]:
            doc=Document.objects.create(trip=trip,stop=stop,kind='ttn',file='trips/x',original_name='x',size=1,content_type=content_type,uploaded_by=owner)
            self.assertEqual(self.client.post(f'/documents/{doc.pk}/remove/').status_code,404)
            self.assertTrue(Document.objects.filter(pk=doc.pk).exists())

    def test_driver_can_remove_own_pdf_with_confirmation(self):
        trip=self.create();self.accept(trip);stop=trip.stops.first()
        doc=Document.objects.create(trip=trip,stop=stop,kind='ttn',file='trips/x.pdf',original_name='x.pdf',size=1,content_type='application/pdf',uploaded_by=self.driver)
        page=self.client.get('/driver/documents/',{'trip':trip.pk,'stop':stop.pk})
        self.assertContains(page,f'/documents/{doc.pk}/remove/')
        self.assertContains(page,'data-confirm-title=')
        self.assertEqual(self.client.post(f'/documents/{doc.pk}/remove/').status_code,302)
        self.assertFalse(Document.objects.filter(pk=doc.pk).exists())
        self.assertTrue(Document.all_objects.filter(pk=doc.pk,deleted_by=self.driver).exists())
    def test_final_check_includes_earlier_loading_and_each_expected_number(self):
        trip=self.create();first=trip.stops.first();first.ttn_number='L-1; L-2';first.save()
        self.accept(trip)
        points=list(trip.stops.all())
        self.upload(trip,points[0],'L-1',kind='ttn')
        self.upload(trip,points[1],kind='ttn')
        self.upload(trip,points[2]);self.upload(trip,points[3])
        for point in points:
            self.act(trip,'arrive',point)
            if point.pk!=points[-1].pk: self.act(trip,'depart',point)
        result=self.act(trip,'depart',points[-1]).json()
        self.assertEqual(result['outcome'],'reminder')
        response=self.client.get(result['redirect'])
        self.assertContains(response,'Перевірте ТТН усіх точок')
        self.assertEqual([s.pk for s in response.context['missing_ttn_stops']],[first.pk])
        self.assertEqual(response.context['missing_ttn_stops'][0].missing_ttn_numbers,['L-2'])
        trip.refresh_from_db();self.assertEqual(trip.status,'unloading')
        self.upload(trip,first,'L-2',kind='ttn')
        self.assertEqual(self.act(trip,'depart',points[-1]).json()['outcome'],'confirmed')
        trip.refresh_from_db();self.assertEqual(trip.status,'completed')
    def dispatcher_stage(self,trip,stage,**extra):
        trip.refresh_from_db()
        self.client.force_login(self.admin)
        return self.client.post(f'/trips/{trip.pk}/stage/',{'stage':stage,'revision':trip.updated_at.isoformat(),**extra})

    def test_dispatcher_can_jump_forward_back_and_record_acceptance_honestly(self):
        trip=self.create();points=list(trip.stops.all())
        self.dispatcher_stage(trip,'unloading',stop=points[2].pk)
        trip.refresh_from_db();self.assertEqual(trip.status,'unloading')
        self.assertTrue(trip.is_accepted);self.assertEqual(trip.accepted_by,self.admin)
        self.assertEqual(list(trip.stops.values_list('state',flat=True)),['completed','completed','arrived','pending'])
        self.assertEqual(trip.current_stop.pk,points[2].pk)
        self.upload(trip,points[2],'',kind='signed_ttn')
        points[2].refresh_from_db();points[2].actual_tons=Decimal('7.9');points[2].save()
        self.dispatcher_stage(trip,'loading',stop=points[0].pk)
        trip.refresh_from_db();self.assertEqual(trip.status,'loading')
        self.assertEqual(list(trip.stops.values_list('state',flat=True)),['arrived','pending','pending','pending'])
        self.assertEqual(trip.documents.count(),1)
        self.assertEqual(trip.stops.get(pk=points[2].pk).actual_tons,Decimal('7.9'))
        self.dispatcher_stage(trip,'planned');trip.refresh_from_db();self.assertFalse(trip.is_accepted)
        self.assertEqual(set(trip.stops.values_list('state',flat=True)),{'pending'})
        self.dispatcher_stage(trip,'accepted');trip.refresh_from_db();self.assertTrue(trip.is_accepted)
        self.assertEqual(trip.status,'planned');self.assertEqual(trip.accepted_by,self.admin)
        self.assertTrue(trip.events.filter(action='Логіст змінив етап рейсу',actor=self.admin).exists())
        self.client.force_login(self.driver);self.assertContains(self.client.get('/'),'Логіст змінив етап рейсу')

    def test_dispatcher_complete_reopen_and_advance_selected_point(self):
        trip=self.create();points=list(trip.stops.all())
        self.dispatcher_stage(trip,'completed');trip.refresh_from_db()
        self.assertEqual(trip.status,'completed');self.assertIsNotNone(trip.completed_at)
        self.assertEqual(set(trip.stops.values_list('state',flat=True)),{'completed'})
        self.assertContains(self.client.get(f'/trips/{trip.pk}/'),'Змінити будь-який етап')
        self.dispatcher_stage(trip,'road',stop=points[1].pk);trip.refresh_from_db()
        self.assertEqual(trip.status,'road');self.assertIsNone(trip.completed_at)
        self.assertEqual(trip.current_stop.pk,points[1].pk)
        self.dispatcher_stage(trip,'point_done',stop=points[1].pk);trip.refresh_from_db()
        self.assertEqual(trip.current_stop.pk,points[2].pk)

    def test_dispatcher_stage_validates_revision_point_permissions_and_conflicts(self):
        trip=self.create();points=list(trip.stops.all());revision=trip.updated_at.isoformat()
        for stage,stop in [('loading',points[2].pk),('unloading',999999),('road','')]:
            self.dispatcher_stage(trip,stage,stop=stop);trip.refresh_from_db();self.assertEqual(trip.status,'planned')
        self.dispatcher_stage(trip,'accepted')
        self.client.post(f'/trips/{trip.pk}/stage/',{'stage':'completed','revision':revision})
        trip.refresh_from_db();self.assertEqual(trip.status,'planned')
        self.client.force_login(self.driver)
        self.assertEqual(self.client.post(f'/trips/{trip.pk}/stage/',{'stage':'completed','revision':trip.updated_at.isoformat()}).status_code,403)
        self.dispatcher_stage(trip,'completed')
        self.client.force_login(self.admin);other=self.create()
        self.dispatcher_stage(trip,'accepted');trip.refresh_from_db();self.assertEqual(trip.status,'completed')
        other.refresh_from_db();self.assertEqual(other.status,'planned')

    def test_quick_actual_weights_update_stops_totals_and_audit_without_advancing_route(self):
        trip=self.create();self.accept(trip);self.client.force_login(self.admin)
        revision=trip.updated_at;stage=trip.status_changed_at
        points=list(trip.stops.filter(kind='unload'))
        url=f'/trips/{trip.pk}/edit/actual_tons/'
        data={'revision':revision.isoformat(),f'weight_{points[0].pk}':'7.80',f'weight_{points[1].pk}':'15.90'}
        self.assertEqual(self.client.post(url,data).status_code,302)
        trip.refresh_from_db();self.assertEqual(trip.actual_tons,Decimal('23.70'))
        self.assertEqual(trip.status,'planned');self.assertEqual(trip.status_changed_at,stage)
        self.assertEqual(trip.stops.get(pk=points[0].pk).weight_revision,1)
        self.assertEqual(trip.events.filter(action='Фактична вага точки').count(),2)
        # Old revisions cannot overwrite a driver's newer update.
        self.assertEqual(self.client.post(url,data).status_code,200)
        trip.refresh_from_db();self.assertEqual(trip.actual_tons,Decimal('23.70'))
        data['revision']=trip.updated_at.isoformat();data[f'weight_{points[0].pk}']=''
        self.assertEqual(self.client.post(url,data).status_code,302)
        trip.refresh_from_db();self.assertIsNone(trip.actual_tons)
        data['revision']=trip.updated_at.isoformat();data[f'weight_{points[0].pk}']='61'
        self.assertEqual(self.client.post(url,data).status_code,200)
        self.client.force_login(self.driver);self.assertEqual(self.client.post(url,data).status_code,403)

    def test_simple_weight_editor_and_shared_stages_live_version_and_bottom_actions(self):
        trip=self.create([{'kind':'load','address':'Вінниця','cargo':'Добрива','planned_tons':24},{'kind':'unload','address':'Гайсин','cargo':'Добрива','planned_tons':24}])
        url=f'/trips/{trip.pk}/'
        response=self.client.get(url)
        html=response.content.decode()
        self.assertGreater(html.index('id="trip-actions"'),html.index('id="documents"'))
        self.assertContains(response,'data-quick-edit="actual_tons"')
        self.assertNotContains(response,'name="stop_mode"')
        version=self.client.get(url+'?live=1').json()['version']
        self.assertEqual(self.client.post(url+'edit/actual_tons/',{'actual_tons':'23.80','revision':trip.updated_at.isoformat()}).status_code,302)
        trip.refresh_from_db();self.assertEqual(trip.actual_tons,Decimal('23.80'))
        self.assertEqual(trip.stops.get(kind='unload').actual_tons,Decimal('23.80'))
        self.assertNotEqual(self.client.get(url+'?live=1').json()['version'],version)
        self.accept(trip)
        for mode in ['arrive','depart','arrive']:
            driver=self.client.get('/').context['driver_steps']
            self.client.force_login(self.admin)
            dispatcher=self.client.get(url)
            self.assertEqual(driver,dispatcher.context['driver_steps'])
            self.assertContains(dispatcher,driver[next(i for i,s in enumerate(driver) if s['current'])]['label'])
            self.client.force_login(self.driver);self.act(trip,mode)
        self.client.force_login(self.admin)
        before=self.client.get(url+'?live=1').json()['version']
        self.driver.rest_started_at=timezone.now();self.driver.rest_changed_at=timezone.now();self.driver.save()
        self.assertNotEqual(self.client.get(url+'?live=1').json()['version'],before)

    def test_driver_documents_default_to_missing_point_and_keep_upload_destination(self):
        trip=self.create();self.accept(trip)
        point=trip.stops.first()
        point.ttn_number='L-1; L-2';point.save()
        self.upload(trip,point,'L-1',kind='ttn')
        home=self.client.get('/')
        self.assertContains(home,'Документи рейсу')
        self.assertContains(home,'Частково')
        self.assertContains(home,'data-driver-connection')
        response=self.client.get(f'/driver/documents/?trip={trip.pk}&stop={point.pk}')
        self.assertEqual(response.context['document_stop'].pk,point.pk)
        self.assertEqual(response.context['document_number'],'L-2')
        self.assertContains(response,'Яку ТТН фотографуєте?')
        self.assertContains(response,'Частково надіслано')
        uploaded=self.upload(trip,point,'L-2',kind='ttn')
        self.assertRedirects(uploaded,f'/driver/documents/?trip={trip.pk}&stop={point.pk}',fetch_redirect_response=False)
        response=self.client.get(uploaded.url)
        self.assertTrue(response.context['document_points'][0].ttn_complete)
        self.assertEqual(len(response.context['selected_documents']),2)
        self.assertContains(response,'✓ ТТН надіслано')
        trip.stops.update(state='completed');trip.status='completed';trip.save()
        response=self.client.get(f'/driver/documents/?trip={trip.pk}')
        self.assertEqual(response.context['document_stop'].position,2)

    def test_driver_documents_reject_foreign_and_cancelled_stops(self):
        trip=self.create();self.accept(trip)
        point=trip.stops.first();point.state='cancelled';point.save()
        for value in [str(point.pk),'999999','invalid']:
            self.assertEqual(self.client.get(f'/driver/documents/?trip={trip.pk}&stop={value}').status_code,404)

    def test_point_buttons_choose_correct_document_and_cancelled_points_are_excluded(self):
        trip=self.create();self.accept(trip)
        response=self.client.get('/')
        for point in trip.stops.all():
            kind='ttn' if point.kind=='load' else 'signed_ttn'
            self.assertContains(response,f'stop={point.pk}&amp;kind={kind}')
            screen=self.client.get(f'/driver/documents/?trip={trip.pk}&stop={point.pk}&kind={kind}')
            self.assertEqual(screen.context['upload_form'].initial['kind'],kind)
            self.assertEqual(screen.context['upload_form'].initial['stop'].pk,point.pk)
        cancelled=TripStop.objects.create(trip=trip,position=5,kind='unload',address='Скасована точка',cargo='Добрива',planned_tons=1,state='cancelled')
        from fleet.routes import route_context
        self.assertNotIn(cancelled.pk,[s.pk for s in route_context(trip)['missing_ttn_stops']])
    def test_create_four_points_and_all_screens(self):
        trip=self.create();self.assertTrue(trip.route_enabled);self.assertEqual(trip.stops.count(),4)
        self.assertEqual(trip.planned_tons,Decimal('24'));self.assertIsNone(trip.actual_tons)
        self.assertEqual((trip.origin,trip.destination),('Вінниця','Київ'))
        self.assertContains(self.client.get(f'/trips/{trip.pk}/'),'Черкаси')
        self.assertContains(self.client.get('/'),'Точка 1 із 4')
        self.client.force_login(self.driver)
        response=self.client.get('/');self.assertContains(response,'Точки маршруту');self.assertContains(response,'Забрати 10,00 т')
        self.assertEqual(self.client.get(f'/trips/{trip.pk}/edit/').status_code,403)
        self.client.force_login(self.other);self.assertEqual(self.client.get(f'/trips/{trip.pk}/').status_code,404)
    def test_driver_sees_all_contacts_after_acceptance_and_only_status_controls(self):
        points=self.point_data()
        for index,point in enumerate(points):
            point.update(contact_name=f'Контакт точки {index+1}',contact_phone=f'+38050900000{index+1}')
        trip=self.create(points)
        self.client.force_login(self.driver)
        response=self.client.get('/')
        for point in points:
            self.assertNotContains(response,point['contact_phone'])
        self.accept(trip)
        self.act(trip,'arrive')
        response=self.client.get('/')
        for point in points:
            self.assertContains(response,f'href="tel:{point["contact_phone"]}"')
        self.assertContains(response,'Точки маршруту')
        self.assertContains(response,'Виконано 0 з 4')
        self.assertNotContains(response,'name="actual_tons"')
        self.assertNotContains(response,'<details class="driver-whole-route"')
        self.act(trip,'depart')
        response=self.client.get('/')
        self.assertEqual(response.context['current_stop'].address,'Умань')
        self.assertEqual(response.context['next_stop'].address,'Черкаси')
        self.assertContains(response,'Виконано 1 з 4')

    def test_driver_two_point_trip_has_no_weight_input(self):
        trip=self.create([{'kind':'load','address':'Вінниця','cargo':'Добрива','planned_tons':'24'},{'kind':'unload','address':'Київ','cargo':'Добрива','planned_tons':'24'}])
        self.accept(trip)
        self.act(trip,'arrive')
        self.assertNotContains(self.client.get('/'),'name="actual_tons"')

    def test_driver_can_search_navigation_for_address_without_coordinates(self):
        trip=self.create([{'kind':'load','address':'Вінниця','cargo':'Добрива','planned_tons':'24'},{'kind':'unload','address':'Київ','cargo':'Добрива','planned_tons':'24'}])
        self.accept(trip)
        response=self.client.get('/')
        self.assertContains(response,'Пошук адреси на карті')
        self.assertContains(response,'www.google.com/maps/search/')

    def test_simple_driver_screen_has_full_areas_once_and_action_before_addresses(self):
        points=[{'kind':'load','address':'Вінниця','cargo':'Добрива','planned_tons':'24','contact_name':'Завантаження контакт','contact_phone':'+380501234561'},{'kind':'unload','address':'Гайсин','cargo':'Добрива','planned_tons':'24','contact_name':'Розвантаження контакт','contact_phone':'+380501234562'}]
        trip=self.create(points);self.accept(trip)
        response=self.client.get('/');html=response.content.decode()
        self.assertContains(response,'Вінницька область')
        self.assertContains(response,'Гайсинський район')
        self.assertContains(response,'Гайсинська громада')
        self.assertNotContains(response,'Точка 1 із 2')
        self.assertNotContains(response,'Точки маршруту')
        self.assertContains(response,'href="tel:+380501234561"',count=1)
        self.assertContains(response,'href="tel:+380501234562"',count=1)
        self.assertLess(html.index('Приїхав на завантаження'),html.index('class="driver-address-list"'))
        self.assertEqual(trip.stops.get(kind='unload').community,'Гайсинська громада')

    def test_fleet_card_shows_route_and_current_point_in_live_updates(self):
        trip=self.create()
        response=self.client.get('/')
        for point in self.point_data(): self.assertContains(response,point['address'])
        self.assertContains(response,'Перша точка')
        self.assertNotContains(response,'Документів:')
        self.accept(trip);self.act(trip,'arrive');self.act(trip,'depart')
        self.client.force_login(self.admin)
        card=next(card for card in self.client.get('/').context['cards'] if card['trip'])
        self.assertEqual(card['route']['current'].address,'Умань')
        self.assertEqual(card['route']['label'],'Їде на завантаження')
        self.assertEqual(card['route']['points'][0]['label'],'Виконано')
        live=self.client.get('/?live=1').json()['cards']
        self.assertIn('Точка 2 із 4',live)
        self.assertIn('Черкаси',live)
        self.assertIn('Їде на завантаження',live)
        self.client.force_login(self.driver);self.act(trip,'arrive')
        self.client.force_login(self.admin)
        self.assertIn('На завантаженні',self.client.get('/?live=1').json()['cards'])

    def test_trip_list_explains_all_stops_and_finds_intermediate_address(self):
        trip=self.create()
        response=self.client.get('/trips/')
        self.assertContains(response,'Точок: 4')
        self.assertContains(response,'Завантажень: 2')
        self.assertContains(response,'Розвантажень: 2')
        for point in self.point_data(): self.assertContains(response,point['address'])
        self.assertContains(self.client.get('/trips/?q=Умань'),'Точок: 4')
        self.accept(trip);self.act(trip,'arrive');self.act(trip,'depart')
        self.client.force_login(self.admin)
        response=self.client.get('/trips/')
        current=response.context['trips'][0].list_route['current']
        self.assertEqual(current['current'].address,'Умань')
        self.assertContains(response,'Точка 2 із 4')
        trip.stops.update(state='completed');trip.status='completed';trip.save()
        response=self.client.get('/trips/')
        self.assertContains(response,'Точок: 4')
        self.assertFalse(response.context['trips'][0].list_route['current'])

    def test_trip_list_short_names_keep_full_addresses_searchable(self):
        trip = self.create()
        first = trip.stops.order_by('position').first()
        first.address = 'м. Вінниця, вулиця Єдності, 10'
        first.settlement_name = 'м. Вінниця'
        first.save(update_fields=['address', 'settlement_name'])
        response = self.client.get('/trips/?q=Єдності')
        row = response.context['trips'][0].list_route
        self.assertEqual(row['origin_short'], 'м. Вінниця')
        self.assertContains(response, 'м. Вінниця, вулиця Єдності, 10')
        self.assertContains(response, 'Деталі')
        self.assertContains(response, '<option value="missing"')
        self.assertContains(response, '<option value="all"')

    def test_trip_list_shows_every_address_and_only_ttn_files_change_status(self):
        trip = self.create()
        first = trip.stops.order_by('position').first()
        first.address = 'м. Вінниця, вулиця Єдності, 10'
        first.save(update_fields=['address'])
        row = next(item for item in self.client.get('/trips/').context['trips'] if item.pk == trip.pk)
        self.assertFalse(row.list_has_ttn)
        response = self.client.get('/trips/')
        self.assertContains(response, 'м. Вінниця, вулиця Єдності, 10')
        self.assertContains(response, 'Не завантажено')
        other = Document.objects.create(trip=trip, kind='weight', file='weight.pdf', original_name='weight.pdf', size=1, content_type='application/pdf', uploaded_by=self.admin)
        row = next(item for item in self.client.get('/trips/').context['trips'] if item.pk == trip.pk)
        self.assertFalse(row.list_has_ttn)
        other.kind = 'ttn'
        other.stop = first
        other.save(update_fields=['kind', 'stop'])
        row = next(item for item in self.client.get('/trips/').context['trips'] if item.pk == trip.pk)
        self.assertTrue(row.list_has_ttn)
        self.assertContains(self.client.get('/trips/'), 'Частково')
        other.kind = 'signed_ttn'
        other.save(update_fields=['kind'])
        row = next(item for item in self.client.get('/trips/').context['trips'] if item.pk == trip.pk)
        self.assertTrue(row.list_has_ttn)

    def test_planned_balance_order_and_capacity_are_validated_atomically(self):
        for modify in ['negative','remaining','first','capacity']:
            points=self.point_data()
            if modify=='negative': points[2]['planned_tons']='25';points[3]['planned_tons']='1'
            if modify=='remaining': points[3]['planned_tons']='15'
            if modify=='first': points[0]['kind']='unload'
            if modify=='capacity': points[0]['planned_tons']='55'
            response=self.client.post('/trips/new/',self.data(points))
            self.assertEqual(response.status_code,200);self.assertTrue(response.context['route_formset'].non_form_errors())
            self.assertFalse(Trip.objects.exists());self.assertFalse(TripStop.objects.exists())
    def test_optional_map_is_hidden_from_driver_until_dispatcher_sets_both_coordinates(self):
        trip=self.create();self.accept(trip)
        response=self.client.get('/')
        self.assertNotContains(response,'data-location-preview');self.assertNotContains(response,'class="driver-map"')
        self.assertNotContains(response,'Точку на карті ще не вказано')
        self.assertContains(response,'Вінниця')
        point=trip.stops.first();point.lat=Decimal('49.233083');point.lon=Decimal('28.468217');point.save()
        response=self.client.get('/')
        self.assertContains(response,'data-location-preview',count=1);self.assertContains(response,'destination=49.233083%2C28.468217')
        point.lat=None;point.lon=None;point.save()
        self.assertNotContains(self.client.get('/'),'data-location-preview')
    def test_route_total_can_exceed_truck_capacity_when_loads_are_delivered_between_pickups(self):
        points=[{'kind':kind,'address':f'Точка {i}','cargo':'Добрива','planned_tons':'40'} for i,kind in enumerate(['load','unload','load','unload'])]
        trip=self.create(points);self.assertEqual(trip.planned_tons,80)
    def test_driver_traverses_every_point_and_total_does_not_double_count(self):
        trip=self.create();self.accept(trip)
        points=list(trip.stops.all())
        for i,stop in enumerate(points):
            response=self.act(trip,'arrive',stop);self.assertEqual(response.json()['outcome'],'confirmed')
            trip.refresh_from_db();self.assertEqual(trip.status,'loading' if stop.kind=='load' else 'unloading')
            if stop.kind=='unload': self.upload(trip,stop)
            else: self.client.post(f'/trips/{trip.pk}/documents/',{'stop':stop.pk,'kind':'ttn','file':SimpleUploadedFile('load.pdf',b'%PDF-1.4\n test',content_type='application/pdf')})
            response=self.act(trip,'depart',stop,actual_tons=str(stop.planned_tons))
            self.assertEqual(response.json()['outcome'],'confirmed')
            trip.refresh_from_db();self.assertEqual(trip.status,'road' if i<3 else 'completed')
        self.assertEqual(trip.actual_tons,Decimal('24'));self.assertEqual(trip.route_balance['tons'],0)
        self.assertTrue(trip.has_signed_ttn);self.assertEqual(trip.events.filter(action='Виконано точку маршруту').count(),4)
        self.client.force_login(self.admin);response=self.client.get('/report/')
        self.assertEqual(response.context['totals']['tons'],24)
        response=self.client.get('/report/?q=Черкаси');self.assertEqual(response.context['count'],1)
        csv=self.client.get('/report/?format=csv&detail=stops').content.decode('utf-8-sig');self.assertIn('Черкаси',csv)
    def test_reminder_per_unload_and_receipt_retry_are_safe(self):
        trip=self.create();self.accept(trip)
        for stop in list(trip.stops.all())[:2]: self.act(trip,'arrive',stop);self.act(trip,'depart',stop)
        third=trip.stops.get(position=3);self.act(trip,'arrive',third)
        response=self.act(trip,'depart',third,actual_tons='7.90')
        self.assertEqual(response.json()['outcome'],'reminder');trip.refresh_from_db();self.assertEqual(trip.status,'unloading')
        self.assertContains(self.client.get(response.json()['redirect']),'7.90')
        trip.refresh_from_db();payload={'action':'stop','stop_id':third.pk,'stop_mode':'depart','actual_tons':'7.90','confirm_without_signed_ttn':'1','empty_distance_km':'0','loaded_distance_km':'420','fuel_used_l':'135','request_id':str(uuid.uuid4()),'expected_revision':trip.updated_at.isoformat()}
        first=self.client.post(f'/driver/trips/{trip.pk}/action/',payload).json()
        second=self.client.post(f'/driver/trips/{trip.pk}/action/',payload).json()
        self.assertEqual(first,second);self.assertEqual(first['outcome'],'confirmed')
        self.assertEqual(trip.events.filter(action='Виконано точку маршруту').count(),3)
        trip.refresh_from_db();self.assertEqual(trip.current_stop.position,4);self.assertFalse(trip.has_signed_ttn)
    def test_driver_cannot_skip_points_forge_global_status_or_use_stale_revision(self):
        trip=self.create();self.accept(trip)
        response=self.act(trip,'arrive',trip.stops.get(position=3));self.assertEqual(response.json()['outcome'],'rejected')
        response=self.act(trip,'depart');self.assertEqual(response.json()['outcome'],'rejected')
        response=self.act(trip,'arrive',expected_revision=timezone.now().isoformat());self.assertEqual(response.json()['outcome'],'rejected')
        self.client.post(f'/trips/{trip.pk}/status/',{'status':'loading'});trip.refresh_from_db();self.assertEqual(trip.status,'planned')
        self.client.force_login(self.other);self.assertEqual(self.act(trip,'arrive').status_code,404)
    def test_documents_are_scoped_to_stop_and_number_and_all_numbers_are_required(self):
        trip=self.create();stop=trip.stops.get(position=3);stop.ttn_number='А-1; А-2';stop.save();self.accept(trip)
        self.assertEqual(self.upload(trip,stop,'А-1').status_code,302)
        stop.refresh_from_db();self.assertFalse(stop.signed_complete);self.assertFalse(trip.has_signed_ttn)
        self.upload(trip,stop,'А-2');stop.refresh_from_db();self.assertTrue(stop.signed_complete);self.assertFalse(trip.has_signed_ttn)
        before=Document.objects.count();self.upload(trip,trip.stops.first(),'bad');self.assertEqual(Document.objects.count(),before)
        foreign=Trip.objects.create(vehicle=self.vehicle,driver=self.other,status='completed',origin='X',destination='Y',cargo='X',planned_tons=1,planned_date=timezone.localdate(),created_by=self.admin)
        other_stop=TripStop.objects.create(trip=foreign,position=1,kind='unload',address='Foreign',cargo='X',planned_tons=1)
        self.upload(trip,other_stop);self.assertEqual(Document.objects.count(),before)
    def test_edit_preserves_progress_audits_changes_and_requires_route_acknowledgement(self):
        trip=self.create();self.accept(trip);self.act(trip,'arrive');self.act(trip,'depart')
        trip.refresh_from_db();first=trip.stops.first();self.client.force_login(self.admin)
        data=self.edit_data(trip);data['stops-2-contact_name']='Одержувач';data['stops-2-contact_phone']='0981234567'
        response=self.client.post(f'/trips/{trip.pk}/edit/',data);self.assertEqual(response.status_code,302)
        first.refresh_from_db();self.assertEqual(first.state,'completed')
        trip.refresh_from_db();self.assertTrue(trip.route_notice);self.assertIsNone(trip.route_notice_acknowledged_at)
        self.client.force_login(self.driver);self.assertContains(self.client.get('/'),'Логіст оновив маршрут')
        response=self.client.post(f'/driver/trips/{trip.pk}/action/',{'action':'ack_route','expected_revision':trip.updated_at.isoformat(),'request_id':str(uuid.uuid4())})
        self.assertEqual(response.json()['outcome'],'confirmed');trip.refresh_from_db();self.assertIsNotNone(trip.route_notice_acknowledged_at)
        self.client.force_login(self.admin);data=self.edit_data(trip);data['stops-0-ORDER']='3';data['stops-2-ORDER']='1'
        self.assertEqual(self.client.post(f'/trips/{trip.pk}/edit/',data).status_code,200)
        self.assertEqual(trip.stops.first().state,'completed')

    def test_redirect_after_arrival_preserves_history_and_requires_new_arrival(self):
        trip=self.create();self.accept(trip);self.act(trip,'arrive')
        trip.refresh_from_db();stop=trip.stops.first();old=stop.address
        self.client.force_login(self.admin);data=self.edit_data(trip)
        data.update({'stops-0-address':'Нова адреса','stops-0-settlement_code':'','stops-0-settlement_name':'','stops-0-region':'Область','stops-0-district':'Район','stops-0-community':'Громада'})
        response=self.client.post(f'/trips/{trip.pk}/edit/',data)
        self.assertEqual(response.status_code,302, (response.context['form'].errors, response.context['route_formset'].errors, response.context['route_formset'].non_form_errors()) if response.status_code == 200 else '')
        trip.refresh_from_db();stop.refresh_from_db()
        self.assertEqual(stop.state,'pending');self.assertIsNone(stop.arrived_at)
        self.assertTrue(trip.events.filter(action='Перенаправлено після прибуття',detail__contains=old).exists())
        self.assertTrue(trip.events.filter(action='Оновлено точку маршруту',detail__contains=old).exists())
        self.assertIsNone(trip.route_notice_acknowledged_at)
        self.client.force_login(self.driver)
        notices=self.client.get(reverse('driver_notifications')).json()
        self.assertTrue(any(n['type']=='route' and n['trip']==trip.pk for n in notices['items']))
        response=self.client.post(reverse('driver_action',args=[trip.pk]),{'action':'ack_route','expected_revision':trip.updated_at.isoformat(),'request_id':str(uuid.uuid4())})
        self.assertEqual(response.json()['outcome'],'confirmed')
        self.assertFalse(any(n['type']=='route' for n in self.client.get(reverse('driver_notifications')).json()['items']))
        self.assertEqual(self.act(trip,'arrive').json()['outcome'],'confirmed')

    @override_settings(WEBPUSH_PUBLIC_KEY='test-public',WEBPUSH_PRIVATE_KEY='test-private',WEBPUSH_SUBJECT='mailto:test@example.com')
    def test_redirect_while_driving_keeps_point_pending_and_notifies_driver(self):
        from fleet.models import PushSubscription, PushDelivery
        from fleet.push import process_deliveries
        PushSubscription.objects.create(user=self.driver,endpoint='https://fcm.googleapis.com/fcm/send/test-route',p256dh='test',auth='test')
        trip=self.create();self.accept(trip);trip.refresh_from_db()
        stop=trip.stops.first();old=stop.address
        self.client.force_login(self.admin);data=self.edit_data(trip)
        data.update({'stops-0-address':'Інша адреса','stops-0-settlement_code':'','stops-0-settlement_name':'','stops-0-region':'Область','stops-0-district':'Район','stops-0-community':'Громада'})
        response=self.client.post(f'/trips/{trip.pk}/edit/',data)
        self.assertEqual(response.status_code,302)
        self.assertTrue(PushDelivery.objects.filter(recipient=self.driver,trip=trip,kind='trip_change',status='pending').exists())
        with patch('fleet.push.webpush') as sender: process_deliveries()
        self.assertTrue(any('Зміни в рейсі' in call.kwargs['data'] for call in sender.call_args_list))
        self.assertTrue(PushDelivery.objects.filter(recipient=self.driver,trip=trip,kind='trip_change',status='sent').exists())
        stop.refresh_from_db();trip.refresh_from_db()
        self.assertEqual(stop.state,'pending');self.assertEqual(stop.address,'Інша адреса')
        self.assertTrue(trip.events.filter(action='Оновлено точку маршруту',detail__contains=old).exists())
        self.client.force_login(self.driver)
        self.assertContains(self.client.get(reverse('trip_detail',args=[trip.pk])),'Логіст оновив маршрут')
        self.assertTrue(any(n['type']=='route' for n in self.client.get(reverse('driver_notifications')).json()['items']))
        self.assertEqual(trip.current_stop.state,'pending')
        response=self.client.post(reverse('driver_action',args=[trip.pk]),{'action':'ack_route','expected_revision':trip.updated_at.isoformat(),'request_id':str(uuid.uuid4())})
        self.assertEqual(response.json()['outcome'],'confirmed')
        trip.refresh_from_db();self.assertIsNotNone(trip.route_notice_acknowledged_at)
        self.assertEqual(trip.current_stop.state,'pending')
        self.assertFalse(any(n['type']=='route' for n in self.client.get(reverse('driver_notifications')).json()['items']))
    def test_copy_route_clears_actuals_documents_and_execution(self):
        trip=self.create();stop=trip.stops.first();stop.actual_tons=Decimal('9.8');stop.state='completed';stop.ttn_number='OLD';stop.save()
        response=self.client.get('/trips/new/',{'copy_from':trip.pk})
        forms=response.context['route_formset'].forms;self.assertEqual(len(forms),4)
        self.assertNotIn('actual_tons',forms[0].initial);self.assertNotIn('ttn_number',forms[0].initial)
        self.assertNotIn('state',forms[0].initial)
        data=self.data();data.update(copy_from=trip.pk,scheduling='queued')
        self.assertEqual(self.client.post('/trips/new/',data).status_code,302)
        copied=Trip.objects.latest('pk');self.assertNotEqual(copied.pk,trip.pk)
        self.assertEqual(copied.stops.count(),4);self.assertFalse(copied.stops.exclude(state='pending').exists())
        self.assertFalse(copied.documents.exists());self.assertFalse(copied.stops.exclude(ttn_number='').exists())

    def test_future_point_cancellation_preserves_record_and_rejects_forged_management_count(self):
        trip=self.create();data=self.edit_data(trip)
        data['stops-INITIAL_FORMS']='0'
        response=self.client.post(f'/trips/{trip.pk}/edit/',data)
        self.assertEqual(response.status_code,200);self.assertEqual(trip.stops.count(),4)
        trip.refresh_from_db();data=self.edit_data(trip)
        data['stops-1-DELETE']='on';data['stops-3-planned_tons']='2'
        self.assertEqual(self.client.post(f'/trips/{trip.pk}/edit/',data).status_code,302)
        trip.refresh_from_db();self.assertEqual(trip.stops.count(),4)
        self.assertEqual(trip.stops.filter(state='cancelled').count(),1);self.assertEqual(trip.planned_tons,10)
        self.assertTrue(trip.events.filter(action='Скасовано точку маршруту').exists())

    def test_substitution_and_queued_handover_preserve_current_operation_and_documents(self):
        from fleet.transfers import substitute_driver,transfer_trip
        trip=self.create();self.accept(trip);self.act(trip,'arrive');self.act(trip,'depart');self.act(trip,'arrive')
        trip.refresh_from_db();current_id=trip.current_stop.pk
        substitute_driver(trip.pk,self.other.pk,self.admin,trip.updated_at)
        trip.refresh_from_db();self.assertEqual(trip.current_stop.pk,current_id);self.assertEqual(trip.current_stop.state,'arrived')
        self.assertEqual(trip.stops.filter(state='completed').count(),1)
        self.assertEqual(trip.vehicle_id,self.vehicle.pk);self.assertTrue(trip.requires_acceptance)
        self.client.force_login(self.driver);self.assertEqual(self.client.get(f'/trips/{trip.pk}/').status_code,404)

    def test_route_edit_stale_revision_and_incomplete_point_coordinates_are_rejected(self):
        trip=self.create();data=self.edit_data(trip)
        data['revision']=timezone.now().isoformat();data['stops-2-address']='Нова адреса'
        self.assertEqual(self.client.post(f'/trips/{trip.pk}/edit/',data).status_code,200)
        self.assertEqual(trip.stops.get(position=3).address,'Черкаси')
        data=self.edit_data(trip);data['stops-2-lat']='49.2'
        self.assertEqual(self.client.post(f'/trips/{trip.pk}/edit/',data).status_code,200)
        self.assertIsNone(trip.stops.get(position=3).lat)
        point=trip.stops.first();point.lat=Decimal('49.233');point.lon=Decimal('28.468');point.save()
        data=self.data();data.update(copy_from=trip.pk,scheduling='queued')
        data.update({'stops-0-address':'Інше місто','stops-0-lat':'49.233','stops-0-lon':'28.468'})
        response=self.client.post('/trips/new/',data);self.assertEqual(response.status_code,200)
        self.assertTrue(response.context['route_formset'].forms[0].errors)

    def test_report_aggregate_cannot_override_stop_weights(self):
        trip=self.create();trip.status='completed';trip.completed_at=timezone.now();trip.save()
        trip.stops.update(state='completed')
        trip.stops.filter(kind='unload').update(actual_tons=12)
        sync_trip(trip);self.assertEqual(trip.actual_tons,24)
        response=self.client.get(f'/report/trips/{trip.pk}/edit/');form=response.context['form']
        data={name:form[name].value() if form[name].value() is not None else '' for name in form.fields}
        data.update(actual_tons='1',origin='Підміна адреси',ttn_number='FAKE')
        self.assertEqual(self.client.post(f'/report/trips/{trip.pk}/edit/',data).status_code,302)
        trip.refresh_from_db();self.assertEqual(trip.actual_tons,24);self.assertEqual(trip.origin,'Вінниця');self.assertNotEqual(trip.ttn_number,'FAKE')
    def complete_report_for_attention_test(self, trip):
        from fleet.models import TripEconomics
        Trip.objects.filter(pk=trip.pk).update(distance_km=100, fuel_used_l=30)
        TripEconomics.objects.create(trip=trip, **{field: 0 for field in ['price','daily_rate','days','bonus_per_km','fuel_price','adblue_price','adblue_rate','depreciation_per_km','credit_per_km','extra_cost']})

    def test_attention_is_per_completed_point_and_skip_does_not_hide_other_point(self):
        trip=self.create();trip.status='completed';trip.completed_at=timezone.now();trip.save()
        trip.stops.update(state='completed',completed_at=timezone.now())
        self.complete_report_for_attention_test(trip)
        from fleet.attention import issue_snapshot
        issues=issue_snapshot(Trip.objects.filter(pk=trip.pk));self.assertEqual(len(issues),2)
        issue=next(i for i in issues if i['key']=='documents')
        self.client.post(f'/attention/{trip.pk}/documents/',{'action':'skip','stop_id':issue['stop'].pk,'episode':issue['episode'],'skip_version':''})
        self.assertEqual(AttentionSkip.objects.get().stop_id,issue['stop'].pk)
        self.assertEqual(sum(not i['skip'] for i in issue_snapshot(Trip.objects.filter(pk=trip.pk))),1)
        response=self.client.get('/attention/');self.assertEqual(response.context['active_count'],1);self.assertContains(response,'Точка 4')

    def test_missing_signed_documents_need_attention_only_after_trip_finishes(self):
        from fleet.attention import issue_snapshot, filter_attention
        trip=self.create();trip.status='road';trip.save()
        first_unload=trip.stops.filter(kind='unload').first()
        TripStop.objects.filter(pk=first_unload.pk).update(state='completed',completed_at=timezone.now())
        self.assertFalse(filter_attention(Trip.objects.filter(pk=trip.pk),'documents').exists())
        self.assertFalse(any(i['key']=='documents' for i in issue_snapshot(Trip.objects.filter(pk=trip.pk))))
        trip.status='completed';trip.completed_at=timezone.now();trip.save()
        trip.stops.update(state='completed',completed_at=timezone.now())
        self.assertTrue(filter_attention(Trip.objects.filter(pk=trip.pk),'documents').exists())
        self.assertEqual(sum(i['key']=='documents' for i in issue_snapshot(Trip.objects.filter(pk=trip.pk))),2)
    def test_cancel_preserves_completed_points_and_notifies_driver(self):
        trip=self.create();self.accept(trip);self.act(trip,'arrive');self.act(trip,'depart')
        self.client.force_login(self.admin);self.client.post(f'/trips/{trip.pk}/status/',{'status':'cancelled'})
        trip.refresh_from_db();self.assertEqual(trip.status,'cancelled');self.assertEqual(trip.stops.filter(state='completed').count(),1)
        self.assertEqual(trip.stops.filter(state='cancelled').count(),3)
        self.client.force_login(self.driver);self.assertContains(self.client.get('/'),'скасовано')
    def test_migration_preserves_legacy_documents_weights_and_unknown_times(self):
        trip=Trip.objects.create(vehicle=self.vehicle,driver=self.driver,status='completed',completed_at=timezone.now(),origin='Початок',destination='Кінець',cargo='Добрива',planned_tons=24,actual_tons=Decimal('23.8'),planned_date=timezone.localdate(),ttn_number='123',created_by=self.admin)
        document=Document.objects.create(trip=trip,kind='signed_ttn',file='existing.pdf',original_name='existing.pdf',size=1,content_type='application/pdf',uploaded_by=self.driver)
        original=trip.updated_at
        migration=importlib.import_module('fleet.migrations.0016_existing_trip_routes')
        from types import SimpleNamespace
        migration.forwards(apps,SimpleNamespace(connection=connection))
        trip.refresh_from_db();document.refresh_from_db()
        self.assertTrue(trip.route_enabled);self.assertEqual(trip.stops.count(),2)
        self.assertEqual(document.stop.kind,'unload');self.assertEqual(trip.actual_tons,Decimal('23.8'))
        self.assertTrue(trip.has_signed_ttn);self.assertEqual(trip.updated_at,original)
        self.assertIsNone(trip.stops.first().completed_at)

    def test_simple_route_has_operation_names_without_point_numbers_across_screens(self):
        trip=self.create([{'kind':'load','address':'Вінниця','cargo':'Добрива','planned_tons':24},{'kind':'unload','address':'Гайсин','cargo':'Добрива','planned_tons':24}])
        self.assertTrue(trip.is_simple_route)
        self.assertEqual(trip.route_progress,'')
        for url in [f'/trips/{trip.pk}/','/trips/','/report/']:
            response=self.client.get(url)
            self.assertEqual(response.status_code,200)
            # Stored audit text describes the route at the time of the event.
            operational_html=response.content.decode().split('<section class="panel trip-history"')[0]
            self.assertNotIn('Точка 1',operational_html)
            self.assertNotIn('Точка 2',operational_html)
            self.assertNotIn('Точок: 2',operational_html)
        from fleet.forms import DocumentForm
        field=DocumentForm(trip=trip).fields['stop']
        self.assertTrue(field.label_from_instance(trip.stops.first()).startswith('Завантаження:'))
        self.accept(trip)
        for point in trip.stops.all():
            self.act(trip,'arrive',point)
            if point.kind=='load': self.act(trip,'depart',point)
        result=self.act(trip,'depart').json()
        response=self.client.get(result['redirect'])
        self.assertContains(response,'Завантаження')
        self.assertContains(response,'Розвантаження')
        self.assertNotContains(response,'Точка 1')
        self.assertNotContains(response,'Точка 2')

    def test_attention_shows_separate_loading_and_signed_unloading_coverage(self):
        trip=self.create([{'kind':'load','address':'Вінниця','cargo':'Добрива','planned_tons':24,'ttn_number':'L-1; L-2'},{'kind':'unload','address':'Гайсин','cargo':'Добрива','planned_tons':24,'ttn_number':'U-1; U-2'}])
        trip.status='completed';trip.completed_at=timezone.now();trip.save()
        trip.stops.update(state='completed',completed_at=timezone.now())
        self.complete_report_for_attention_test(trip)
        from fleet.attention import issue_snapshot
        issue=issue_snapshot(Trip.objects.filter(pk=trip.pk))[0]
        self.assertEqual([check['complete'] for check in issue['document_checks']],[False,False])
        original_episode=issue['episode']
        first,last=list(trip.stops.all())
        # A scale ticket and unsigned unloading TTN are not replacements for these photos.
        self.upload(trip,first,'',kind='weight');self.upload(trip,last,'U-1',kind='ttn')
        self.upload(trip,first,'L-1',kind='ttn')
        issue=issue_snapshot(Trip.objects.filter(pk=trip.pk))[0]
        self.assertEqual(issue['document_checks'][0]['missing_numbers'],['L-2'])
        self.upload(trip,first,'L-2',kind='ttn')
        self.upload(trip,last,'U-1')
        issue=issue_snapshot(Trip.objects.filter(pk=trip.pk))[0]
        self.assertEqual([check['complete'] for check in issue['document_checks']],[True,False])
        self.assertEqual(issue['document_checks'][1]['missing_numbers'],['U-2'])
        self.assertEqual(issue['episode'],original_episode)
        response=self.client.get('/attention/')
        self.assertNotContains(response,'Точка 2')
        self.assertContains(response,'ТТН — додано')
        self.assertContains(response,'Підписана ТТН — не додано')
        self.assertContains(response,'Бракує ТТН: U-2')
        self.assertNotContains(response,'Бракує ТТН: L-1')
        for point,kind in [(first,'ttn'),(last,'signed_ttn')]:
            upload=self.client.get(f'/trips/{trip.pk}/?document_stop={point.pk}').context['upload_form']
            self.assertEqual(upload.initial['stop'],point)
            self.assertEqual(upload.initial['kind'],kind)
        self.upload(trip,last,'U-2')
        self.assertEqual(issue_snapshot(Trip.objects.filter(pk=trip.pk)),[])

    def test_dashboard_ttn_summary_tracks_files_by_kind_and_number(self):
        trip=self.create([{'kind':'load','address':'Вінниця','cargo':'Добрива','planned_tons':24,'ttn_number':'L-1; L-2'},{'kind':'unload','address':'Гайсин','cargo':'Добрива','planned_tons':24,'ttn_number':'U-1'}])
        first,last=list(trip.stops.all())
        page=self.client.get('/')
        self.assertContains(page,'ТТН завантаження');self.assertContains(page,'Підписана ТТН')
        self.assertContains(page,'Не завантажено',count=2)
        self.upload(trip,first,'L-1',kind='ttn')
        self.upload(trip,last,'U-1',kind='ttn')
        live=self.client.get('/?live=1').json()['cards']
        self.assertIn('Частково',live);self.assertIn('Не завантажено',live)
        updates=self.client.get('/trips/', {'ttn_live':'1', 'ids':str(trip.pk)}).json()
        self.assertEqual([item['state'] for item in updates['trips'][str(trip.pk)]], ['partial', 'missing'])
        listing=self.client.get('/trips/')
        row=next(item for item in listing.context['trips'] if item.pk==trip.pk)
        self.assertEqual([item['state'] for item in row.ttn_summary], ['partial', 'missing'])
        self.assertContains(listing, 'ТТН завантаження')
        self.assertContains(listing, 'Підписана ТТН')
        self.upload(trip,first,'L-2',kind='ttn');self.upload(trip,last,'U-1',kind='signed_ttn')
        live=self.client.get('/?live=1').json()['cards']
        self.assertEqual(live.count('Завантажено'),2)
        row=next(item for item in self.client.get('/trips/').context['trips'] if item.pk==trip.pk)
        self.assertEqual([item['state'] for item in row.ttn_summary], ['complete', 'complete'])
        updates=self.client.get('/trips/', {'ttn_live':'1', 'ids':str(trip.pk)}).json()
        self.assertEqual([item['state'] for item in updates['trips'][str(trip.pk)]], ['complete', 'complete'])

    def test_driver_ttn_notifications_are_per_recipient_and_persist(self):
        from fleet.models import TtnNotification
        second=User.objects.create_user('+380507000004', role='admin', full_name='Другий логіст', must_change_password=False)
        trip=self.create([{'kind':'load','address':'Вінниця','cargo':'Добрива','planned_tons':24},{'kind':'unload','address':'Гайсин','cargo':'Добрива','planned_tons':24}])
        point=trip.stops.first()
        self.accept(trip)
        response=self.upload(trip,point,kind='ttn')
        self.assertEqual(response.status_code,302)
        self.assertEqual(TtnNotification.objects.count(),2)
        self.assertEqual(self.client.get('/notifications/ttn/').status_code,403)
        self.client.force_login(self.admin)
        data=self.client.get('/notifications/ttn/').json()
        self.assertEqual(data['unread'],2)
        self.assertEqual(data['items'][0]['kind'],'ТТН із завантаження')
        self.assertEqual(self.client.post('/notifications/ttn/read/',{'id':data['items'][0]['id']}).status_code,200)
        remaining=self.client.get('/notifications/ttn/').json()
        self.assertEqual(remaining['unread'],1)
        self.assertTrue(any(i['kind']=='Водій прийняв рейс' and not i['read'] for i in remaining['items']))
        other_notice=TtnNotification.objects.get(recipient=second)
        self.client.post('/notifications/ttn/read/',{'id':other_notice.pk})
        other_notice.refresh_from_db()
        self.assertIsNone(other_notice.read_at)
        self.client.force_login(second)
        self.assertEqual(self.client.get('/notifications/ttn/').json()['unread'],2)

    def test_driver_other_documents_do_not_create_ttn_notifications(self):
        from fleet.models import TtnNotification
        trip=self.create([{'kind':'load','address':'Вінниця','cargo':'Добрива','planned_tons':24},{'kind':'unload','address':'Гайсин','cargo':'Добрива','planned_tons':24}])
        self.accept(trip)
        response=self.upload(trip,trip.stops.first(),kind='weight')
        self.assertEqual(response.status_code,302)
        self.assertFalse(TtnNotification.objects.exists())

    def test_dispatcher_controls_originals_and_uploads_per_point(self):
        trip=self.create([{'kind':'load','address':'Вінниця','cargo':'Добрива','planned_tons':24},{'kind':'unload','address':'Гайсин','cargo':'Добрива','planned_tons':24}])
        load,unload=list(trip.stops.all())
        page=self.client.get(f'/trips/{trip.pk}/')
        self.assertContains(page,f'document_stop={load.pk}')
        self.assertContains(page,f'document_stop={unload.pk}')
        self.assertNotContains(page,'Позначити отриманим')
        for point,kind in [(load,'ttn'),(unload,'signed_ttn')]:
            form=self.client.get(f'/trips/{trip.pk}/',{'document_stop':point.pk}).context['upload_form']
            self.assertEqual(form.initial['stop'].pk,point.pk)
            self.assertEqual(form.initial['kind'],kind)
        result=self.client.post(f'/trips/{trip.pk}/original/',{'stop_id':unload.pk,'received':'1'})
        self.assertEqual(result.status_code,410)
        unload.refresh_from_db();trip.refresh_from_db()
        self.assertFalse(unload.original_received);self.assertFalse(trip.original_received)

    def test_driver_delay_notifies_each_logistician_and_records_history(self):
        from fleet.models import TripDelay, DelayNotification
        trip=self.create(); self.accept(trip)
        response=self.client.post(f'/driver/trips/{trip.pk}/delay/', {'reason':'queue', 'comment':'Черга на складі', 'minutes':'45'})
        self.assertEqual(response.status_code,302)
        delay=TripDelay.objects.get(trip=trip)
        self.assertEqual(delay.minutes,45)
        self.assertTrue(AuditEvent.objects.filter(trip=trip,actor=self.driver,action='Водій повідомив про затримку').exists())
        self.assertTrue(DelayNotification.objects.filter(delay=delay,recipient=self.admin,read_at__isnull=True).exists())
        self.client.force_login(self.admin)
        notice=self.client.get('/notifications/ttn/').json()['items'][0]
        self.assertIn('Затримка',notice['kind'])
        self.assertEqual(self.client.post('/notifications/ttn/read/',{'id':notice['id']}).status_code,200)
        remaining=self.client.get('/notifications/ttn/').json()
        self.assertEqual(remaining['unread'],1)
        self.assertTrue(any(i['kind']=='Водій прийняв рейс' and not i['read'] for i in remaining['items']))
        self.assertContains(self.client.get(f'/trips/{trip.pk}/'), 'Черга на складі')
        self.assertContains(self.client.get(f'/trips/{trip.pk}/'), 'Повідомлення водія')
        live=self.client.get(f'/trips/{trip.pk}/?live=1').json()
        self.assertIn('Черга на складі',live['driver_messages_html'])
        self.assertIn('45 хв',live['driver_messages_html'])
        self.assertIn(self.driver.full_name,live['driver_messages_html'])
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(f'/driver/trips/{trip.pk}/delay/', {'reason':'queue'}).status_code,404)

    def test_driver_expense_and_receipt_are_private(self):
        from fleet.models import TripExpense
        trip=self.create();self.accept(trip)
        response=self.client.post(f'/driver/trips/{trip.pk}/expenses/', {'category':'fuel','amount':'1250.50','currency':'UAH','litres':'24.25','receipt':SimpleUploadedFile('receipt.pdf',b'%PDF-1.4\n test',content_type='application/pdf')})
        self.assertEqual(response.status_code,302)
        expense=TripExpense.objects.get(trip=trip)
        self.assertEqual(expense.amount,Decimal('1250.50'))
        self.assertTrue(AuditEvent.objects.filter(trip=trip,action='Додано витрату').exists())
        self.assertEqual(self.client.get(f'/expenses/{expense.pk}/receipt/').status_code,200)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/expenses/{expense.pk}/receipt/').status_code,404)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(f'/trips/{trip.pk}/'),'1250,50')

    def test_expenses_reject_invalid_amount_and_invalid_receipt(self):
        from fleet.models import TripExpense
        trip=self.create();self.accept(trip)
        invalid_amount=self.client.post(f'/driver/trips/{trip.pk}/expenses/',{'category':'fuel','amount':'-1','currency':'UAH'})
        self.assertEqual(invalid_amount.status_code,200)
        invalid_receipt=self.client.post(f'/driver/trips/{trip.pk}/expenses/',{'category':'fuel','amount':'5','currency':'UAH','receipt':SimpleUploadedFile('fake.pdf',b'not-a-pdf',content_type='application/pdf')})
        self.assertEqual(invalid_receipt.status_code,200)
        self.assertFalse(TripExpense.objects.exists())

    def test_attention_document_checks_are_batched_and_do_not_include_cancelled_points(self):
        trip=self.create();trip.status='completed';trip.completed_at=timezone.now();trip.save()
        trip.stops.update(state='completed',completed_at=timezone.now())
        self.complete_report_for_attention_test(trip)
        trip.stops.filter(position=1).update(state='cancelled')
        from fleet.attention import issue_snapshot
        with self.assertNumQueries(5):
            issues=issue_snapshot(Trip.objects.filter(pk=trip.pk))
            self.assertEqual(len(issues),2)
            self.assertTrue(all(len(i['document_checks'])==3 for i in issues))
            self.assertTrue(all([c['stop'].kind for c in i['document_checks']]==['load','unload','unload'] for i in issues))

