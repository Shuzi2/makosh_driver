"""Explicit, owner-only removal of a test trip and its dependent records."""
import logging
import re
from datetime import timedelta
from pathlib import PurePosixPath
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .models import (AttentionSkip, AuditEvent, Document, DriverRequest, LoginThrottle,
                     Trip, TripDelay, TripEconomics, TripExpense, TripStop, TripTransfer, Vehicle)
import hashlib

logger=logging.getLogger(__name__)

def can_delete_test_trip(user):
    return bool(user.is_authenticated and user.is_active and user.role=='admin' and user.can_delete_test_trips)

@login_required
@require_http_methods(['POST'])
def account_test_trip_permission(request, pk):
    if not request.user.is_superuser or not request.user.is_active:
        raise PermissionDenied
    from .models import User
    account = get_object_or_404(User, pk=pk)
    enabled = request.POST.get('enabled') == '1'
    if enabled and (account.role != User.Role.ADMIN or not account.is_active):
        messages.error(request, 'Дозвіл можна надати лише активному адміністратору.')
    elif account.can_delete_test_trips != enabled:
        account.can_delete_test_trips = enabled
        account.save(update_fields=['can_delete_test_trips'])
        AuditEvent.objects.create(actor=request.user, action='Змінено дозвіл видалення тестових рейсів',
                                  detail=f'{account.full_name} ({account.phone}): {"надано" if enabled else "забрано"}')
        messages.success(request, 'Дозвіл оновлено.')
    return redirect('account_detail', pk=pk)

def _file_names(trip):
    docs=list(Document.all_objects.filter(trip=trip).values_list('file',flat=True))
    receipts=list(TripExpense.objects.filter(trip=trip).exclude(receipt='').values_list('receipt',flat=True))
    for name,prefix in [(n,'trips') for n in docs]+[(n,'expense-receipts') for n in receipts]:
        path=PurePosixPath(name)
        if not name or path.is_absolute() or len(path.parts)<3 or path.parts[:2]!=(prefix,str(trip.pk)) or '..' in path.parts or '\\' in name:
            raise ValueError('Небезпечний шлях документа. Зверніться до адміністратора.')
    return docs+receipts

def _undo_odometer(trip, vehicle):
    entries=list(AuditEvent.objects.filter(trip=trip,action='Пробіг рейсу додано до одометра').order_by('created_at'))
    if not entries: return 'Пробіг цього рейсу не був доданий до одометра або немає підтвердження.'
    if len(entries)!=1: return 'Одометр не змінено: є кілька записів про додавання пробігу.'
    entry=entries[0]
    match=re.fullmatch(r'(.+): \+([0-9]+) км; одометр ([0-9]+) км',entry.detail)
    if not match or vehicle.odometer_km is None or not vehicle.odometer_updated_at:
        return 'Одометр не змінено: неможливо надійно визначити попереднє значення.'
    plate,added,end=match.groups();added=int(added);end=int(end)
    if plate!=vehicle.plate or end<added or vehicle.odometer_km!=end or vehicle.odometer_updated_at>entry.created_at:
        return 'Одометр не змінено: після рейсу показник міг змінитися. Перевірте його вручну.'
    vehicle.odometer_km=end-added;vehicle.odometer_updated_at=timezone.now()
    vehicle.save(update_fields=['odometer_km','odometer_updated_at'])
    return f'З одометра віднято {added} км.'

def _check_password(request, password):
    key=hashlib.sha256(f'test-trip-delete:{request.user.pk}'.encode()).hexdigest()
    with transaction.atomic():
        bucket,_=LoginThrottle.objects.select_for_update().get_or_create(key=key,defaults={'window_start':timezone.now()})
        if bucket.window_start < timezone.now()-timedelta(minutes=15):
            bucket.failures=0;bucket.window_start=timezone.now()
        if bucket.failures>=10: return False
        if request.user.check_password(password):
            bucket.delete();return True
        bucket.failures+=1;bucket.save(update_fields=['failures','window_start']);return False

@login_required
@require_http_methods(['GET','POST'])
def delete_test_trip(request,pk):
    if not can_delete_test_trip(request.user): raise PermissionDenied
    trip=get_object_or_404(Trip.objects.select_related('driver','vehicle'),pk=pk)
    if request.method=='POST':
        if request.POST.get('is_test')!='yes':
            messages.error(request,'Підтвердіть, що цей рейс тестовий.')
        elif request.POST.get('confirm','').strip()!=str(pk):
            messages.error(request,'Введіть номер рейсу для підтвердження.')
        elif not _check_password(request,request.POST.get('password','')):
            messages.error(request,'Неправильний пароль або забагато спроб. Спробуйте пізніше.')
        else:
            failures=[]
            try:
                with transaction.atomic():
                    trip=get_object_or_404(Trip.objects.select_for_update(),pk=pk)
                    vehicle=Vehicle.objects.select_for_update().get(pk=trip.vehicle_id)
                    names=_file_names(trip)
                    from django.core.files.storage import default_storage
                    odometer_result=_undo_odometer(trip,vehicle)
                    AttentionSkip.objects.filter(trip=trip).delete()
                    Document.all_objects.filter(trip=trip).delete()
                    TripExpense.objects.filter(trip=trip).delete()
                    TripDelay.objects.filter(trip=trip).delete()
                    TripEconomics.objects.filter(trip=trip).delete()
                    TripTransfer.objects.filter(trip=trip).delete()
                    DriverRequest.objects.filter(trip=trip).delete()
                    AuditEvent.objects.filter(trip=trip).delete()
                    TripStop.objects.filter(trip=trip).delete()
                    trip.delete()
                    audit=AuditEvent.objects.create(actor=request.user,action='Видалено тестовий рейс',detail=f'Рейс №{pk}. {odometer_result}')
                    def remove_files():
                        for name in names:
                            try: default_storage.delete(name)
                            except Exception:
                                failures.append(name);logger.exception('Failed to delete test trip file %s',name)
                        if failures:
                            try:
                                AuditEvent.objects.filter(pk=audit.pk).update(detail=f'Рейс №{pk}. {odometer_result} Не вдалося видалити деякі файли; деталі у журналі сервера.')
                            except Exception:
                                logger.exception('Could not record failed test trip file deletion')
                    transaction.on_commit(remove_files)
                messages.success(request,f'Тестовий рейс №{pk} видалено. {odometer_result}')
                if failures: messages.warning(request,'Не вдалося видалити деякі файли з диска. Зверніться до адміністратора сервера.')
                return redirect('trip_list')
            except ValueError as exc:
                messages.error(request,str(exc))
    return render(request,'fleet/test_trip_delete.html',{'trip':trip})
