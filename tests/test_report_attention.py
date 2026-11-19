from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from fleet.attention import issue_snapshot, filter_attention
from fleet.models import User, Vehicle, Trip, TripEconomics, DriverEconomicsDefaults


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES={"default":{"BACKEND":"django.core.files.storage.FileSystemStorage"},"staticfiles":{"BACKEND":"django.contrib.staticfiles.storage.StaticFilesStorage"}})
class ReportAttentionTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user('+380501234567', 'password', full_name='Логіст', role='admin', must_change_password=False)
        self.driver = User.objects.create_user('+380501234568', 'password', full_name='Водій')
        self.vehicle = Vehicle.objects.create(plate='TEST123', kind='tent', default_driver=self.driver)
        self.trip = Trip.objects.create(vehicle=self.vehicle, driver=self.driver, created_by=self.manager, origin='А', destination='Б', cargo='Вантаж', planned_tons=10, planned_date=timezone.localdate(), status='completed', completed_at=timezone.now(), distance_km=100, fuel_used_l=30)

    def report_issues(self):
        return [i for i in issue_snapshot(Trip.objects.filter(pk=self.trip.pk)) if i['key']=='report']

    def test_missing_fields_link_and_completion(self):
        self.assertIn('Ціна рейсу', self.report_issues()[0]['missing_report'])
        self.client.force_login(self.manager)
        response=self.client.get(reverse('attention'), {'category':'report'})
        self.assertContains(response, reverse('driver_report_detail', args=[self.driver.pk]))
        self.assertContains(response, 'Ціна рейсу')
        values={name:0 for name in ['price','daily_rate','days','bonus_per_km','fuel_price','adblue_price','adblue_rate','depreciation_per_km','credit_per_km','extra_cost']}
        economics=TripEconomics.objects.create(trip=self.trip,**values)
        self.assertFalse(self.report_issues())
        self.assertFalse(filter_attention(Trip.objects.filter(pk=self.trip.pk),'report').exists())
        economics.fuel_price=None; economics.save()
        self.assertEqual(self.report_issues()[0]['missing_report'],['Ціна пального'])

    def test_default_values_count_only_before_economics_saved(self):
        DriverEconomicsDefaults.objects.create(id=1, price=0,daily_rate=0,days=0,bonus_per_km=0,fuel_price=0,adblue_price=0,adblue_rate=0,depreciation_tent=0,credit_per_km=0,extra_cost=0)
        self.assertFalse(self.report_issues())
        self.assertFalse(filter_attention(Trip.objects.filter(pk=self.trip.pk),'report').exists())
        TripEconomics.objects.create(trip=self.trip, price=0)
        self.assertIn('Ціна пального',self.report_issues()[0]['missing_report'])

    def test_completion_rates_remain_fixed_when_defaults_change(self):
        from fleet.driver_reports import snapshot_completion_rates
        defaults=DriverEconomicsDefaults.objects.create(id=1, fuel_price=50, bonus_per_km=3)
        snapshot_completion_rates(self.trip)
        defaults.fuel_price=55;defaults.bonus_per_km=4;defaults.save()
        saved=TripEconomics.objects.get(trip=self.trip)
        self.assertEqual(saved.fuel_price,50)
        self.assertEqual(saved.bonus_per_km,3)
        snapshot_completion_rates(self.trip)
        saved.refresh_from_db();self.assertEqual(saved.fuel_price,50)

    def test_old_unsaved_trip_keeps_previous_visible_values_when_defaults_change(self):
        defaults=DriverEconomicsDefaults.objects.create(id=1,fuel_price=50,bonus_per_km=3)
        data={field.name:(getattr(defaults,field.name) if getattr(defaults,field.name) is not None else '') for field in defaults._meta.fields if field.name not in ('id','updated_at')}
        data['fuel_price']='55'
        self.client.force_login(self.manager)
        response=self.client.post(reverse('driver_report_defaults'),data)
        self.assertEqual(response.status_code,302)
        self.assertEqual(TripEconomics.objects.get(trip=self.trip).fuel_price,50)
        defaults.refresh_from_db();self.assertEqual(defaults.fuel_price,55)


