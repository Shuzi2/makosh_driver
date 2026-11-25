import hashlib
from pathlib import Path
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from .forms import DocumentForm
from .driver_requests import locked_receipt


def upload_documents(request,pk):
    from .views import trips_for, can_write, trip_response, event
    trip=get_object_or_404(trips_for(request.user),pk=pk)
    def check_access(trip):
        if not can_write(request.user,trip) or trip.status=='cancelled' or (request.user.role=='driver' and not trip.driver_can_upload_documents): raise PermissionDenied
    check_access(trip)
    wants_json=request.headers.get('Accept')=='application/json'
    files=request.FILES.getlist('file')
    errors=[]
    if not 1<=len(files)<=10: errors.append('Оберіть від 1 до 10 файлів.')
    if sum(file.size for file in files)>40*1024*1024: errors.append('Разом файли мають займати не більше 40 МБ.')
    forms=[]
    if not errors:
        for file in files:
            form=DocumentForm(request.POST,{'file':file},trip=trip)
            if form.is_valid(): forms.append(form)
            else:
                errors.extend(f'{Path(file.name).name}: {error}' for values in form.errors.values() for error in values)
    if errors:
        if wants_json: return JsonResponse({'outcome':'rejected','message':' '.join(errors)},status=400)
        for error in errors: messages.error(request,error)
        return trip_response(request,pk,documents=True)
    saved=[]
    try:
        with transaction.atomic():
            trip=get_object_or_404(trips_for(request.user).select_for_update(),pk=pk)
            check_access(trip)
            receipt=None
            if wants_json:
                fingerprints=[]
                for file in files:
                    digest=hashlib.sha256()
                    for chunk in file.chunks(): digest.update(chunk)
                    file.seek(0)
                    fingerprints.append([file.name,file.size,digest.hexdigest()])
                receipt=locked_receipt(request,trip,'documents',{'kind':request.POST.get('kind'),'stop':request.POST.get('stop',''),'ttn_number':request.POST.get('ttn_number',''),'files':fingerprints})
                if receipt.result: return JsonResponse(receipt.result)
            for form in forms:
                doc=form.save(commit=False);doc.trip=trip;doc.uploaded_by=request.user
                if trip.route_enabled:
                    from .models import TripStop
                    current_stop=TripStop.objects.filter(pk=doc.stop_id,trip=trip).exclude(state='cancelled').first()
                    if not current_stop: raise ValidationError('Точку маршруту скасовано. Відкрийте актуальні документи.')
                    if doc.kind=='signed_ttn' and (current_stop.kind!='unload' or current_stop.ttn_numbers and doc.ttn_number not in current_stop.ttn_numbers):
                        raise ValidationError('Дані ТТН у точці змінилися. Перевірте актуальні номери перед надсиланням.')
                    if doc.kind=='ttn' and current_stop.ttn_numbers and doc.ttn_number not in current_stop.ttn_numbers:
                        raise ValidationError('Номери ТТН завантаження змінилися. Відкрийте актуальні документи.')
                    doc.stop=current_stop
                doc.original_name=Path(form.cleaned_data['file'].name).name[:250]
                doc.content_type=form.detected_type;doc.size=form.cleaned_data['file'].size
                saved.append(doc)
                doc.save();event(request.user,'Додано документ',trip,f'{doc.get_kind_display()}: {doc.original_name}'+(f' · Точка {doc.stop.position}, {doc.stop.address} · ТТН {doc.ttn_number}' if doc.stop_id else ''))
                if request.user.role == 'driver' and doc.kind in ('ttn', 'signed_ttn'):
                    from .models import TtnNotification, User
                    from .permissions_models import ModeratorPermissionSettings
                    permissions=ModeratorPermissionSettings.objects.filter(pk=1).first()
                    roles=['admin']
                    if permissions is None or (permissions.view_documents and permissions.view_trips): roles.append('moderator')
                    TtnNotification.objects.bulk_create([TtnNotification(recipient_id=user_id, document=doc) for user_id in User.objects.filter(is_active=True,role__in=roles).values_list('pk',flat=True)])
                    from .push import enqueue_staff
                    from .document_notices import describe
                    enqueue_staff('staff_ttn',trip,message=describe(doc)['message'])
                if doc.stop_id:
                    from .routes import refresh_signed
                    refresh_signed(doc.stop)
            if trip.route_enabled:
                trip.save(update_fields=['updated_at'])
            result={'outcome':'confirmed','message':f'Надіслано файлів: {len(saved)}. Сервер підтвердив збереження.','redirect':reverse('driver_documents')+f'?trip={pk}'}
            if request.user.role=='driver' and saved[0].stop_id:
                result['redirect']+=f'&stop={saved[0].stop_id}'
            if receipt:
                receipt.result=result;receipt.save(update_fields=['result'])
    except ValidationError as exc:
        return JsonResponse({'outcome':'rejected','message':exc.messages[0]},status=400)
    except Exception:
        for doc in saved:
            if doc.file and doc.file.name and doc.file._committed: doc.file.delete(save=False)
        raise
    messages.success(request,f'Документи збережено: {len(saved)}.')
    if any(doc.content_type=='application/octet-stream' for doc in saved):
        messages.warning(request,'Файл збережено, але попередній перегляд недоступний. Завантажте його та перевірте вміст.')
    if wants_json: return JsonResponse(result)
    if request.user.role=='driver' and saved[0].stop_id: return redirect(result['redirect'])
    return trip_response(request,pk,documents=True)
