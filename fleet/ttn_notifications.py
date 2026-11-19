from django.http import JsonResponse
from django.db.models import F
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from .models import TtnNotification, DelayNotification
from .views import staff_required


@staff_required
@require_GET
def inbox(request):
    records=TtnNotification.objects.filter(recipient=request.user,document__deleted_at__isnull=True).select_related('document__trip__vehicle','document__uploaded_by')
    items=[]
    for notice in records.order_by(F('read_at').asc(nulls_first=True),'-created_at','-pk')[:40]:
        doc=notice.document
        items.append({'id':notice.pk, 'kind':doc.get_kind_display(), 'driver':doc.uploaded_by.full_name,
                      'trip':doc.trip_id, 'vehicle':doc.trip.vehicle.plate,
                      'time':timezone.localtime(notice.created_at).strftime('%d.%m.%Y %H:%M'),
                      'read':notice.read_at is not None, 'sort_time':notice.created_at.isoformat(),
                      'url':reverse('trip_detail',args=[doc.trip_id])+'#documents'})
    delays=DelayNotification.objects.filter(recipient=request.user).select_related('delay__trip__vehicle','delay__driver')
    for notice in delays.order_by(F('read_at').asc(nulls_first=True),'-delay__created_at','-pk')[:40]:
        delay=notice.delay
        items.append({'id':f'delay:{notice.pk}','kind':'Затримка: '+delay.get_reason_display(), 'driver':delay.driver.full_name,
                      'trip':delay.trip_id,'vehicle':delay.trip.vehicle.plate,
                      'time':timezone.localtime(delay.created_at).strftime('%d.%m.%Y %H:%M'),
                      'read':notice.read_at is not None,'sort_time':delay.created_at.isoformat(),
                      'url':reverse('trip_detail',args=[delay.trip_id])+'#trip-history'})
    items.sort(key=lambda item:item['sort_time'],reverse=True)
    items.sort(key=lambda item:item['read'])
    response=JsonResponse({'items':items[:40],'unread':records.filter(read_at__isnull=True).count()+delays.filter(read_at__isnull=True).count()})
    response['Cache-Control']='private, no-store'
    return response


@staff_required
@require_POST
def mark_read(request):
    notice_id=request.POST.get('id','')
    model=TtnNotification
    if notice_id.startswith('delay:'):
        model=DelayNotification
        notice_id=notice_id[6:]
    if not notice_id.isascii() or not notice_id.isdigit() or len(notice_id)>18:
        return JsonResponse({'error':'Invalid id'},status=400)
    changed=model.objects.filter(pk=int(notice_id),recipient=request.user,read_at__isnull=True).update(read_at=timezone.now())
    return JsonResponse({'updated':bool(changed)})
