from decimal import Decimal
from django.test import TestCase, override_settings
from django.urls import reverse
from fleet.models import User, DriverReportHistory, TripEconomics,DriverEconomicsDefaults,Trip,TripStop,Vehicle,TripExpense
from fleet.driver_reports import finances, report_insights, economics_initial
from fleet.management.commands.import_driver_reports import parse_sheet
from django.utils import timezone
from django.core.management import call_command
from django.core.management.base import CommandError
import json,tempfile
from pathlib import Path
from datetime import timedelta
from html.parser import HTMLParser


@override_settings(SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False,CSRF_COOKIE_SECURE=False,STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class DriverReportsTests(TestCase):
    def setUp(self):
        self.admin=User.objects.create_user('+380501234501',role='admin',full_name='Логіст',must_change_password=False)
        self.driver=User.objects.create_user('+380501234502',role='driver',full_name='Тестовий Водій',must_change_password=False)

    def test_driver_report_lists_only_active_ttn_for_selected_driver(self):
        from fleet.models import Document
        from fleet.permissions_models import ModeratorPermissionSettings
        vehicle=Vehicle.objects.create(plate='DOCS1',kind='tent')
        other=User.objects.create_user('+380501234503',role='driver',full_name='Інший водій',must_change_password=False)
        def make_trip(driver): return Trip.objects.create(vehicle=vehicle,driver=driver,created_by=self.admin,origin='Київ',destination='Одеса',cargo='Зерно',planned_tons=1,planned_date=timezone.localdate(),status='completed',completed_at=timezone.now())
        trip=make_trip(self.driver);other_trip=make_trip(other)
        def doc(trip,name,kind='ttn',deleted_at=None): return Document.all_objects.create(trip=trip,kind=kind,file='test/file.jpg',original_name=name,content_type='image/jpeg',size=100,uploaded_by=self.admin,deleted_at=deleted_at)
        doc(trip,'loading.jpg');doc(trip,'signed.jpg','signed_ttn');doc(trip,'other.jpg','other');doc(trip,'deleted.jpg',deleted_at=timezone.now());doc(other_trip,'private.jpg')
        url=reverse('driver_report_documents',args=[self.driver.pk])
        self.client.force_login(self.admin)
        report=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]))
        self.assertContains(report,'Усі ТТН водія')
        self.assertContains(report,'data-driver-ttn-dialog')
        page=self.client.get(url)
        self.assertContains(page,'loading.jpg');self.assertContains(page,'signed.jpg')
        for name in ('other.jpg','deleted.jpg','private.jpg'): self.assertNotContains(page,name)
        self.assertContains(page,f'Рейс №{trip.pk}')
        fragment=self.client.get(url,{'view':'dialog'})
        self.assertContains(fragment,'loading.jpg')
        self.assertNotContains(fragment,'<!doctype html>')
        moderator=User.objects.create_user('+380501234504',role='moderator',full_name='Модератор',must_change_password=False)
        permissions,_=ModeratorPermissionSettings.objects.get_or_create(pk=1);permissions.view_reports=True;permissions.view_documents=False;permissions.save()
        self.client.force_login(moderator)
        self.assertNotContains(self.client.get(reverse('driver_report_detail',args=[self.driver.pk])),'Усі ТТН водія')
        self.assertEqual(self.client.get(url).status_code,403)
        permissions.view_documents=True;permissions.save()
        self.assertEqual(self.client.get(url).status_code,200)
        self.client.force_login(self.driver);self.assertEqual(self.client.get(url).status_code,403)

    def test_archive_corrections_match_table_totals_and_excel(self):
        from io import BytesIO
        from zipfile import ZipFile
        from xml.etree import ElementTree
        row=DriverReportHistory.objects.create(source_sheet='Перевірка',source_row=901,driver=self.driver,driver_label=self.driver.full_name,source_raw=[12],source_values={'kilometers':'100','fuel_rate':'30','fuel_price':'50'},display_values={'extra_columns':[{'column':'AB','value':'30'}]},report_corrections={'source_number':'77','fuel_litres':'35.25'})
        self.client.force_login(self.admin)
        page=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]))
        self.assertContains(page,'driver_sheet_polish.css')
        self.assertNotContains(page,'driver-report-wide<link')
        rendered=page.context['historical'][0]
        self.assertEqual(rendered.source_number,'77')
        self.assertEqual(Decimal(str(rendered.extra_ab)),Decimal('35.25'))
        self.assertEqual(page.context['period_totals']['archive']['fuel_litres']['value'],Decimal('35.25'))
        result=self.client.get(reverse('driver_report_export',args=[self.driver.pk]))
        with ZipFile(BytesIO(result.content)) as book:
            sheet=ElementTree.fromstring(book.read('xl/worksheets/sheet2.xml'))
            ns={'x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            cells={c.attrib['r']:''.join(c.itertext()) for c in sheet.findall('.//x:row[@r="2"]/x:c',ns)}
            self.assertEqual(cells['A2'],'77')
            self.assertEqual(Decimal(cells['AB2']),Decimal('35.25'))
        row.refresh_from_db();self.assertEqual(row.source_values['fuel_rate'],'30')


    def test_driver_expenses_are_listed_summed_and_converted_in_report(self):
        from fleet.driver_report_export import live_row
        vehicle=Vehicle.objects.create(plate='EXPENSE1',kind='tent')
        trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Зерно',planned_tons=1,planned_date=timezone.localdate(),status='completed',completed_at=timezone.now(),distance_km=100,fuel_used_l=30)
        economics=TripEconomics.objects.create(trip=trip,price=10000,daily_rate=1000,days=1,bonus_per_km=3,fuel_price=50,adblue_price=20,adblue_rate=1,depreciation_per_km=9,credit_per_km=6,extra_cost=100)
        TripExpense.objects.create(trip=trip,driver=self.driver,category='road',amount=300,currency='UAH')
        TripExpense.objects.create(trip=trip,driver=self.driver,category='parking',amount=500,currency='UAH')
        TripExpense.objects.create(trip=trip,driver=self.driver,category='fuel',amount=800,currency='UAH')
        foreign=TripExpense.objects.create(trip=trip,driver=self.driver,category='road',amount=10,currency='EUR')
        self.client.force_login(self.admin)
        page=self.client.get(reverse('trip_detail',args=[trip.pk]))
        self.assertEqual({x['currency']:x['amount'] for x in page.context['expense_summary']['totals']},{'UAH':Decimal('1600'),'EUR':Decimal('10')})
        self.assertIsNone(finances(trip,economics)['total'])
        url=reverse('expense_conversion',args=[foreign.pk])
        self.assertEqual(self.client.post(url,{'converted_uah':'450,00'}).status_code,302)
        foreign.refresh_from_db();self.assertEqual(foreign.converted_uah,Decimal('450'))
        self.client.post(url,{'converted_uah':'1','previous':''})
        foreign.refresh_from_db();self.assertEqual(foreign.converted_uah,Decimal('450'))
        metrics=finances(trip,economics)
        self.assertEqual(metrics['other_uah'],Decimal('1250'))
        self.assertEqual(metrics['total'],Decimal('5670'))
        report=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]))
        self.assertEqual(report.context['period_totals']['live']['extra_cost']['value'],Decimal('1350'))
        self.assertIn('1250',live_row(report.context['trips'][0])[-1])
        self.client.force_login(self.driver)
        self.assertEqual(self.client.post(url,{'converted_uah':'1'}).status_code,403)
        foreign.refresh_from_db();self.assertEqual(foreign.converted_uah,Decimal('450'))

    def test_driver_report_uses_first_loading_arrival_for_period_totals_and_excel(self):
        from datetime import datetime
        from io import BytesIO
        from zipfile import ZipFile
        vehicle=Vehicle.objects.create(plate='DATE123',kind='tent')
        trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Зерно',planned_tons=25,planned_date='2026-11-25',status='completed',completed_at=timezone.make_aware(datetime(2026,11,27,12)),distance_km=100,route_enabled=True)
        Trip.objects.filter(pk=trip.pk).update(created_at=timezone.make_aware(datetime(2026,11,24,12)))
        TripStop.objects.create(trip=trip,position=1,kind='load',address='А',cargo='Зерно',planned_tons=25,state='completed',arrived_at=timezone.make_aware(datetime(2026,11,26,12)))
        TripStop.objects.create(trip=trip,position=2,kind='load',address='В',cargo='Зерно',planned_tons=25,state='completed',arrived_at=timezone.make_aware(datetime(2026,11,27,10)))
        self.client.force_login(self.admin)
        detail=reverse('driver_report_detail',args=[self.driver.pk])
        params={'start':'2026-11-26','end':'2026-11-26'}
        page=self.client.get(detail,params)
        self.assertEqual(page.context['trip_count'],1)
        self.assertEqual(page.context['trips'][0].report_fields['date'],'26.11.2026')
        self.assertEqual(page.context['period_totals']['live']['kilometers']['value'],100)
        self.assertEqual(self.client.get(detail,{'start':'2026-11-27','end':'2026-11-27'}).context['trip_count'],0)
        export=self.client.get(reverse('driver_report_export',args=[self.driver.pk]),params)
        with ZipFile(BytesIO(export.content)) as book:
            self.assertIn('26.11.2026',book.read('xl/worksheets/sheet1.xml').decode())
        self.assertEqual(self.client.get(reverse('report'),{'start':'2026-11-27','end':'2026-11-27'}).context['count'],1)
        TripEconomics.objects.create(trip=trip,report_overrides={'manual_v2':{'date':'2026-11-25'}})
        self.assertEqual(self.client.get(detail,params).context['trip_count'],0)
        corrected=self.client.get(detail,{'start':'2026-11-25','end':'2026-11-25'}).context['trips'][0]
        self.assertEqual(corrected.report_fields['date'],'25.11.2026')
        self.assertEqual(next(c for c in corrected.correction_list if c['label']=='Дата')['original'],'26.11.2026')

    def test_missing_first_loading_arrival_is_not_invented(self):
        from fleet.driver_reports import report_date
        vehicle=Vehicle.objects.create(plate='NODATE',kind='tent')
        trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Зерно',planned_tons=25,planned_date=timezone.localdate(),status='completed',completed_at=timezone.now(),route_enabled=True)
        first=TripStop.objects.create(trip=trip,position=1,kind='load',address='А',cargo='Зерно',planned_tons=25,state='completed')
        TripStop.objects.create(trip=trip,position=2,kind='load',address='Б',cargo='Зерно',planned_tons=25,state='completed',arrived_at=timezone.now())
        self.assertIsNone(report_date(trip,'live'))
        self.client.force_login(self.admin)
        url=reverse('driver_report_detail',args=[self.driver.pk])
        self.assertEqual(self.client.get(url).context['trip_count'],1)
        self.assertEqual(self.client.get(url,{'start':timezone.localdate().isoformat()}).context['trip_count'],0)
        first.state='cancelled'; first.save(update_fields=['state'])
        self.assertEqual(report_date(trip,'live'),timezone.localdate())

    def test_driver_report_includes_all_active_route_points_in_table_search_and_excel(self):
        from fleet.driver_report_export import live_row
        vehicle=Vehicle.objects.create(plate='ROUTE123',kind='tent')
        trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='Перша',destination='Остання',cargo='Зерно',planned_tons=25,planned_date=timezone.localdate(),status='completed',completed_at=timezone.now(),route_enabled=True)
        for position,kind,address,state in [(1,'load','Перша','completed'),(2,'load','Друга','completed'),(3,'unload','Третя','completed'),(4,'unload','Скасована','cancelled'),(5,'unload','Остання','completed')]:
            TripStop.objects.create(trip=trip,position=position,kind=kind,address=address,cargo='Зерно',planned_tons=10,state=state)
        self.client.force_login(self.admin)
        url=reverse('driver_report_detail',args=[self.driver.pk])
        page=self.client.get(url,{'q':'Друга'})
        row=page.context['trips'][0]
        self.assertEqual(row.report_fields['loading'],'Перша; Друга')
        self.assertEqual(row.report_fields['unloading'],'Третя; Остання')
        self.assertEqual(row.report_fields['route'],'Перша → Друга → Третя → Остання')
        self.assertNotIn('Скасована',row.report_fields['route'])
        values=live_row(row)
        self.assertEqual(values[15:18],['Перша; Друга','Третя; Остання','Перша → Друга → Третя → Остання'])
        self.assertContains(page,'Друга')
        export=self.client.get(reverse('driver_report_export',args=[self.driver.pk]),{'q':'Третя'})
        from io import BytesIO
        from zipfile import ZipFile
        with ZipFile(BytesIO(export.content)) as book:
            sheet=book.read('xl/worksheets/sheet1.xml').decode()
        self.assertIn('Перша → Друга → Третя → Остання',sheet)
        trip.acceptance_location='Стоянка';trip.save(update_fields=['acceptance_location'])
        with_start=self.client.get(url,{'q':'Стоянка'}).context['trips'][0]
        self.assertEqual(with_start.report_fields['route'],'Стоянка → Перша → Друга → Третя → Остання')
        self.assertEqual(live_row(with_start)[17],'Стоянка → Перша → Друга → Третя → Остання')
        trip.acceptance_location='Перша';trip.save(update_fields=['acceptance_location'])
        self.assertEqual(self.client.get(url).context['trips'][0].report_fields['route'],'Перша → Друга → Третя → Остання')
        TripEconomics.objects.create(trip=trip,report_overrides={'manual_v2':{'loading':'Ручне виправлення'}})
        corrected=self.client.get(url).context['trips'][0]
        self.assertEqual(corrected.report_fields['loading'],'Ручне виправлення')
        self.assertEqual(corrected.report_fields['route'],'Перша → Друга → Третя → Остання')
        self.assertEqual(next(change for change in corrected.correction_list if change['label']=='Точка завантаження')['original'],'Перша; Друга')

    def test_archive_summary_and_insights_are_collapsed_by_default(self):
        self.client.force_login(self.admin)
        page=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]))
        self.assertContains(page,'<details class="period-summary-group archive-disclosure">')
        self.assertContains(page,'<details class="insight-group archive-disclosure">')
        self.assertContains(page,'Рейси сайту')

    def test_archive_inline_edit_preserves_original_and_hides_stale_results(self):
        from fleet.driver_reports import archive_revision
        original={'daily_rate':'1000','days':'2','bonus_per_km':'3','fuel_price':'50','adblue_price':'20','adblue_rate':'1','depreciation_per_km':'9','credit_per_km':'6','revenue':'10000','extra_cost':'0','profit':'1234','driver_cost':'2300'}
        row=DriverReportHistory.objects.create(source_sheet='Тест',source_row=2,driver=self.driver,driver_label=self.driver.full_name,source_values=original,display_values=original)
        self.client.force_login(self.admin)
        response=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]))
        for key in ('daily_rate','days','bonus_per_km','fuel_price','adblue_price','fuel_rate','adblue_rate','depreciation_per_km','credit_per_km','revenue','extra_cost'):
            self.assertContains(response,f'data-field="{key}"')
        prefix=f'archive-{row.pk}'
        result=self.client.post(reverse('driver_archive_prices',args=[row.pk]),{'inline':'1',f'{prefix}-revision':archive_revision(row),f'{prefix}-daily_rate':'1200',f'{prefix}-days':'3',f'{prefix}-fuel_rate':'31.50'})
        self.assertEqual(result.status_code,302)
        row.refresh_from_db()
        self.assertEqual(row.source_values,original)
        self.assertEqual(row.report_corrections,{'daily_rate':'1200','days':'3','fuel_rate':'31.50'})
        response=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]))
        self.assertIsNone(response.context['period_totals']['archive']['wage']['value'])
        self.assertIsNone(response.context['period_totals']['archive']['profit']['value'])
        self.assertContains(response,'1200')

    def test_period_totals_keep_archive_separate_and_exclude_missing_values(self):
        vehicle=Vehicle.objects.create(plate='SUM001',kind='tent')
        now=timezone.now()
        def completed(days, km, fuel):
            trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Товар',planned_tons=1,planned_date=timezone.localdate(),status='completed',completed_at=now-timedelta(days=days),distance_km=km,fuel_used_l=fuel,route_enabled=True)
            TripStop.objects.create(trip=trip,position=1,kind='load',address='А',cargo='Товар',planned_tons=1,state='completed',arrived_at=now-timedelta(days=days))
            return trip
        chosen=completed(0,100,Decimal('30'))
        completed(0,50,None)
        completed(10,900,Decimal('200'))
        TripEconomics.objects.create(trip=chosen,price=10000,daily_rate=1000,days=2,bonus_per_km=3,fuel_price=50,adblue_price=20,adblue_rate=1,depreciation_per_km=9,credit_per_km=6,extra_cost=0)
        DriverReportHistory.objects.create(source_sheet='Тест',source_row=2,driver=self.driver,driver_label=self.driver.full_name,trip_date=timezone.localdate(),source_values={'kilometers':200,'driver_cost':400,'profit':500},display_values={})
        self.client.force_login(self.admin)
        response=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]),{'start':timezone.localdate().isoformat(),'end':timezone.localdate().isoformat()})
        live=response.context['period_totals']['live']
        archive=response.context['period_totals']['archive']
        self.assertEqual(live['kilometers']['value'],150)
        self.assertEqual(live['fuel_litres']['value'],30)
        self.assertEqual(live['fuel_litres']['missing'],1)
        self.assertEqual(live['wage']['value'],2300)
        self.assertEqual(live['wage']['missing'],1)
        self.assertEqual(archive['kilometers']['value'],200)
        self.assertEqual(archive['wage']['value'],400)
        self.assertContains(response,'Підсумки за вибраний період')

    def test_driver_sidebar_is_compact_and_marks_current_driver(self):
        self.client.force_login(self.admin)
        overview=self.client.get(reverse('driver_reports'))
        self.assertContains(overview,'class="report-driver-menu" open')
        self.assertContains(overview,'Усі водії')
        detail=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]))
        self.assertContains(detail,'class="report-driver-menu" open')
        self.assertContains(detail,'aria-current="page"')
        other=self.client.get(reverse('trip_list'))
        self.assertContains(other,'class="report-driver-menu"')
        self.assertNotContains(other,'class="report-driver-menu" open')

    def test_new_driver_and_completed_trip_appear_in_report_automatically(self):
        new_driver=User.objects.create_user('+380501234503',role='driver',full_name='Новий Водій',must_change_password=False)
        vehicle=Vehicle.objects.create(plate='NEW001',kind='tent')
        trip=Trip.objects.create(vehicle=vehicle,driver=new_driver,created_by=self.admin,origin='А',destination='Б',cargo='Товар',planned_tons=10,planned_date=timezone.localdate(),status='completed',completed_at=timezone.now(),distance_km=100)
        self.client.force_login(self.admin)
        overview=self.client.get(reverse('driver_reports'))
        self.assertContains(overview,'Новий Водій')
        detail=self.client.get(reverse('driver_report_detail',args=[new_driver.pk]))
        self.assertContains(detail,f'№{trip.pk}')
        self.assertContains(detail,'aria-current="page"')

    def test_excel_export_is_filtered_safe_and_keeps_archive_separate(self):
        import io,zipfile,xml.etree.ElementTree as ET
        vehicle=Vehicle.objects.create(plate='XLS001',kind='tent')
        trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='=не формула',destination='Б',cargo='Товар',planned_tons=10,planned_date=timezone.localdate(),status='completed',completed_at=timezone.now(),distance_km=100,fuel_used_l=Decimal('30'))
        TripEconomics.objects.create(trip=trip,price=10000,daily_rate=1000,days=1,bonus_per_km=3,fuel_price=50,adblue_price=20,adblue_rate=1,depreciation_per_km=9,credit_per_km=6,extra_cost=0)
        row=DriverReportHistory.objects.create(source_sheet='Тест',source_row=2,driver=self.driver,driver_label=self.driver.full_name,trip_date=timezone.localdate(),source_values={'kilometers':100,'profit':123},display_values={'date':'01.01.2026','kilometers':'100,00','profit':'123,00'},anomalies=['Перевірити'])
        self.client.force_login(self.admin)
        url=reverse('driver_report_export',args=[self.driver.pk])
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response['Content-Type'],'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        with zipfile.ZipFile(io.BytesIO(response.content)) as workbook:
            for name in workbook.namelist():
                if name.endswith('.xml'): ET.fromstring(workbook.read(name))
            site=workbook.read('xl/worksheets/sheet1.xml').decode()
            archive=workbook.read('xl/worksheets/sheet2.xml').decode()
            self.assertIn('5680',site);self.assertIn('=не формула',site);self.assertNotIn('<f>',site)
            self.assertIn('Перевірити',archive);self.assertIn('123',archive)
            self.assertEqual(len(ET.fromstring(site).find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheetData')[0]),30)
        future=timezone.localdate()+timedelta(days=1)
        filtered=self.client.get(url,{'start':future.isoformat()})
        with zipfile.ZipFile(io.BytesIO(filtered.content)) as workbook:
            self.assertNotIn(f'№{trip.pk}',workbook.read('xl/worksheets/sheet1.xml').decode())
            self.assertNotIn('Перевірити',workbook.read('xl/worksheets/sheet2.xml').decode())
        self.assertEqual(self.client.get(url,{'start':'not-a-date'}).status_code,400)
        self.client.force_login(self.driver)
        self.assertEqual(self.client.get(url).status_code,403)

    def test_search_filters_totals_and_excel_for_both_sources(self):
        import io,zipfile
        vehicle=Vehicle.objects.create(plate='FIND123',kind='tent')
        def trip(origin,distance):
            return Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin=origin,destination='Київ',cargo='Вантаж',planned_tons=1,planned_date=timezone.localdate(),status='completed',completed_at=timezone.now(),distance_km=distance)
        chosen=trip('Львів',100);other=trip('Одеса',200)
        DriverReportHistory.objects.create(source_sheet='Тест',source_row=2,source_raw=[15],driver=self.driver,driver_label=self.driver.full_name,trip_date=timezone.localdate(),source_values={'kilometers':300,'profit':50},display_values={'loading':'Львів','kilometers':'300','profit':'50'})
        DriverReportHistory.objects.create(source_sheet='Тест',source_row=3,source_raw=[16],driver=self.driver,driver_label=self.driver.full_name,trip_date=timezone.localdate(),source_values={'kilometers':400,'profit':70},display_values={'loading':'Одеса','kilometers':'400','profit':'70'})
        self.client.force_login(self.admin)
        response=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]),{'q':'львів'})
        self.assertEqual(response.context['trip_count'],1)
        self.assertEqual(response.context['history_count'],1)
        self.assertEqual(response.context['history_sums']['kilometers'],300)
        self.assertEqual(response.context['insight_groups'][1][1]['count'],1)
        self.assertContains(response,'q=%D0%BB%D1%8C%D0%B2%D1%96%D0%B2')
        result=self.client.get(reverse('driver_report_export',args=[self.driver.pk]),{'q':'львів'})
        with zipfile.ZipFile(io.BytesIO(result.content)) as workbook:
            site=workbook.read('xl/worksheets/sheet1.xml').decode()
            archive=workbook.read('xl/worksheets/sheet2.xml').decode()
            self.assertIn(f'№{chosen.pk}',site);self.assertNotIn(f'№{other.pk}',site)
            self.assertIn('Львів',archive);self.assertNotIn('Одеса',archive)
        self.assertEqual(self.client.get(reverse('driver_report_detail',args=[self.driver.pk]),{'q':f'№{other.pk}'}).context['trip_count'],1)

    def test_audit_marks_future_and_missing_distance_without_changing_source(self):
        row=DriverReportHistory.objects.create(source_sheet='Тест',source_row=3,driver_label='Тест',trip_date=timezone.localdate()+timedelta(days=1),source_values={'kilometers': None},anomalies=['Наявне зауваження'])
        call_command('audit_driver_reports',apply=True,verbosity=0)
        row.refresh_from_db()
        self.assertEqual(row.source_values,{'kilometers': None})
        self.assertEqual(len(row.anomalies),3)
        call_command('audit_driver_reports',apply=True,verbosity=0)
        row.refresh_from_db()
        self.assertEqual(len(row.anomalies),3)

    def test_sheet_columns_preserve_source_order_and_extra_ab(self):
        from fleet.management.commands.import_driver_reports import KEYS
        displayed={name:f'value-{index}' for index,name in enumerate(KEYS)}
        displayed['extra_columns']=[{'column':'AB','value':'189,00'}]
        DriverReportHistory.objects.create(source_sheet='Тест (тент)',source_row=2,driver=self.driver,driver_label=self.driver.full_name,source_raw=[7],source_values={},display_values=displayed)
        self.client.force_login(self.admin)
        html=self.client.get(reverse('driver_report_detail',args=[self.driver.pk])).content.decode()
        class Cells(HTMLParser):
            def __init__(self): super().__init__(); self.in_row=False;self.in_cell=False;self.values=[];self.current=''
            def handle_starttag(self,tag,attrs):
                if tag=='tr' and not self.values: self.in_row=True
                if self.in_row and tag=='td': self.in_cell=True;self.current=''
            def handle_data(self,data):
                if self.in_cell: self.current+=data
            def handle_endtag(self,tag):
                if self.in_cell and tag=='td': self.values.append(self.current.strip());self.in_cell=False
        parser=Cells();parser.feed(html)
        self.assertEqual(len(parser.values),29)
        self.assertTrue(parser.values[0].startswith('7'))
        self.assertEqual([value.removesuffix('✎') for value in parser.values[1:27]],[f'value-{i}' for i in range(26)])
        self.assertEqual(parser.values[27].removesuffix('✎'),'189,00')

    def test_report_preserves_historical_values_and_separate_live_data(self):
        DriverReportHistory.objects.create(source_sheet='Тест (тент)',source_row=2,driver=self.driver,driver_label='Тестовий Водій',vehicle_kind='tent',source_values={'kilometers':600,'revenue':33840,'expenses':21905.8,'profit':11934},anomalies=['Прибуток не збігається з розрахунком'])
        self.client.force_login(self.admin)
        response=self.client.get(reverse('driver_reports'))
        self.assertContains(response,'Тестовий Водій')
        detail=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]))
        self.assertContains(detail,'33')
        self.assertContains(detail,'Прибуток не збігається з розрахунком')
        self.client.force_login(self.driver)
        self.assertEqual(self.client.get(reverse('driver_reports')).status_code,403)

    def test_financial_calculation_requires_inputs_and_uses_actual_fuel(self):
        vehicle=Vehicle.objects.create(plate='DRA001',kind='tent')
        trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Товар',planned_tons=10,planned_date=timezone.localdate(),status='completed',distance_km=100,fuel_used_l=Decimal('30'))
        inputs=TripEconomics(trip=trip,price=Decimal('10000'),daily_rate=Decimal('1000'),days=Decimal('1'),bonus_per_km=Decimal('3'),fuel_price=Decimal('50'),adblue_price=Decimal('20'),adblue_rate=Decimal('1'),depreciation_per_km=Decimal('9'),credit_per_km=Decimal('6'),extra_cost=Decimal('0'))
        result=finances(trip,inputs)
        self.assertEqual(result['fuel'],Decimal('1500'));self.assertEqual(result['total'],Decimal('4320'));self.assertEqual(result['profit'],Decimal('5680'))
        inputs.fuel_price=None;self.assertIsNone(finances(trip,inputs)['profit'])
        inputs.fuel_price=Decimal('50')
        TripExpense.objects.create(trip=trip,driver=self.driver,category='road',amount=Decimal('100'),currency='UAH')
        TripExpense.objects.create(trip=trip,driver=self.driver,category='fuel',amount=Decimal('800'),currency='UAH')
        self.assertEqual(finances(trip,inputs)['total'],Decimal('4420'))
        TripExpense.objects.create(trip=trip,driver=self.driver,category='parking',amount=Decimal('10'),currency='EUR')
        self.assertIsNone(finances(trip,inputs)['total'])

    def test_report_only_fuel_rate_correction_recalculates_cost_without_changing_trip(self):
        vehicle=Vehicle.objects.create(plate='COR002',kind='tent')
        trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Товар',planned_tons=10,planned_date=timezone.localdate(),status='completed',distance_km=100,fuel_used_l=Decimal('30'))
        inputs=TripEconomics(trip=trip,price=10000,daily_rate=1000,days=1,bonus_per_km=3,fuel_price=50,adblue_price=20,adblue_rate=1,depreciation_per_km=9,credit_per_km=6,extra_cost=0,report_overrides={'manual_v2':{'fuel_rate':'35'}})
        result=finances(trip,inputs)
        self.assertEqual(result['fuel_rate'],Decimal('35'))
        self.assertEqual(result['fuel'],Decimal('1750'))
        self.assertEqual(result['profit'],Decimal('5430'))
        self.assertEqual(trip.fuel_used_l,Decimal('30'))

    def test_live_fuel_rate_is_corrected_only_in_report_and_can_be_reset(self):
        vehicle=Vehicle.objects.create(plate='OVR001',kind='tent',odometer_km=1000)
        trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Товар',planned_tons=10,planned_date=timezone.localdate(),status='completed',distance_km=100,fuel_used_l=Decimal('30'))
        economics=TripEconomics.objects.create(trip=trip,price=10000,daily_rate=1000,days=1,bonus_per_km=3,fuel_price=50,adblue_price=20,adblue_rate=1,depreciation_per_km=9,credit_per_km=6,extra_cost=0)
        self.client.force_login(self.admin)
        url=reverse('driver_report_fuel_rate',args=[trip.pk])
        response=self.client.post(url,{'revision':economics.updated_at.isoformat(),'value':'35'})
        self.assertEqual(response.status_code,200)
        trip.refresh_from_db();vehicle.refresh_from_db();economics.refresh_from_db()
        self.assertEqual(trip.fuel_used_l,Decimal('30'))
        self.assertEqual(vehicle.odometer_km,1000)
        self.assertEqual(finances(trip,economics)['fuel'],Decimal('1750'))
        response=self.client.post(url,{'revision':economics.updated_at.isoformat(),'value':''})
        self.assertEqual(response.status_code,200)
        economics.refresh_from_db();self.assertEqual(finances(trip,economics)['fuel'],Decimal('1500'))

    def test_manual_formula_value_updates_dependent_report_totals(self):
        vehicle=Vehicle.objects.create(plate='OVR002',kind='tent')
        trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Товар',planned_tons=10,planned_date=timezone.localdate(),status='completed',distance_km=100,fuel_used_l=Decimal('30'))
        economics=TripEconomics.objects.create(trip=trip,price=10000,daily_rate=1000,days=1,bonus_per_km=3,fuel_price=50,adblue_price=20,adblue_rate=1,depreciation_per_km=9,credit_per_km=6,extra_cost=0)
        self.client.force_login(self.admin)
        response=self.client.post(reverse('driver_report_fuel_rate',args=[trip.pk]),{'field':'fuel','revision':economics.updated_at.isoformat(),'value':'2000'})
        self.assertEqual(response.status_code,200)
        economics.refresh_from_db();trip.refresh_from_db()
        result=finances(trip,economics)
        self.assertEqual(result['fuel'],Decimal('2000'))
        self.assertEqual(result['profit'],Decimal('5180'))
        self.assertEqual(trip.fuel_used_l,Decimal('30'))

    def test_report_only_distance_override_updates_calculations_not_odometer(self):
        vehicle=Vehicle.objects.create(plate='OVR003',kind='tent',odometer_km=1000)
        trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Товар',planned_tons=10,planned_date=timezone.localdate(),status='completed',completed_at=timezone.now(),distance_km=100,fuel_used_l=Decimal('30'))
        econ=TripEconomics.objects.create(trip=trip,price=10000,daily_rate=1000,days=1,bonus_per_km=3,fuel_price=50,adblue_price=20,adblue_rate=1,depreciation_per_km=9,credit_per_km=6,extra_cost=0)
        self.client.force_login(self.admin)
        response=self.client.post(reverse('driver_report_fuel_rate',args=[trip.pk]),{'field':'kilometers','revision':econ.updated_at.isoformat(),'value':'200'})
        self.assertEqual(response.status_code,200)
        econ.refresh_from_db();trip.refresh_from_db();vehicle.refresh_from_db()
        self.assertEqual(trip.distance_km,100);self.assertEqual(vehicle.odometer_km,1000)
        self.assertEqual(finances(trip,econ)['fuel_rate'],Decimal('15'))
        page=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]))
        self.assertEqual(page.context['period_totals']['live']['kilometers']['value'],200)

    def test_archive_missing_ratios_are_calculated_without_mutating_import(self):
        from fleet.driver_reports import archive_effective
        source={'kilometers':1357,'revenue':135099,'expenses':'98851.15125','profit':'36247.84875'}
        row=DriverReportHistory(source_values=source,report_corrections={})
        result=archive_effective(row)
        self.assertAlmostEqual(result['cost_per_km'],Decimal('72.845358'),places=5)
        self.assertAlmostEqual(result['profit_per_km'],Decimal('26.711753'),places=5)
        self.assertAlmostEqual(result['margin'],Decimal('26.830583'),places=5)
        self.assertEqual(row.source_values,source)

    def test_archive_formula_can_be_corrected_and_used_in_totals(self):
        from fleet.driver_reports import archive_revision,period_totals
        source={'kilometers':100,'revenue':10000,'expenses':4000,'profit':6000,'cost_per_km':40}
        row=DriverReportHistory.objects.create(source_sheet='Архів',source_row=12,driver=self.driver,driver_label=self.driver.full_name,source_values=source)
        self.client.force_login(self.admin)
        response=self.client.post(reverse('driver_report_archive_cell',args=[row.pk]),{'field':'profit','value':'5000','revision':archive_revision(row)})
        self.assertEqual(response.status_code,200)
        row.refresh_from_db();self.assertEqual(row.source_values,source)
        self.assertEqual(period_totals([row],[])['archive']['profit']['value'],Decimal('5000'))

    def test_every_report_data_cell_is_editable_for_live_and_archive(self):
        vehicle=Vehicle.objects.create(plate='GRID01',kind='tent')
        trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Товар',planned_tons=10,planned_date=timezone.localdate(),status='completed',completed_at=timezone.now(),distance_km=100,fuel_used_l=30)
        TripEconomics.objects.create(trip=trip)
        DriverReportHistory.objects.create(source_sheet='Архів',source_row=1,driver=self.driver,driver_label=self.driver.full_name,source_values={'kilometers':100})
        self.client.force_login(self.admin)
        page=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]))
        class GridParser(HTMLParser):
            def __init__(self): super().__init__();self.rows=[];self.row=None
            def handle_starttag(self,tag,attrs):
                attrs=dict(attrs)
                if tag=='tr' and 'data-sheet-row' in attrs: self.row=[]
                if tag=='td' and self.row is not None: self.row.append(attrs.get('class',''))
            def handle_endtag(self,tag):
                if tag=='tr' and self.row is not None: self.rows.append(self.row);self.row=None
        parser=GridParser();parser.feed(page.content.decode())
        self.assertEqual(len(parser.rows),2)
        for row in parser.rows:
            self.assertGreaterEqual(len(row),29)
            self.assertTrue(all('sheet-editable' in cell or 'sheet-result-editable' in cell for cell in row[:28]),row)

    def test_archive_imported_formula_is_marked_for_confirmation(self):
        formulas=['']*27;formulas[3]='=1+1'
        DriverReportHistory.objects.create(source_sheet='Архів',source_row=2,driver=self.driver,driver_label=self.driver.full_name,source_values={'daily_rate':2},source_formulas=formulas)
        self.client.force_login(self.admin)
        page=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]))
        self.assertContains(page,'data-field="daily_rate" data-formula="1"')

    def test_archive_correction_recalculates_totals_and_excel_preserves_source(self):
        from fleet.driver_reports import archive_revision
        from fleet.driver_report_export import archive_row
        source={'kilometers':100,'daily_rate':1000,'days':1,'bonus_per_km':3,'adblue_price':20,'fuel_price':50,'fuel_rate':30,'adblue_rate':1,'depreciation_per_km':9,'credit_per_km':6,'revenue':10000,'extra_cost':0,'fuel_cost':1500,'adblue_cost':20,'depreciation_cost':1500,'driver_cost':1300,'expenses':4320,'profit':5680}
        row=DriverReportHistory.objects.create(source_sheet='Архів',source_row=20,driver=self.driver,driver_label=self.driver.full_name,source_values=source)
        self.client.force_login(self.admin)
        response=self.client.post(reverse('driver_report_archive_cell',args=[row.pk]),{'field':'fuel_rate','value':'35','revision':archive_revision(row)})
        self.assertEqual(response.status_code,200)
        row.refresh_from_db();self.assertEqual(row.source_values,source)
        page=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]))
        self.assertEqual(page.context['period_totals']['archive']['fuel_cost']['value'],Decimal('1750'))
        self.assertEqual(page.context['period_totals']['archive']['profit']['value'],Decimal('5430'))
        exported=archive_row(page.context['historical'][0])
        self.assertEqual(exported[18],Decimal('1750'))
        self.assertEqual(exported[23],Decimal('5430'))

    def test_report_date_filter_uses_report_correction_and_not_trip_date(self):
        from datetime import date
        vehicle=Vehicle.objects.create(plate='GRID02',kind='tent')
        trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Товар',planned_tons=10,planned_date=timezone.localdate(),status='completed',completed_at=timezone.now(),distance_km=100)
        econ=TripEconomics.objects.create(trip=trip)
        self.client.force_login(self.admin)
        response=self.client.post(reverse('driver_report_fuel_rate',args=[trip.pk]),{'field':'date','value':'01.01.2020','revision':econ.updated_at.isoformat()})
        self.assertEqual(response.status_code,200)
        detail=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]),{'start':'2020-01-01','end':'2020-01-01'})
        self.assertEqual(detail.context['trip_count'],1)
        self.assertEqual(detail.context['trips'][0].report_fields['date'],'01.01.2020')
        trip.refresh_from_db();self.assertNotEqual(timezone.localtime(trip.completed_at).date(),date(2020,1,1))

    def test_archive_multiple_formula_corrections_conflict_and_restore(self):
        from fleet.driver_reports import archive_revision,archive_effective
        source={'kilometers':100,'revenue':10000,'expenses':4000,'profit':6000}
        row=DriverReportHistory.objects.create(source_sheet='Архів',source_row=30,driver=self.driver,driver_label=self.driver.full_name,source_values=source)
        url=reverse('driver_report_archive_cell',args=[row.pk]);old=archive_revision(row)
        self.client.force_login(self.driver)
        self.assertEqual(self.client.post(url,{'field':'profit','value':'5000','revision':old}).status_code,403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(url,{'field':'profit','value':'5000','revision':old}).status_code,200)
        self.assertEqual(self.client.post(url,{'field':'kilometers','value':'200','revision':old}).status_code,409)
        row.refresh_from_db();self.assertEqual(archive_effective(row)['profit_per_km'],Decimal('50'))
        self.assertEqual(self.client.post(url,{'field':'profit','value':'','revision':archive_revision(row)}).status_code,200)
        row.refresh_from_db();self.assertEqual(archive_effective(row)['profit'],6000)
        self.assertEqual(row.source_values,source)

    def test_live_report_corrections_flow_to_export_and_not_trip(self):
        from fleet.driver_report_export import live_row
        vehicle=Vehicle.objects.create(plate='GRID03',kind='tent',odometer_km=1000)
        trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Товар',planned_tons=10,planned_date=timezone.localdate(),status='completed',completed_at=timezone.now(),distance_km=100,fuel_used_l=30)
        econ=TripEconomics.objects.create(trip=trip,price=10000,daily_rate=1000,days=1,bonus_per_km=3,fuel_price=50,adblue_price=20,adblue_rate=1,depreciation_per_km=9,credit_per_km=6,extra_cost=0)
        self.client.force_login(self.admin);url=reverse('driver_report_fuel_rate',args=[trip.pk])
        for field,value in [('kilometers','200'),('loading','м. Нове Місто'),('fuel','2000')]:
            econ.refresh_from_db()
            self.assertEqual(self.client.post(url,{'field':field,'value':value,'revision':econ.updated_at.isoformat()}).status_code,200)
        page=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]));exported=live_row(page.context['trips'][0])
        self.assertEqual(exported[2],Decimal('200'));self.assertEqual(exported[15],'м. Нове Місто')
        self.assertEqual(exported[18],Decimal('2000'))
        trip.refresh_from_db();vehicle.refresh_from_db()
        self.assertEqual(trip.distance_km,100);self.assertEqual(trip.origin,'А');self.assertEqual(vehicle.odometer_km,1000)

    def test_manual_margin_is_used_in_weighted_report_insights(self):
        from fleet.driver_reports import report_insights
        vehicle=Vehicle.objects.create(plate='GRID04',kind='tent')
        trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Товар',planned_tons=10,planned_date=timezone.localdate(),status='completed',completed_at=timezone.now(),distance_km=100,fuel_used_l=30)
        TripEconomics.objects.create(trip=trip,price=10000,report_overrides={'manual_v2':{'margin':'25'}})
        self.assertEqual(report_insights([], [trip])['live']['margin'],Decimal('25'))

    def test_calculated_columns_are_read_only_and_ignore_old_overrides(self):
        vehicle=Vehicle.objects.create(plate='COR001',kind='tent')
        trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Товар',planned_tons=10,planned_date=timezone.localdate(),status='completed',distance_km=100,fuel_used_l=Decimal('30'))
        inputs=TripEconomics.objects.create(trip=trip,price=10000,daily_rate=1000,days=1,bonus_per_km=3,fuel_price=50,adblue_price=20,adblue_rate=1,depreciation_per_km=9,credit_per_km=6,extra_cost=0,report_overrides={'fuel':'1'})
        self.assertEqual(finances(trip,inputs)['fuel'],Decimal('1500'))
        self.assertEqual(finances(trip,inputs)['profit'],Decimal('5680'))
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(f'/report/result/live/{trip.pk}/',{'field':'fuel','value':'1'}).status_code,404)
        page=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]))
        self.assertContains(page,'data-field="fuel"')
        inputs.refresh_from_db();self.assertEqual(inputs.report_overrides,{'fuel':'1'})

    def test_preview_calculates_without_saving_and_checks_permissions(self):
        vehicle=Vehicle.objects.create(plate='PRV001',kind='tent')
        trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Товар',planned_tons=10,planned_date=timezone.localdate(),status='completed',distance_km=100,fuel_used_l=Decimal('30'))
        data={'price':'10000','daily_rate':'1000','days':'1','bonus_per_km':'3','fuel_price':'50','adblue_price':'20','adblue_rate':'1','depreciation_per_km':'9','credit_per_km':'6','extra_cost':'0','revision':''}
        post={f'trip-{trip.pk}-{key}':value for key,value in data.items()}
        url=reverse('driver_economics_preview',args=[trip.pk])
        self.client.force_login(self.admin)
        response=self.client.post(url,post)
        self.assertEqual(response.status_code,200)
        self.assertIn(response.json()['values']['profit'],('5680,00','5680.00'))
        self.assertFalse(TripEconomics.objects.filter(trip=trip).exists())
        post[f'trip-{trip.pk}-fuel_price']='bad'
        self.assertEqual(self.client.post(url,post).status_code,400)
        self.client.force_login(self.driver)
        self.assertEqual(self.client.post(url,post).status_code,403)

    def test_all_nine_financial_columns_follow_documented_formulas(self):
        vehicle=Vehicle.objects.create(plate='FRM001',kind='tent')
        trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Товар',planned_tons=10,planned_date=timezone.localdate(),status='completed',distance_km=100,fuel_used_l=Decimal('30'))
        inputs=TripEconomics(trip=trip,price=Decimal('10000'),daily_rate=Decimal('1000'),days=Decimal('1'),bonus_per_km=Decimal('3'),fuel_price=Decimal('50'),adblue_price=Decimal('20'),adblue_rate=Decimal('1'),depreciation_per_km=Decimal('9'),credit_per_km=Decimal('6'),extra_cost=Decimal('0'))
        result=finances(trip,inputs)
        expected={'fuel':'1500','adblue':'20','depreciation':'1500','wage':'1300','total':'4320','profit':'5680','cost_per_km':'43.2','profit_per_km':'56.8','margin':'56.8'}
        for key,value in expected.items(): self.assertEqual(result[key],Decimal(value),key)

    def test_import_flags_bad_historical_result_without_changing_source(self):
        row=[1,46328,100,1000,1,3,20,50,30,1,9,6,10000,0,'Місто','Склад','Клієнт','Маршрут',1500,20,1500,1300,4320,22,43.2,.22]
        sheet={'name':'Тестовий (тент)','values':[row]}
        parsed=parse_sheet(sheet,{'values':[[]]},{'values':[row]},[self.driver])
        self.assertEqual(len(parsed),1);self.assertEqual(parsed[0].source_values['profit'],22)
        self.assertTrue(parsed[0].anomalies)

    def test_saving_economics_creates_calculation_and_checks_permissions(self):
        vehicle=Vehicle.objects.create(plate='DRA002',kind='tent')
        trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Товар',planned_tons=10,planned_date=timezone.localdate(),status='completed',distance_km=100,fuel_used_l=Decimal('30'))
        data={'price':'10000','daily_rate':'1000','days':'1','bonus_per_km':'3','fuel_price':'50','adblue_price':'20','adblue_rate':'1','depreciation_per_km':'9','credit_per_km':'6','extra_cost':'0','revision':''}
        self.client.force_login(self.driver)
        self.assertEqual(self.client.post(reverse('driver_economics_edit',args=[trip.pk]),data).status_code,403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('driver_economics_edit',args=[trip.pk]),data).status_code,302)
        self.assertEqual(finances(trip,TripEconomics.objects.get(trip=trip))['profit'],Decimal('5680'))

    def test_completed_trip_price_can_be_corrected_and_is_linked_from_report(self):
        vehicle=Vehicle.objects.create(plate='DRAEDIT',kind='tent')
        trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Товар',planned_tons=10,planned_date=timezone.localdate(),status='completed',completed_at=timezone.now(),distance_km=100)
        record=TripEconomics.objects.create(trip=trip,price=10000,fuel_price=50)
        self.client.force_login(self.admin)
        report=self.client.get(reverse('report'))
        self.assertContains(report,reverse('driver_report_detail',args=[self.driver.pk])+f'#trip-{trip.pk}')
        detail=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]))
        self.assertContains(detail,f'id="trip-{trip.pk}"')
        self.assertContains(detail,'class="inline-editor" hidden')
        self.assertContains(detail,'class="sheet-cell-button"')
        self.assertNotContains(detail,'<summary>Редагувати</summary>')
        data={'price':'12000','fuel_price':'55','revision':record.updated_at.isoformat()}
        self.assertEqual(self.client.post(reverse('driver_economics_edit',args=[trip.pk]),data).status_code,302)
        record.refresh_from_db();self.assertEqual(record.price,12000);self.assertEqual(record.fuel_price,55)
        self.assertTrue(trip.events.filter(action='Оновлено економіку рейсу').exists())

    def test_archive_prices_are_correctable_without_changing_source_or_counting_stale_profit(self):
        from fleet.driver_reports import archive_revision, history_totals, report_insights
        row=DriverReportHistory.objects.create(source_sheet='Водій',source_row=9,driver=self.driver,driver_label=self.driver.full_name,source_values={'revenue':'10000','fuel_price':'50','adblue_price':'20','kilometers':'100','expenses':'4000','profit':'6000'},display_values={'revenue':'10000','fuel_price':'50','adblue_price':'20'},source_formulas=['=A1'])
        url=reverse('driver_archive_prices',args=[row.pk])
        data={'revision':archive_revision(row),'revenue':'11000','fuel_price':'55','adblue_price':'20'}
        self.client.force_login(self.driver);self.assertEqual(self.client.post(url,data).status_code,403)
        self.client.force_login(self.admin);self.assertEqual(self.client.post(url,data).status_code,302)
        row.refresh_from_db()
        self.assertEqual(row.source_values['fuel_price'],'50');self.assertEqual(row.source_formulas,['=A1'])
        self.assertEqual(row.report_corrections['fuel_price'],'55')
        self.assertIsNone(history_totals([row])['profit'])
        self.assertEqual(report_insights([row],[])['historical']['margin_count'],0)
        self.assertContains(self.client.get(reverse('driver_report_detail',args=[self.driver.pk])),'Виправлено у звіті')
        self.assertEqual(self.client.post(url,data).status_code,302)
        row.refresh_from_db();self.assertEqual(row.report_corrections['revenue'],'11000')

    def test_inline_defaults_are_suggested_only_for_new_trip(self):
        vehicle=Vehicle.objects.create(plate='DRA003',kind='tent')
        trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Товар',planned_tons=10,planned_date=timezone.localdate(),status='completed',distance_km=100)
        self.client.force_login(self.admin)
        response=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]))
        form=response.context['trips'][0].economics_form
        self.assertEqual(form.initial['daily_rate'],Decimal('1000'))
        self.assertEqual(form.initial['depreciation_per_km'],Decimal('11.70'))
        self.assertNotIn('fuel_price',form.initial)
        self.assertFalse(TripEconomics.objects.filter(trip=trip).exists())
        record=TripEconomics.objects.create(trip=trip,daily_rate=Decimal('1500'))
        response=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]))
        form=response.context['trips'][0].economics_form
        self.assertEqual(form.instance.daily_rate,Decimal('1500'))
        self.assertIsNone(form.initial['credit_per_km'])
        data={f'trip-{trip.pk}-revision':record.updated_at.isoformat(),f'trip-{trip.pk}-daily_rate':'1700','inline':'1'}
        self.assertEqual(self.client.post(reverse('driver_economics_edit',args=[trip.pk]),data).status_code,302)
        record.refresh_from_db();self.assertEqual(record.daily_rate,Decimal('1700'))

    def test_defaults_change_only_new_calculations_and_restrict_access(self):
        vehicle=Vehicle.objects.create(plate='DRA004',kind='tent')
        trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Товар',planned_tons=10,planned_date=timezone.localdate(),status='completed',distance_km=100)
        saved=TripEconomics.objects.create(trip=trip,daily_rate=Decimal('1200'),bonus_per_km=Decimal('2'))
        self.client.force_login(self.driver)
        self.assertEqual(self.client.post(reverse('driver_report_defaults'),{'daily_rate':'1800','bonus_per_km':'4'}).status_code,403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('driver_report_defaults'),{'daily_rate':'1800','bonus_per_km':'4'}).status_code,302)
        saved.refresh_from_db();self.assertEqual(saved.daily_rate,Decimal('1200'))
        self.assertNotIn('daily_rate',economics_initial(trip,saved))
        proposal=economics_initial(trip,None)
        self.assertEqual(proposal['daily_rate'],Decimal('1800'));self.assertEqual(proposal['bonus_per_km'],Decimal('4'))
        self.assertTrue(DriverEconomicsDefaults.objects.filter(pk=1).exists())

    def test_all_manual_defaults_apply_by_vehicle_kind_and_empty_values_stay_empty(self):
        defaults=DriverEconomicsDefaults(daily_rate=Decimal('1400'),days=Decimal('2'),bonus_per_km=Decimal('4'),fuel_price=Decimal('55'),adblue_price=Decimal('30'),adblue_rate=Decimal('1.5'),depreciation_tent=Decimal('12'),depreciation_grain=Decimal('9'),credit_per_km=Decimal('7'),price=None,extra_cost=Decimal('0'))
        tent=Vehicle.objects.create(plate='DRA005',kind='tent')
        grain=Vehicle.objects.create(plate='DRA006',kind='grain')
        def trip(vehicle):
            return Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Товар',planned_tons=10,planned_date=timezone.localdate(),status='completed',distance_km=100)
        tent_trip=trip(tent);grain_trip=trip(grain)
        initial=economics_initial(tent_trip,None,defaults)
        self.assertEqual(initial['depreciation_per_km'],Decimal('12'))
        self.assertEqual(initial['fuel_price'],Decimal('55'))
        self.assertEqual(initial['days'],Decimal('2'))
        self.assertEqual(initial['extra_cost'],Decimal('0'))
        self.assertNotIn('price',initial)
        self.assertEqual(economics_initial(grain_trip,None,defaults)['depreciation_per_km'],Decimal('9'))
        stored=TripEconomics.objects.create(trip=tent_trip,fuel_price=Decimal('44'))
        self.assertNotIn('fuel_price',economics_initial(tent_trip,stored,defaults))

    def test_insights_use_paired_values_and_weighted_ratios(self):
        records=[DriverReportHistory(source_values={'kilometers':100,'revenue':1000,'profit':200},display_values={'extra_columns':[{'column':'AB','value':'30'}]}),DriverReportHistory(source_values={'kilometers':300,'revenue':3000,'profit':-300},display_values={'extra_columns':[{'column':'AB','value':'60'}]}),DriverReportHistory(source_values={'kilometers':50,'profit':None},display_values={})]
        result=report_insights(records,[])['historical']
        self.assertEqual(result['revenue_per_km'],Decimal('10'))
        self.assertEqual(result['margin'],Decimal('-2.5'))
        self.assertEqual(result['fuel_rate'],Decimal('22.5'))
        self.assertEqual((result['loss_count'],result['profit_count'],result['missing_profit']),(1,2,1))

    def test_overview_excludes_archive_without_removing_it(self):
        from datetime import date
        DriverReportHistory.objects.create(source_sheet='A',source_row=1,driver=self.driver,driver_label=self.driver.full_name,trip_date=date(2026,1,1),source_values={'kilometers':10},display_values={})
        DriverReportHistory.objects.create(source_sheet='A',source_row=2,driver=self.driver,driver_label=self.driver.full_name,trip_date=date(2026,2,1),source_values={'kilometers':30},display_values={})
        self.client.force_login(self.admin)
        response=self.client.get(reverse('driver_reports'),{'start':'2026-02-01','end':'2026-02-28'})
        self.assertEqual(response.context['live_total'],0)
        self.assertIsNone(response.context['totals']['kilometers']['value'])
        self.assertEqual(DriverReportHistory.objects.count(),2)
        self.assertNotContains(response,'Історичний пробіг')

    def test_meeting_overview_matches_driver_report_and_marks_missing_values(self):
        vehicle=Vehicle.objects.create(plate='MEET01',kind='tent')
        trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Вантаж',planned_tons=1,planned_date=timezone.localdate(),status='completed',completed_at=timezone.now(),distance_km=100,fuel_used_l=30)
        TripEconomics.objects.create(trip=trip,price=10000,daily_rate=1000,days=1,bonus_per_km=3,fuel_price=50,adblue_price=20,adblue_rate=1,depreciation_per_km=9,credit_per_km=6,extra_cost=100)
        Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='В',destination='Г',cargo='Вантаж',planned_tons=1,planned_date=timezone.localdate(),status='completed',completed_at=timezone.now())
        DriverReportHistory.objects.create(source_sheet='Архів',source_row=900,driver=self.driver,driver_label=self.driver.full_name,source_values={'kilometers':999999})
        self.client.force_login(self.admin)
        overview=self.client.get(reverse('driver_reports'))
        detail=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]))
        row=next(item for item in overview.context['drivers'] if item['driver']==self.driver)
        self.assertEqual(overview.context['live_total'],2)
        for key in ('kilometers','fuel_litres','wage','revenue','profit'):
            self.assertEqual(row['totals'][key],detail.context['period_totals']['live'][key])
            self.assertEqual(row['totals'][key]['missing'],1)
        self.assertEqual(row['totals']['kilometers']['value'],Decimal('100'))
        self.assertEqual(row['totals']['profit']['value'],Decimal('5580'))
        self.assertEqual(overview.context['distance_chart'][0]['value'],Decimal('100'))
        self.assertEqual(overview.context['distance_chart'][0]['missing'],1)
        self.assertEqual(overview.context['distance_chart'][0]['width'],100)
        self.assertEqual(overview.context['insight']['fuel_rate'],Decimal('30'))
        self.assertEqual(overview.context['insight']['fuel_count'],1)
        self.assertEqual(overview.context['fuel_chart'][0]['value'],Decimal('30'))
        self.assertEqual((overview.context['fuel_chart'][0]['known'],overview.context['fuel_chart'][0]['total']),(1,2))
        self.assertEqual(overview.context['trip_chart'][0]['count'],2)
        self.assertContains(overview,'Середня витрата за водіями')
        self.assertContains(overview,'Пробіг за водіями')
        self.assertEqual(overview.context['missing_date'],2)
        self.assertNotContains(overview,'Повнота даних')
        self.assertEqual(overview.context['timeline'],[])
        trip.route_enabled=True;trip.save(update_fields=['route_enabled'])
        TripStop.objects.create(trip=trip,position=1,kind='load',address='А',cargo='Вантаж',planned_tons=1,arrived_at=timezone.now())
        overview=self.client.get(reverse('driver_reports'))
        self.assertEqual(overview.context['timeline'][0]['count'],1)
        self.assertEqual(overview.context['missing_date'],1)
        self.assertContains(overview,'Рейси за місяцями')

    def test_import_is_idempotent_and_changed_source_is_not_overwritten(self):
        row=[1,46328,100,1000,1,3,20,50,30,1,9,6,10000,0,'Місто','Склад','Клієнт','Маршрут',1500,20,1500,1300,4320,22,43.2,.22]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); name='Тестовий (тент)'
            def write(file,data): (root/file).write_text(json.dumps(data,ensure_ascii=False),encoding='utf8')
            write('data.json',{'spreadsheet':'1Y420d-f6QSaQdzGYKmoBE1BSQXe1MXWf5ddDxSZzVdc','sheets':[{'name':name,'values':[row]}]})
            write('formulas.json',{'sheets':[{'name':name,'values':[[]]}]});write('display.json',{'sheets':[{'name':name,'values':[row]}]})
            args=[str(root/'data.json'),str(root/'formulas.json'),str(root/'display.json')]
            call_command('import_driver_reports',*args,apply=True,verbosity=0)
            call_command('import_driver_reports',*args,apply=True,verbosity=0)
            self.assertEqual(DriverReportHistory.objects.count(),1)
            row[12]=1;write('data.json',{'spreadsheet':'1Y420d-f6QSaQdzGYKmoBE1BSQXe1MXWf5ddDxSZzVdc','sheets':[{'name':name,'values':[row]}]})
            with self.assertRaises(CommandError): call_command('import_driver_reports',*args,apply=True,verbosity=0)
            self.assertEqual(DriverReportHistory.objects.get().source_values['revenue'],10000)
