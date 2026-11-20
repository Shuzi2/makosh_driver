"""Receipts and server acknowledgements for repeatable mobile requests."""
import hashlib
import json
import uuid
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_GET
from django.utils.dateparse import parse_datetime
from django.urls import reverse
from .models import DriverRequest, Vehicle, Trip


def locked_receipt(request, trip, kind, payload):
    try: key=uuid.UUID(request.POST.get('request_id',''))
    except (ValueError, TypeError, AttributeError): raise ValidationError('Оновіть сторінку та повторіть дію.')
    fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    receipt,_=DriverRequest.objects.get_or_create(pk=key,defaults={'user':request.user,'trip':trip,'kind':kind,'fingerprint':fingerprint})
    receipt=DriverRequest.objects.select_for_update().get(pk=key)
    if receipt.user_id!=request.user.pk or receipt.trip_id!=(trip.pk if trip else None) or receipt.kind!=kind or receipt.fingerprint!=fingerprint:
        raise ValidationError('Запит змінився. Оновіть сторінку та спробуйте ще раз.')
    return receipt


@login_required
@require_POST
def driver_rest(request):
    from .models import User, AuditEvent
    from django.utils import timezone
    from django.utils.http import url_has_allowed_host_and_scheme
    if request.user.role!='driver': raise PermissionDenied
    payload={key:request.POST.get(key,'') for key in ['rest','expected_revision','return_to']}
    if payload['rest'] not in ['start','end']:
        return JsonResponse({'outcome':'rejected','message':'Оберіть початок або завершення відпочинку.'},status=400)
    try:
        with transaction.atomic():
            driver=User.objects.select_for_update().get(pk=request.user.pk)
            receipt=locked_receipt(request,None,'rest',payload)
            if receipt.result: return JsonResponse(receipt.result)
            try: revision=parse_datetime(payload['expected_revision'])
            except ValueError: revision=None
            if revision!=driver.rest_changed_at:
                result={'outcome':'rejected','message':'Стан відпочинку вже змінився. Оновіть сторінку.','redirect':reverse('dashboard')}
            else:
                starting=payload['rest']=='start'
                if starting != bool(driver.rest_started_at):
                    now=timezone.now()
                    duration=max(0,int((now-driver.rest_started_at).total_seconds()//60)) if driver.rest_started_at else 0
                    driver.rest_started_at=now if starting else None
                    driver.rest_changed_at=now
                    driver.save(update_fields=['rest_started_at','rest_changed_at'])
                    trip=Trip.objects.filter(driver=driver,status__in=Trip.ACTIVE).first()
                    AuditEvent.objects.create(actor=driver,trip=trip,
                        action='Початок відпочинку' if starting else 'Завершення відпочинку',
                        detail=f'{driver.full_name}. Етап рейсу збережено.'+('' if starting else f' Відпочивав {duration} хв.'))
                target=payload['return_to']
                if not target.startswith('/') or target.startswith('//') or not url_has_allowed_host_and_scheme(target,allowed_hosts={request.get_host()}):
                    target=reverse('dashboard')
                result={'outcome':'confirmed','redirect':target,'message':'Сервер підтвердив відпочинок.' if starting else 'Сервер підтвердив продовження роботи.'}
            receipt.result=result;receipt.save(update_fields=['result'])
        return JsonResponse(result)
    except ValidationError as exc:
        return JsonResponse({'outcome':'rejected','message':exc.messages[0]},status=400)


@login_required
@require_POST
def driver_action(request, pk):
    from .views import trips_for, trip_accept, trip_start, trip_status, trip_ack_cancel
    if request.user.role!='driver': raise PermissionDenied
    action=request.POST.get('action')
    from .route_views import stop_action,acknowledge_route
    handlers={'stop':stop_action,'ack_route':acknowledge_route,'accept':trip_accept,'start':trip_start,'status':trip_status,'ack_cancel':trip_ack_cancel}
    if action not in handlers: return JsonResponse({'outcome':'rejected','message':'Невідома дія.'},status=400)
    payload={key:request.POST.get(key,'') for key in ['action','status','actual_tons','confirm_without_signed_ttn','expected_revision','stop_id','stop_mode']}
    if 'acceptance_location' in request.POST: payload['acceptance_location']=request.POST['acceptance_location']
    if 'acceptance_odometer_km' in request.POST: payload['acceptance_odometer_km']=request.POST['acceptance_odometer_km']
    for key in ['distance_km','empty_distance_km','loaded_distance_km','fuel_used_l'] + [key for key in request.POST if key.startswith('unload_weight_')]:
        if key in request.POST: payload[key]=request.POST[key]
    try:
        with transaction.atomic():
            initial=get_object_or_404(trips_for(request.user),pk=pk)
            # Same ordering as starting a queued trip; status changes only lock their trip.
            if action=='start': Vehicle.objects.select_for_update().get(pk=initial.vehicle_id)
            trip=get_object_or_404(trips_for(request.user).select_for_update(),pk=pk)
            receipt=locked_receipt(request,trip,action,payload)
            if receipt.result: return JsonResponse(receipt.result)
            try: revision=parse_datetime(payload['expected_revision'])
            except ValueError: revision=None
            if action=='accept' and trip.status=='queued':
                result={'outcome':'rejected','message':'Це майбутній рейс. Прийняти його можна, коли він стане поточним.','redirect':reverse('trip_detail',args=[pk])}
            elif action=='accept' and not trip.is_accepted and (not payload.get('acceptance_location','').strip() or len(payload.get('acceptance_location','').strip())>250):
                result={'outcome':'rejected','message':'Укажіть, де ви зараз стоїте (до 250 символів).','redirect':reverse('dashboard')}
            elif action=='accept' and not trip.is_accepted and (not payload.get('acceptance_odometer_km','').strip().isascii() or not payload.get('acceptance_odometer_km','').strip().isdigit() or len(payload.get('acceptance_odometer_km','').strip())>7):
                result={'outcome':'rejected','message':'Укажіть показник спідометра цілим числом від 0 до 9 999 999 км.','redirect':reverse('dashboard')}
            elif not revision or revision!=trip.updated_at:
                result={'outcome':'rejected','message':'Логіст або інша вкладка вже змінили рейс. Оновіть дані перед наступною дією.','redirect':reverse('dashboard')}
            else:
                previous_status=trip.status
                previous_revision=trip.updated_at
                response=handlers[action](request,pk)
                trip.refresh_from_db()
                confirmed=(action=='accept' and trip.is_accepted) or (action=='start' and previous_status=='queued' and trip.status in Trip.ACTIVE) or (action=='status' and previous_status!=payload['status'] and trip.status==payload['status']) or (action=='ack_cancel' and trip.status=='cancelled' and trip.cancellation_acknowledged_at is not None)
                confirmed=confirmed or (action=='stop' and trip.updated_at!=previous_revision and response.status_code==302) or (action=='ack_route' and trip.route_notice_acknowledged_at is not None)
                if confirmed:
                    result={'outcome':'confirmed','redirect':reverse('dashboard'),'message':'Сервер підтвердив зміну. Логіст бачить оновлені дані.'}
                elif (action=='status' and payload['status']=='completed' or action=='stop') and response.status_code==200:
                    from urllib.parse import urlencode
                    details={key:value for key,value in payload.items() if (key in ['actual_tons','distance_km','empty_distance_km','loaded_distance_km','fuel_used_l'] or key.startswith('unload_weight_')) and value}
                    result={'outcome':'reminder','redirect':reverse('driver_finish',args=[pk])+('?' + urlencode(details) if details else '')}
                else:
                    result={'outcome':'rejected','message':'Дію не збережено. Оновіть рейс і перевірте повідомлення.','redirect':reverse('trip_detail',args=[pk])}
            receipt.result=result;receipt.save(update_fields=['result'])
        return JsonResponse(result)
    except ValidationError as exc:
        return JsonResponse({'outcome':'rejected','message':exc.messages[0]},status=400)


@login_required
@require_GET
def driver_finish(request,pk):
    from .views import trips_for
    if request.user.role!='driver': raise PermissionDenied
    trip=get_object_or_404(trips_for(request.user),pk=pk)
    if trip.route_enabled:
        from .routes import route_reminder_context
        stop=trip.current_stop
        if not trip.is_active or not stop or stop.kind!='unload' or stop.state!='arrived': return redirect('trip_detail',pk=pk)
        context=route_reminder_context(trip,request.GET.get('actual_tons',''))
        if context['route_last_stop']:
            from .finish_details import render_finish
            return render_finish(request,trip,request.GET if request.GET else None)
        if not context['missing_ttn_stops']: return redirect('trip_detail',pk=pk)
        return render(request,'fleet/route_finish_reminder.html',context)
    if trip.status!='unloading': return redirect('trip_detail',pk=pk)
    from .finish_details import render_finish
    return render_finish(request,trip,request.GET if request.GET else None)
