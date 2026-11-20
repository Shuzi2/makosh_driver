"""Private, durable web-push delivery. Payloads deliberately omit trip details."""
import base64
import binascii
import json
import logging
from datetime import timedelta
from urllib.parse import urlsplit
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from pywebpush import webpush, WebPushException
import requests
from .models import PushSubscription, PushDelivery

logger=logging.getLogger(__name__)
ALLOWED_HOSTS={'fcm.googleapis.com','updates.push.services.mozilla.com'}

class PushSession(requests.Session):
    def post(self, url, **kwargs):
        kwargs['allow_redirects'] = False
        return super().post(url, **kwargs)

def configured():
    return bool(settings.WEBPUSH_PUBLIC_KEY and settings.WEBPUSH_PRIVATE_KEY and settings.WEBPUSH_SUBJECT)

def valid_endpoint(value):
    try:
        url=urlsplit(value)
        host=(url.hostname or '').lower()
        return (len(value)<=2048 and url.scheme=='https' and not url.username and not url.password
                and url.port in (None,443) and not url.fragment and
                (host in ALLOWED_HOSTS or host.endswith('.push.apple.com') or host.endswith('.notify.windows.com')))
    except (TypeError,ValueError):
        return False

def valid_key(value,length):
    try:
        if not isinstance(value,str) or not value or len(value)>128: return False
        raw=base64.b64decode(value+'='*(-len(value)%4),altchars=b'-_',validate=True)
        return len(raw)==length and (length!=65 or raw[0]==4)
    except (ValueError,binascii.Error):
        return False

def driver_only(request):
    if request.user.role!='driver' or not request.user.is_active: raise PermissionDenied

@login_required
@require_GET
def configuration(request):
    driver_only(request)
    response=JsonResponse({'enabled':configured(),'publicKey':settings.WEBPUSH_PUBLIC_KEY if configured() else ''})
    response['Cache-Control']='private, no-store'
    return response

@login_required
@require_POST
def subscribe(request):
    driver_only(request)
    if not configured(): return JsonResponse({'error':'Сповіщення ще не налаштовані на сервері.'},status=503)
    if len(request.body)>8192: return JsonResponse({'error':'Invalid subscription'},status=400)
    try:
        data=json.loads(request.body)
        endpoint=data['endpoint'];keys=data['keys'];p256dh=keys['p256dh'];auth=keys['auth']
    except (ValueError,KeyError,TypeError):
        return JsonResponse({'error':'Invalid subscription'},status=400)
    if not isinstance(endpoint,str) or not valid_endpoint(endpoint) or not valid_key(p256dh,65) or not valid_key(auth,16):
        return JsonResponse({'error':'Invalid subscription'},status=400)
    PushSubscription.objects.update_or_create(endpoint=endpoint,defaults={'user':request.user,'p256dh':p256dh,'auth':auth})
    return JsonResponse({'ok':True})

@login_required
@require_POST
def unsubscribe(request):
    driver_only(request)
    try: endpoint=json.loads(request.body).get('endpoint','') if len(request.body)<=8192 else ''
    except (ValueError,AttributeError): endpoint=''
    if not isinstance(endpoint,str): return JsonResponse({'error':'Invalid subscription'},status=400)
    PushSubscription.objects.filter(user=request.user,endpoint=endpoint).delete()
    return JsonResponse({'ok':True})

@require_GET
def service_worker(request):
    from pathlib import Path
    body=(Path(settings.BASE_DIR)/'static'/'driver_push_worker.js').read_text(encoding='utf-8')
    response=HttpResponse(body,content_type='application/javascript; charset=utf-8')
    response['Cache-Control']='no-cache'
    return response

@require_GET
def manifest(request):
    response=JsonResponse({'id':'/','name':'M TRANS','short_name':'M TRANS','start_url':'/','scope':'/',
                           'display':'standalone','background_color':'#eef0f4','theme_color':'#20242a',
                           'icons':[{'src':static(f'brand/push-icon-{size}.png'),'sizes':f'{size}x{size}','type':'image/png'} for size in (192,512)]})
    response['Content-Type']='application/manifest+json'
    return response

def enqueue(user_id,kind,trip=None):
    if not configured(): return
    subscriptions=PushSubscription.objects.filter(user_id=user_id,user__is_active=True,user__role='driver')
    PushDelivery.objects.bulk_create([PushDelivery(subscription=sub,recipient_id=user_id,kind=kind,trip=trip) for sub in subscriptions])

def queue_trip_change(trip):
    if trip.status in trip.ACTIVE+['queued']:
        enqueue(trip.driver_id,'trip_change',trip)

def queue_service_reply(user_id):
    enqueue(user_id,'service')

def process_deliveries(limit=30):
    if not configured(): return
    now=timezone.now()
    PushDelivery.objects.filter(status='processing',claimed_at__lt=now-timedelta(minutes=10)).update(status='pending')
    for _ in range(limit):
        with transaction.atomic():
            job=(PushDelivery.objects.select_for_update(skip_locked=True,of=('self',))
                 .filter(status='pending',next_attempt_at__lte=timezone.now()).select_related('subscription','recipient','trip').first())
            if not job: break
            job.status='processing';job.claimed_at=timezone.now();job.attempts+=1
            job.save(update_fields=['status','claimed_at','attempts'])
        sub=job.subscription
        if (job.created_at < timezone.now()-timedelta(hours=24) or sub.user_id!=job.recipient_id
            or not job.recipient.is_active or job.recipient.role!='driver'
            or (job.trip_id and job.trip.driver_id!=job.recipient_id) or not valid_endpoint(sub.endpoint)):
            PushDelivery.objects.filter(pk=job.pk).update(status='discarded');continue
        labels={'assignment':('Новий рейс','Відкрийте кабінет, перегляньте завдання та прийміть рейс.'),
                'trip_change':('Зміни в рейсі','Логіст оновив рейс. Перевірте подробиці в кабінеті.'),
                'cancelled':('Рейс скасовано','Логіст скасував рейс. Перегляньте повідомлення в кабінеті.'),
                'service':('Оновлення сервісу','Логіст оновив ваше звернення. Відкрийте сервіс, щоб переглянути.')}
        title,body=labels.get(job.kind,('Повідомлення','Відкрийте кабінет водія.'))
        if job.kind == 'assignment' and job.trip_id:
            title=f'Новий рейс №{job.trip_id}'
        target=reverse('driver_service') if job.kind=='service' else reverse('dashboard')
        try:
            webpush(subscription_info={'endpoint':sub.endpoint,'keys':{'p256dh':sub.p256dh,'auth':sub.auth}},
                    data=json.dumps({'title':'M TRANS · '+title,'body':body,'url':target},ensure_ascii=False),
                    vapid_private_key=settings.WEBPUSH_PRIVATE_KEY,vapid_claims={'sub':settings.WEBPUSH_SUBJECT},
                    timeout=10,ttl=86400,requests_session=PushSession())
            PushDelivery.objects.filter(pk=job.pk).update(status='sent')
        except WebPushException as exc:
            status=exc.response.status_code if exc.response is not None else None
            if status in (404,410):
                sub.delete()
            else:
                retry(job)
        except Exception:
            logger.warning('Push delivery failed; retry scheduled',exc_info=False)
            retry(job)

def retry(job):
    state='failed' if job.attempts>=6 else 'pending'
    delay=min(3600,30*(2**(job.attempts-1)))
    PushDelivery.objects.filter(pk=job.pk).update(status=state,next_attempt_at=timezone.now()+timedelta(seconds=delay))
