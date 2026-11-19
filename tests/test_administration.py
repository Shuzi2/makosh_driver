from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from fleet.models import AuditEvent, OperationJob, OperationsSettings, Trip, User, Vehicle


@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class AdministrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user('+380501234561', 'Test-Password-986!', role='admin', full_name='Логіст', must_change_password=False)
        cls.driver = User.objects.create_user('+380501234562', 'Test-Password-986!', full_name='Водій', must_change_password=False)
        cls.superuser = User.objects.create_superuser('+380501234563', 'Test-Password-986!', full_name='Суперкористувач')

    def setUp(self):
        self.client.force_login(self.admin)

    def test_resource_metrics_endpoint_is_admin_only_and_not_cached(self):
        url=reverse('admin_metrics_data')
        self.client.force_login(self.driver)
        self.assertEqual(self.client.get(url).status_code,403)
        self.client.force_login(self.admin)
        with patch('fleet.administration.operations.resource_metrics',return_value={'rows':[],'scope':'Docker VM'}):
            response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['scope'],'Docker VM')
        self.assertIsNotNone(timezone.datetime.fromisoformat(response.json()['sampled_at']))
        self.assertIn('no-store',response['Cache-Control'])

    def test_read_pages_are_admin_only(self):
        for name in ['admin_overview', 'admin_journal', 'admin_roles', 'admin_maintenance']:
            self.client.logout()
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)
            self.client.force_login(self.driver)
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)
        self.client.force_login(self.admin)
        with patch('fleet.administration.operations.resource_metrics', return_value={'rows': [], 'scope': 'Docker'}):
            for name in ['admin_overview', 'admin_journal', 'admin_roles', 'admin_maintenance']:
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_mutations_require_post_and_csrf(self):
        urls = [reverse('admin_role_update', args=[self.driver.pk]), reverse('admin_backup_now'), reverse('admin_cleanup_now')]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 405)
        secured = Client(enforce_csrf_checks=True)
        secured.force_login(self.admin)
        for url in urls + [reverse('admin_maintenance')]:
            self.assertEqual(secured.post(url, {'role': 'admin', 'confirm': 'yes'}).status_code, 403)
        self.client.force_login(self.driver)
        for url in urls + [reverse('admin_maintenance')]:
            self.assertEqual(self.client.post(url, {'role': 'admin', 'confirm': 'yes'}).status_code, 403)

    def test_role_change_is_audited_and_guards_own_and_superuser(self):
        self.client.post(reverse('admin_role_update', args=[self.driver.pk]), {'role': 'admin'})
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.role, 'admin')
        self.assertTrue(AuditEvent.objects.filter(actor=self.admin, action='Змінено роль користувача').exists())
        for account in [self.admin, self.superuser]:
            self.client.post(reverse('admin_role_update', args=[account.pk]), {'role': 'driver'})
            account.refresh_from_db()
            self.assertEqual(account.role, 'admin')

    def test_role_rejects_unknown_value_and_assigned_driver(self):
        url = reverse('admin_role_update', args=[self.driver.pk])
        self.client.post(url, {'role': 'owner'})
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.role, 'driver')
        Vehicle.objects.create(plate='ADMIN1', trailer_plate='ADMIN2', kind='tent', default_driver=self.driver)
        self.client.post(url, {'role': 'admin'})
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.role, 'driver')

    def test_vehicle_assignment_rechecks_driver_after_form_validation(self):
        from fleet.forms import VehicleForm
        original = VehicleForm.is_valid
        def promote_after_validation(form):
            valid = original(form)
            if valid:
                User.objects.filter(pk=self.driver.pk).update(role='admin')
            return valid
        with patch.object(VehicleForm, 'is_valid', promote_after_validation):
            response = self.client.post(reverse('vehicle_create'), {
                'plate': 'RACE1', 'trailer_plate': 'RACE2', 'kind': 'tent',
                'default_driver': self.driver.pk,
            })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Vehicle.objects.filter(plate='RACE1').exists())

    def test_role_rejects_active_and_queued_trips(self):
        vehicle = Vehicle.objects.create(plate='ADMIN3', trailer_plate='ADMIN4', kind='tent')
        trip = Trip.objects.create(vehicle=vehicle, driver=self.driver, origin='Київ', destination='Львів', cargo='Вантаж', planned_tons=10, planned_date=timezone.localdate(), created_by=self.admin)
        for status in ['planned', 'queued']:
            trip.status = status
            trip.save()
            self.client.post(reverse('admin_role_update', args=[self.driver.pk]), {'role': 'admin'})
            self.driver.refresh_from_db()
            self.assertEqual(self.driver.role, 'driver')

    def test_settings_validate_and_audit(self):
        settings = OperationsSettings.objects.get_or_create(pk=1)[0]
        data = {'backup_enabled': 'on', 'backup_time': '04:15', 'backup_keep': '21', 'cleanup_enabled': 'on', 'cleanup_days': '120'}
        self.assertEqual(self.client.post(reverse('admin_maintenance'), data).status_code, 302)
        settings.refresh_from_db()
        self.assertEqual(settings.backup_keep, 21)
        self.assertEqual(settings.cleanup_days, 120)
        self.assertEqual(settings.backup_time.strftime('%H:%M'), '04:15')
        self.assertTrue(AuditEvent.objects.filter(action='Змінено налаштування обслуговування').exists())
        for field, value in [('cleanup_days', '89'), ('backup_keep', '0'), ('backup_keep', '91'), ('backup_time', '25:00')]:
            self.assertEqual(self.client.post(reverse('admin_maintenance'), {**data, field: value}).status_code, 200)
        settings.refresh_from_db()
        self.assertEqual(settings.cleanup_days, 120)
        self.assertEqual(settings.backup_keep, 21)

    @patch('fleet.administration.operations.queue_job')
    def test_manual_jobs_and_cleanup_confirmation(self, queue):
        self.client.post(reverse('admin_backup_now'))
        queue.assert_called_once_with('backup', user=self.admin)
        queue.reset_mock()
        self.client.post(reverse('admin_cleanup_now'))
        queue.assert_not_called()
        self.client.post(reverse('admin_cleanup_now'), {'confirm': 'yes'})
        queue.assert_called_once_with('cleanup', user=self.admin)

    def test_journal_search_and_pagination_preserve_filters(self):
        AuditEvent.objects.bulk_create([AuditEvent(actor=self.admin, action='Змінено роль', detail=str(i)) for i in range(42)])
        AuditEvent.objects.create(actor=self.admin, action='Інша дія')
        response = self.client.get(reverse('admin_journal'), {'q': 'Змінено', 'page': '2'})
        self.assertEqual(len(response.context['page_obj']), 2)
        self.assertNotIn('Інша дія', [entry.action for entry in response.context['page_obj']])
        self.assertEqual(self.client.get(reverse('admin_journal'), {'date_from': 'invalid'}).context['page_obj'].paginator.count, 0)

    def test_missing_backup_is_404_and_download_is_admin_only(self):
        job = OperationJob.objects.create(kind='backup', status='succeeded', artifact='missing')
        url = reverse('admin_backup_download', args=[job.pk])
        with patch('fleet.administration.operations.backup_path', side_effect=ValueError('invalid')):
            self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.driver)
        self.assertEqual(self.client.get(url).status_code, 403)
