from django.test import Client, TestCase, override_settings
from django.urls import reverse
from fleet.models import AuditEvent, DriverPermissionSettings, User


@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class DriverPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin=User.objects.create_user('+380501001001', password='Test-pass-1234', role='admin', full_name='Admin', must_change_password=False)
        cls.driver=User.objects.create_user('+380501001002', password='Test-pass-1234', role='driver', full_name='Driver', must_change_password=False)

    def setUp(self):
        self.client.force_login(self.driver)

    def test_existing_driver_defaults_preserve_access(self):
        self.assertEqual(self.client.get(reverse('dashboard')).status_code,200)
        settings=DriverPermissionSettings.objects.get()
        self.assertTrue(settings.view_trips and settings.change_stages and settings.upload_documents)
        self.assertTrue(settings.report_delays and settings.manage_expenses)

    def test_new_driver_operations_can_be_disabled(self):
        DriverPermissionSettings.objects.create(view_trips=True,report_delays=False,manage_expenses=False)
        for name in ['driver_delay','driver_expense','expense_receipt']:
            self.assertEqual(self.client.get(reverse(name,args=[1])).status_code,403)

    def test_revoked_access_is_enforced_on_direct_requests(self):
        DriverPermissionSettings.objects.create(view_trips=False,change_stages=False,upload_documents=False)
        for name,args,method in [('dashboard',[], 'get'),('trip_list',[],'get'),('driver_documents',[],'get'),('document_download',[1],'get'),('trip_accept',[1],'post'),('driver_action',[1],'post'),('document_upload',[1],'post')]:
            self.assertEqual(getattr(self.client,method)(reverse(name,args=args)).status_code,403,name)
        self.assertEqual(self.client.get(reverse('password_change')).status_code,200)

    def test_only_admin_can_update_permissions_and_change_is_audited(self):
        url=reverse('admin_permission_update')
        self.assertEqual(self.client.post(url,{}).status_code,403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url).status_code,405)
        secured=Client(enforce_csrf_checks=True);secured.force_login(self.admin)
        self.assertEqual(secured.post(url,{}).status_code,403)
        self.assertEqual(self.client.post(url,{'view_trips':'on','upload_documents':'on'}).status_code,302)
        settings=DriverPermissionSettings.objects.get()
        self.assertFalse(settings.change_stages)
        self.assertTrue(AuditEvent.objects.filter(action='Змінено права ролі водія').exists())
        self.assertEqual(self.client.get(reverse('dashboard')).status_code,200)

    def test_trip_read_is_prerequisite_for_documents_and_actions(self):
        DriverPermissionSettings.objects.create(view_trips=False,change_stages=True,upload_documents=True)
        for name,args,method in [('driver_documents',[],'get'),('document_preview',[1],'get'),('driver_action',[1],'post')]:
            self.assertEqual(getattr(self.client,method)(reverse(name,args=args)).status_code,403,name)

    def test_roles_page_reflects_saved_driver_permissions(self):
        DriverPermissionSettings.objects.create(view_trips=True, change_stages=False, upload_documents=True)
        self.client.force_login(self.admin)
        response = self.client.get(reverse('admin_roles'))
        self.assertContains(response, 'name="view_trips" checked', html=False)
        self.assertContains(response, 'name="change_stages"  aria-label=', html=False)
        self.assertContains(response, 'name="upload_documents" checked', html=False)
