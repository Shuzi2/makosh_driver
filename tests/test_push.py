import base64
import json
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from fleet.models import User, Vehicle, Trip


@override_settings(SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False,CSRF_COOKIE_SECURE=False,
                   WEBPUSH_PUBLIC_KEY='test-public',WEBPUSH_PRIVATE_KEY='test-private',WEBPUSH_SUBJECT='mailto:test@example.com',
                   STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class DriverPushTests(TestCase):
    def setUp(self):
        self.driver=User.objects.create_user('+380501010101',role='driver',full_name='Водій',must_change_password=False)
        self.other=User.objects.create_user('+380501010102',role='driver',full_name='Інший',must_change_password=False)
        self.staff=User.objects.create_user('+380501010103',role='admin',full_name='Логіст',must_change_password=False)
        self.vehicle=Vehicle.objects.create(plate='PUSH123',kind='tent')
        self.trip=Trip.objects.create(vehicle=self.vehicle,driver=self.driver,created_by=self.staff,origin='А',destination='Б',cargo='Вантаж',planned_tons=1,planned_date=timezone.localdate())
        self.client.force_login(self.driver)
        self.subscription={'endpoint':'https://fcm.googleapis.com/fcm/send/test','keys':{'p256dh':base64.urlsafe_b64encode(b'\x04'+b'\x01'*64).decode().rstrip('='),'auth':base64.urlsafe_b64encode(b'\x02'*16).decode().rstrip('=')}}

    def subscribe(self,payload=None):
        return self.client.post(reverse('driver_push_subscribe'),data=json.dumps(payload or self.subscription),content_type='application/json')

    def test_subscribe_requires_driver_and_rejects_untrusted_endpoints(self):
        self.assertEqual(self.subscribe().status_code,200)
        from fleet.models import PushSubscription
        self.assertEqual(PushSubscription.objects.filter(user=self.driver).count(),1)
        bad={**self.subscription,'endpoint':'https://127.0.0.1/private'}
        self.assertEqual(self.subscribe(bad).status_code,400)
        self.client.force_login(self.staff)
        self.assertEqual(self.subscribe().status_code,403)

    def test_assignment_queues_only_recipient_and_delivers_generic_payload(self):
        self.subscribe()
        from fleet.driver_notifications import notify_assignment
        from fleet.models import PushDelivery
        notify_assignment(self.trip)
        delivery=PushDelivery.objects.get()
        self.assertEqual(delivery.subscription.user,self.driver)
        from fleet.push import process_deliveries
        with patch('fleet.push.webpush') as sender:
            process_deliveries()
        sender.assert_called_once()
        self.assertEqual(PushDelivery.objects.get().status,'sent')
        payload=json.loads(sender.call_args.kwargs['data'])
        self.assertIn(f'№{self.trip.pk}',payload['title'])
        self.assertIn('прийміть',payload['body'].lower())
        self.assertEqual(payload['url'],reverse('dashboard'))
        self.assertNotIn(self.driver.full_name,sender.call_args.kwargs['data'])
        self.assertNotIn(self.vehicle.plate,sender.call_args.kwargs['data'])

    def test_changes_and_service_reply_queue_for_the_driver(self):
        self.subscribe()
        from fleet.push import queue_trip_change,queue_service_reply
        from fleet.models import PushDelivery
        queue_trip_change(self.trip)
        queue_service_reply(self.driver.pk)
        self.assertEqual(PushDelivery.objects.count(),2)

    def test_subscription_cannot_be_removed_by_another_driver(self):
        self.subscribe()
        self.client.force_login(self.other)
        self.client.post(reverse('driver_push_unsubscribe'),data=json.dumps({'endpoint':self.subscription['endpoint']}),content_type='application/json')
        from fleet.models import PushSubscription
        self.assertTrue(PushSubscription.objects.filter(user=self.driver).exists())

    def test_service_status_and_response_create_push_without_duplicates(self):
        from fleet.models import DriverServiceRequest, PushDelivery
        self.subscribe()
        item=DriverServiceRequest.objects.create(driver=self.driver,vehicle=self.vehicle,tractor_plate=self.vehicle.plate,title='Пічка')
        self.client.force_login(self.staff)
        def update(status,note):
            item.refresh_from_db()
            return self.client.post(reverse('manager_service_update',args=[item.pk]),{'revision':item.updated_at.isoformat(),'status':status,'manager_note':note})
        self.assertEqual(update('in_progress','').status_code,302)
        self.assertEqual(update('in_progress','Замовлено').status_code,302)
        self.assertEqual(update('in_progress','Замовлено').status_code,302)
        self.assertEqual(PushDelivery.objects.filter(kind='service').count(),2)

    def test_reassigned_subscription_does_not_receive_old_queued_message(self):
        self.subscribe()
        from fleet.driver_notifications import notify_assignment
        from fleet.push import process_deliveries
        from fleet.models import PushDelivery
        notify_assignment(self.trip)
        self.client.force_login(self.other)
        self.subscribe()
        with patch('fleet.push.webpush') as sender:
            process_deliveries()
        sender.assert_not_called()
        self.assertEqual(PushDelivery.objects.get().status,'discarded')

    def test_trip_reassignment_discards_old_notice(self):
        self.subscribe()
        from fleet.driver_notifications import notify_assignment
        from fleet.push import process_deliveries
        from fleet.models import PushDelivery
        notify_assignment(self.trip)
        self.trip.driver=self.other
        self.trip.save(update_fields=['driver'])
        with patch('fleet.push.webpush') as sender:
            process_deliveries()
        sender.assert_not_called()
        self.assertEqual(PushDelivery.objects.get().status,'discarded')

    def test_real_vapid_key_signs_and_encrypts_payload(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        from django.test import override_settings
        from fleet.driver_notifications import notify_assignment
        from fleet.push import process_deliveries
        from fleet.models import PushDelivery
        import requests
        encode=lambda value: base64.urlsafe_b64encode(value).decode().rstrip('=')
        vapid=ec.generate_private_key(ec.SECP256R1())
        browser=ec.generate_private_key(ec.SECP256R1())
        subscription={**self.subscription,'keys':{'p256dh':encode(browser.public_key().public_bytes(Encoding.X962,PublicFormat.UncompressedPoint)), 'auth':encode(b'0123456789abcdef')}}
        self.subscribe(subscription)
        private=encode(vapid.private_numbers().private_value.to_bytes(32,'big'))
        response=requests.Response();response.status_code=201
        with override_settings(WEBPUSH_PRIVATE_KEY=private), patch('fleet.push.PushSession.post',return_value=response) as send:
            notify_assignment(self.trip)
            process_deliveries()
        send.assert_called_once()
        self.assertEqual(PushDelivery.objects.get().status,'sent')
