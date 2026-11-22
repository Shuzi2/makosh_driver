from decimal import Decimal
from django.test import TestCase, override_settings
from django.urls import reverse
from fleet.models import User, DriverReportHistory, TripEconomics,DriverEconomicsDefaults,Trip,Vehicle,TripExpense
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

    def test_archive_inline_edit_preserves_original_and_hides_stale_results(self):
        from fleet.driver_reports import archive_revision
        original={'daily_rate':'1000','days':'2','bonus_per_km':'3','fuel_price':'50','adblue_price':'20','adblue_rate':'1','depreciation_per_km':'9','credit_per_km':'6','revenue':'10000','extra_cost':'0','profit':'1234','driver_cost':'2300'}
        row=DriverReportHistory.objects.create(source_sheet='Тест',source_row=2,driver=self.driver,driver_label=self.driver.full_name,source_values=original,display_values=original)
        self.client.force_login(self.admin)
        response=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]))
        for key in ('daily_rate','days','bonus_per_km','fuel_price','adblue_price','adblue_rate','depreciation_per_km','credit_per_km','revenue','extra_cost'):
            self.assertContains(response,f'data-field="{key}"')
        prefix=f'archive-{row.pk}'
        result=self.client.post(reverse('driver_archive_prices',args=[row.pk]),{'inline':'1',f'{prefix}-revision':archive_revision(row),f'{prefix}-daily_rate':'1200',f'{prefix}-days':'3'})
        self.assertEqual(result.status_code,302)
        row.refresh_from_db()
        self.assertEqual(row.source_values,original)
        self.assertEqual(row.report_corrections,{'daily_rate':'1200','days':'3'})
        response=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]))
        self.assertIsNone(response.context['period_totals']['archive']['wage']['value'])
        self.assertIsNone(response.context['period_totals']['archive']['profit']['value'])
        self.assertContains(response,'1200')

    def test_period_totals_keep_archive_separate_and_exclude_missing_values(self):
        vehicle=Vehicle.objects.create(plate='SUM001',kind='tent')
        now=timezone.now()
        def completed(days, km, fuel):
            return Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Товар',planned_tons=1,planned_date=timezone.localdate(),status='completed',completed_at=now-timedelta(days=days),distance_km=km,fuel_used_l=fuel)
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
        self.assertEqual(parser.values[27],'189,00')

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

    def test_calculated_columns_are_read_only_and_ignore_old_overrides(self):
        vehicle=Vehicle.objects.create(plate='COR001',kind='tent')
        trip=Trip.objects.create(vehicle=vehicle,driver=self.driver,created_by=self.admin,origin='А',destination='Б',cargo='Товар',planned_tons=10,planned_date=timezone.localdate(),status='completed',distance_km=100,fuel_used_l=Decimal('30'))
        inputs=TripEconomics.objects.create(trip=trip,price=10000,daily_rate=1000,days=1,bonus_per_km=3,fuel_price=50,adblue_price=20,adblue_rate=1,depreciation_per_km=9,credit_per_km=6,extra_cost=0,report_overrides={'fuel':'1'})
        self.assertEqual(finances(trip,inputs)['fuel'],Decimal('1500'))
        self.assertEqual(finances(trip,inputs)['profit'],Decimal('5680'))
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(f'/report/result/live/{trip.pk}/',{'field':'fuel','value':'1'}).status_code,404)
        page=self.client.get(reverse('driver_report_detail',args=[self.driver.pk]))
        self.assertContains(page,'sheet-result-readonly')
        self.assertNotContains(page,'sheet-result-editable')
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
        self.assertContains(self.client.get(reverse('driver_report_detail',args=[self.driver.pk])),'Дані виправлено')
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

    def test_overview_period_filters_driver_and_history(self):
        from datetime import date
        DriverReportHistory.objects.create(source_sheet='A',source_row=1,driver=self.driver,driver_label=self.driver.full_name,trip_date=date(2026,1,1),source_values={'kilometers':10},display_values={})
        DriverReportHistory.objects.create(source_sheet='A',source_row=2,driver=self.driver,driver_label=self.driver.full_name,trip_date=date(2026,2,1),source_values={'kilometers':30},display_values={})
        self.client.force_login(self.admin)
        response=self.client.get(reverse('driver_reports'),{'start':'2026-02-01','end':'2026-02-28'})
        self.assertEqual(response.context['history_total'],1)
        self.assertEqual(response.context['history_sums']['kilometers'],Decimal('30'))

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
