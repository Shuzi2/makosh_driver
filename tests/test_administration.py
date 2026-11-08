from django.apps import apps
from django.test import TestCase, Client, override_settings
from fleet.models import User, AuditEvent


@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False,
                   STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                             "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class AdministrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user('+380501234001', 'Testing-Password-2026!', full_name='Адміністратор', role='admin', must_change_password=False)
        cls.staff = User.objects.create_user('+380501234002', 'Testing-Password-2026!', full_name='Логіст', role='logistic', must_change_password=False)
        cls.driver = User.objects.create_user('+380501234003', 'Testing-Password-2026!', full_name='Водій', role='driver', must_change_password=False)
        cls.manager = User.objects.create_user('+380501234004', 'Testing-Password-2026!', full_name='Керівник', role='manager', must_change_password=False)

    def test_admin_hub_groups_management_links(self):
        self.client.force_login(self.admin)
        response = self.client.get('/administration/')
        self.assertEqual(response.status_code, 200)
        for url in ['/accounts/', '/password/', '/administration/places/', '/administration/activity/']:
            self.assertContains(response, 'href="' + url + '"')
        home = self.client.get('/')
        self.assertContains(home, 'Адмін панель')
        nav = home.content.decode().split('<nav>')[1].split('</nav>')[0]
        self.assertNotIn('/accounts/', nav)
        self.assertNotIn('/password/', nav)

    def test_nonadmins_cannot_read_or_write_administration(self):
        self.assertEqual(self.client.get('/administration/').status_code, 302)
        for user in [self.staff, self.driver, self.manager]:
            self.client.force_login(user)
            for url in ['/administration/', '/administration/places/', '/administration/places/new/', '/administration/activity/']:
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url, {'name': 'Forbidden', 'address': 'Forbidden'}).status_code, 403)
            home = self.client.get('/')
            self.assertNotContains(home, 'Адмін панель')
            self.assertContains(home, 'href="/password/"')

    def test_saved_place_create_edit_archive_and_trip_suggestions(self):
        self.client.force_login(self.admin)
        data = {'name': 'Склад Любомль', 'address': 'Любомль, Складська 1', 'notes': 'Вхід №2', 'is_active': 'on'}
        response = self.client.post('/administration/places/new/', data)
        self.assertEqual(response.status_code, 302)
        place = apps.get_model('fleet', 'SavedPlace').objects.get()
        self.client.force_login(self.staff)
        self.assertContains(self.client.get('/trips/new/'), '<option value="Любомль, Складська 1">Склад Любомль</option>', html=True)
        self.assertEqual(self.client.post(f'/administration/places/{place.pk}/edit/', data).status_code, 403)
        self.client.force_login(self.admin)
        data.update(address='Любомль, Складська 2')
        self.assertEqual(self.client.post(f'/administration/places/{place.pk}/edit/', data).status_code, 302)
        place.refresh_from_db()
        self.assertEqual(place.address, data['address'])
        data.pop('is_active')
        self.client.post(f'/administration/places/{place.pk}/edit/', data)
        place.refresh_from_db()
        self.assertFalse(place.is_active)
        self.client.force_login(self.staff)
        self.assertNotContains(self.client.get('/trips/new/'), '<option value="Любомль, Складська 2">')
        self.assertTrue(AuditEvent.objects.filter(action='Додано збережене місце').exists())
        self.assertTrue(AuditEvent.objects.filter(action='Оновлено збережене місце').exists())

    def test_place_validation_and_escape(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/administration/places/').status_code, 200)
        model = apps.get_model('fleet', 'SavedPlace')
        response = self.client.post('/administration/places/new/', {'name': '', 'address': ''})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(model.objects.count(), 0)
        data = {'name': '<script>bad()</script>', 'address': 'X' * 181}
        self.client.post('/administration/places/new/', data)
        self.assertEqual(model.objects.count(), 0)
        data['address'] = 'Вінниця'
        self.client.post('/administration/places/new/', data)
        response = self.client.get('/administration/places/')
        self.assertContains(response, '&lt;script&gt;bad()&lt;/script&gt;')
        self.assertNotContains(response, '<script>bad()</script>')
        self.client.post('/administration/places/new/', data)
        self.assertEqual(model.objects.count(), 1)

    def test_saved_places_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post('/administration/places/new/', {'name': 'X', 'address': 'Y'}).status_code, 403)

    def test_activity_is_readonly_and_recent_first(self):
        self.client.force_login(self.admin)
        AuditEvent.objects.create(actor=self.admin, action='Тестова дія', detail='<unsafe>')
        response = self.client.get('/administration/activity/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Тестова дія')
        self.assertContains(response, '&lt;unsafe&gt;')
        self.assertEqual(self.client.post('/administration/activity/').status_code, 405)
