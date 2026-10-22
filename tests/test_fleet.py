import csv, tempfile
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
        cls.logistic=User.objects.create_user('+380502222222',cls.password,full_name='Логіст Тест',role='logistic',must_change_password=False)
        cls.manager=User.objects.create_user('+380503333333',cls.password,full_name='Керівник Тест',role='manager',must_change_password=False)
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
        r=self.client.get('/trips/');self.assertContains(r,'TEST0001');self.assertNotContains(r,'TEST0002')
        self.assertEqual(self.client.get(self.url('trip_detail',self.trip2.pk)).status_code,404)
        self.assertEqual(self.client.post(self.url('trip_status',self.trip2.pk),{'status':'loading'}).status_code,404)
        self.assertEqual(self.upload(self.trip2).status_code,404)
    def test_driver_cannot_create_reassign_manage_users_or_repairs(self):
        self.login_as(self.driver)
        self.assertEqual(self.client.get('/trips/new/').status_code,403)
        self.assertEqual(self.client.get('/accounts/').status_code,403)
        self.assertEqual(self.client.post(self.url('assign_driver',self.trip.pk),{'driver':self.spare.pk}).status_code,403)
        self.assertEqual(self.client.post(self.url('vehicle_repair',self.vehicle.pk)).status_code,403)
    def test_manager_is_read_only(self):
        self.login_as(self.manager)
        for path in ['/','/trips/','/report/',self.url('trip_detail',self.trip.pk)]: self.assertEqual(self.client.get(path).status_code,200)
        self.assertEqual(self.client.post(self.url('trip_status',self.trip.pk),{'status':'completed'}).status_code,403)
        self.assertEqual(self.upload().status_code,403)
        self.assertEqual(self.client.get('/accounts/').status_code,403)
    def test_logistic_can_manage_fleet_not_accounts(self):
        self.login_as(self.logistic)
        self.assertEqual(self.client.get('/accounts/').status_code,403)
        self.assertEqual(self.client.post(self.url('trip_status',self.trip.pk),{'status':'road'}).status_code,302)
        self.trip.refresh_from_db();self.assertEqual(self.trip.status,'road')
    def test_driver_must_follow_sequence(self):
        self.login_as(self.driver)
        self.client.post(self.url('trip_status',self.trip.pk),{'status':'completed'})
        self.trip.refresh_from_db();self.assertEqual(self.trip.status,'planned')
        for status in ['loading','road','unloading','completed']:
            self.client.post(self.url('trip_status',self.trip.pk),{'status':status,'actual_tons':'24.10'})
        self.trip.refresh_from_db();self.assertEqual(self.trip.status,'completed');self.assertEqual(self.trip.actual_tons,Decimal('24.10'));self.assertIsNotNone(self.trip.completed_at)
        self.assertEqual(AuditEvent.objects.filter(trip=self.trip).count(),4)
    def test_invalid_weight_is_not_saved(self):
        self.login_as(self.driver)
        for weight in ['NaN','Infinity','-2','61','24.123']:
            self.client.post(self.url('trip_status',self.trip.pk),{'status':'loading','actual_tons':weight})
            self.trip.refresh_from_db();self.assertEqual(self.trip.status,'planned');self.assertIsNone(self.trip.actual_tons)
    def test_reassignment_revokes_old_driver_access(self):
        self.login_as(self.logistic)
        self.assertEqual(self.client.post(self.url('assign_driver',self.trip.pk),{'driver':self.spare.pk}).status_code,302)
        self.login_as(self.driver);self.assertEqual(self.client.get(self.url('trip_detail',self.trip.pk)).status_code,404)
        self.login_as(self.spare);self.assertEqual(self.client.get(self.url('trip_detail',self.trip.pk)).status_code,200)
    def test_cannot_assign_busy_driver(self):
        self.login_as(self.logistic);self.client.post(self.url('assign_driver',self.trip.pk),{'driver':self.other.pk})
        self.trip.refresh_from_db();self.assertEqual(self.trip.driver_id,self.driver.pk)
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
        self.login_as(self.driver);r=self.upload();self.assertEqual(r.status_code,302)
        d=Document.objects.get();self.assertTrue(Path(d.file.path).is_file());self.assertNotIn('signed.pdf',d.file.name)
        r=self.client.get(self.url('document_download',d.pk));self.assertEqual(r.status_code,200);self.assertIn('attachment',r['Content-Disposition']);r.close()
        self.login_as(self.other);self.assertEqual(self.client.get(self.url('document_download',d.pk)).status_code,404)
        self.client.logout();self.assertEqual(self.client.get(self.url('document_download',d.pk)).status_code,302)
        self.assertEqual(self.client.get('/private-media/'+d.file.name).status_code,404)
    def test_upload_real_image_and_reject_fake_extension(self):
        self.login_as(self.driver)
        data=BytesIO();Image.new('RGB',(10,10)).save(data,format='PNG')
        self.upload(file=SimpleUploadedFile('weight.png',data.getvalue(),content_type='image/png'))
        self.assertEqual(Document.objects.count(),1)
        self.upload(file=SimpleUploadedFile('evil.png',b'<script>bad()</script>',content_type='image/png'))
        self.upload(file=SimpleUploadedFile('evil.html',b'%PDF-fake',content_type='application/pdf'))
        self.assertEqual(Document.objects.count(),1)
    @override_settings(MAX_DOCUMENT_BYTES=10)
    def test_reject_large_file(self):
        self.login_as(self.driver);self.upload();self.assertFalse(Document.objects.exists())
    def test_completed_trip_accepts_late_ttn_and_clears_missing(self):
        self.finish();self.login_as(self.driver)
        self.assertContains(self.client.get('/trips/?status=missing'),'TEST0001')
        self.upload()
        self.assertNotContains(self.client.get('/trips/?status=missing'),'TEST0001')
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
        self.login_as(self.manager);self.assertEqual(self.client.get('/report/').context['count'],2)
    def test_invalid_report_dates(self):
        self.login_as(self.driver)
        for query in ['?start=bad','?start=2026-10-25&end=2026-10-20']:
            self.assertEqual(self.client.get('/report/'+query).status_code,200)
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
        self.client.post(self.url('original_received',self.trip.pk));self.trip.refresh_from_db();self.assertTrue(self.trip.original_received)
        self.client.post(self.url('vehicle_repair',self.vehicle.pk));self.vehicle.refresh_from_db();self.assertFalse(self.vehicle.in_repair)
        self.finish();self.client.post(self.url('vehicle_repair',self.vehicle.pk));self.vehicle.refresh_from_db();self.assertTrue(self.vehicle.in_repair)
    def test_post_only_actions(self):
        self.login_as(self.admin)
        for name,pk in [('trip_status',self.trip.pk),('assign_driver',self.trip.pk),('document_upload',self.trip.pk),('account_action',self.driver.pk),('vehicle_repair',self.vehicle.pk),('original_received',self.trip.pk)]: self.assertEqual(self.client.get(self.url(name,pk)).status_code,405)
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
