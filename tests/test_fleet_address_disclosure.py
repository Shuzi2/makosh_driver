from django.template.loader import render_to_string
from django.test import SimpleTestCase


class FleetAddressDisclosureTests(SimpleTestCase):
    def test_place_stays_visible_and_address_starts_collapsed(self):
        html = render_to_string('fleet/fleet_job_place.html', {
            'operation': 'load', 'operation_label': 'Завантаження', 'trip_id': 39,
            'place': {'name': 'с. Білокриниця', 'has_area': True, 'region': 'Тернопільська область', 'detail': 'вул. Шевченка, 2'},
        })
        self.assertIn('<b>с. Білокриниця</b>', html)
        self.assertIn('<details class="fleet-address-details" data-trip-id="39" data-address-key="load">', html)
        self.assertIn('вул. Шевченка, 2', html)

    def test_place_without_address_has_no_empty_disclosure(self):
        html = render_to_string('fleet/fleet_job_place.html', {'operation': 'unload', 'place': {'name': 'Гребінка'}})
        self.assertNotIn('fleet-address-details', html)
