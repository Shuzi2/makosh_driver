from django.test import TestCase,override_settings
from fleet.models import User
from fleet.settlements import catalogue,normalize


@override_settings(SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False,CSRF_COOKIE_SECURE=False,STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class SettlementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin=User.objects.create_user('+380508100001',role='admin',full_name='Логіст',must_change_password=False)
        cls.driver=User.objects.create_user('+380508100002',role='driver',full_name='Водій',must_change_password=False)

    def search(self,query):
        self.client.force_login(self.admin)
        return self.client.get('/settlements/search/',{'q':query}).json()

    def test_source_hierarchy_and_special_city_are_preserved(self):
        records=[item for item,name,text in catalogue()]
        self.assertEqual(len(records),29703)
        self.assertEqual(len({r['code'] for r in records}),29703)
        self.assertTrue(all(r['region'] for r in records))
        self.assertLessEqual(max(len(r['address']) for r in records),250)
        vinnytsia=next(r for r in records if r['name']=='Вінниця')
        self.assertEqual(vinnytsia['region'],'Вінницька область')
        self.assertEqual(vinnytsia['district'],'Вінницький район')
        self.assertEqual(vinnytsia['community'],'Вінницька громада')
        kyiv=next(r for r in records if r['code']=='UA80000000000093317')
        self.assertEqual(kyiv['address'],'м. Київ')
        self.assertEqual(kyiv['district'],'')
        self.assertEqual(kyiv['community'],'')

    def test_search_handles_case_apostrophes_and_disambiguates_names(self):
        found=self.search('ВІННИЦЯ')['results']
        self.assertEqual(found[0]['name'],'Вінниця')
        found=self.search('Андріївка Вінницька')['results']
        self.assertTrue(found)
        self.assertTrue(all('Вінницька' in r['address'] for r in found))
        self.assertEqual(normalize("Кам’янець"),normalize("Кам'янець"))
        self.assertEqual(normalize("Кам’янець"),normalize('Камянець'))
        self.assertLessEqual(len(self.search('андріївка')['results']),20)
        self.assertTrue(self.search('андріївка')['more'])

    def test_driver_can_search_settlements_for_acceptance_without_staff_access(self):
        self.assertEqual(self.client.get('/driver/settlements/search/',{'q':'Вінниця'}).status_code,302)
        self.client.force_login(self.driver)
        response=self.client.get('/driver/settlements/search/',{'q':'Вінниця'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['results'][0]['name'],'Вінниця')
        self.assertIn('Вінницька область',response.json()['results'][0]['address'])
        self.assertEqual(self.client.get('/settlements/search/',{'q':'Вінниця'}).status_code,403)

    def test_search_is_staff_only_and_unknown_address_can_be_manual(self):
        self.assertEqual(self.client.get('/settlements/search/',{'q':'Вінниця'}).status_code,302)
        self.client.force_login(self.driver)
        self.assertEqual(self.client.get('/settlements/search/',{'q':'Вінниця'}).status_code,403)
        self.assertEqual(self.search('Цьогонаселеногопунктунемає123')['results'],[])
        self.assertEqual(self.search('а')['results'],[])
        from fleet.route_forms import StopForm
        form=StopForm(data={'kind':'load','address':'Моє село, Вінницька область, склад №2','cargo':'Добрива','planned_tons':'24','region':'Вінницька область','district':'Вінницький район','community':'Місцева громада'})
        self.assertTrue(form.is_valid(),form.errors)
        self.assertEqual(form.cleaned_data['address'],'Моє село, Вінницька область, склад №2')

    def test_new_manual_stop_requires_all_administrative_fields(self):
        from fleet.route_forms import StopForm
        form=StopForm(data={'kind':'load','address':'Невідоме село для тесту','cargo':'Добрива','planned_tons':'24'})
        self.assertFalse(form.is_valid())
        for name in ['region','district','community']: self.assertIn(name,form.errors)
        city=StopForm(data={'kind':'load','address':'м. Київ','cargo':'Добрива','planned_tons':'24'})
        self.assertTrue(city.is_valid(),city.errors)
        self.assertEqual(city.cleaned_data['region'],'м. Київ')
        self.assertEqual(city.cleaned_data['community'],'')

    def test_selected_place_is_saved_as_a_structured_snapshot(self):
        from fleet.route_forms import StopForm
        source=self.search('Вінниця')['results'][0]
        form=StopForm(data={'kind':'load','address':source['address']+', вул. Польова, 2','settlement_code':source['code'],'cargo':'Добрива','planned_tons':'24','region':'Підміна області'})
        self.assertTrue(form.is_valid(),form.errors)
        stop=form.save(commit=False)
        self.assertEqual(stop.region,'Вінницька область')
        self.assertEqual(stop.driver_place['name'],'м. Вінниця')
        self.assertEqual(stop.driver_place['detail'],'вул. Польова, 2')
