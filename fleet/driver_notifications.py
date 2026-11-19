from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from .models import DriverAssignmentNotification, DriverServiceNotification, Trip

def notify_assignment(trip):
    DriverAssignmentNotification.objects.create(recipient_id=trip.driver_id,trip=trip)

def resolve_assignment(trip,driver_id=None):
    DriverAssignmentNotification.objects.filter(trip=trip,recipient_id=driver_id or trip.driver_id,resolved_at__isnull=True).update(resolved_at=timezone.now())

def visible_notices(user):
    # A reassigned, accepted, cancelled or completed task is no longer a new task.
    return DriverAssignmentNotification.objects.filter(recipient=user,trip__driver=user,trip__status__in=Trip.ACTIVE+['queued'],trip__accepted_at__isnull=True,resolved_at__isnull=True).select_related('trip__vehicle')

@login_required
@require_GET
def inbox(request):
    if request.user.role!='driver': raise PermissionDenied
    notices=visible_notices(request.user)
    items=[{'id':n.pk,'type':'trip','trip':n.trip_id,'vehicle':n.trip.vehicle.plate,'url':reverse('trip_detail',args=[n.trip_id]),'time':timezone.localtime(n.created_at).strftime('%d.%m.%Y %H:%M'),'sort_time':n.created_at.isoformat(),'read':bool(n.read_at)} for n in notices[:40]]
    services=DriverServiceNotification.objects.filter(recipient=request.user,service_request__driver=request.user).select_related('service_request')
    items += [{'id':f'service:{n.pk}','type':'service','title':n.service_request.title,'message':n.message,'url':reverse('driver_service')+f'#service-{n.service_request_id}','time':timezone.localtime(n.created_at).strftime('%d.%m.%Y %H:%M'),'sort_time':n.created_at.isoformat(),'read':bool(n.read_at)} for n in services[:40]]
    items.sort(key=lambda item:item['sort_time'],reverse=True)
    response=JsonResponse({'items':items[:80],'unread':notices.filter(read_at__isnull=True).count()+services.filter(read_at__isnull=True).count()})
    response['Cache-Control']='private, no-store'
    return response

@login_required
@require_POST
def mark_read(request):
    if request.user.role!='driver': raise PermissionDenied
    value=request.POST.get('id','')
    if value.startswith('service:'):
        value=value[8:]
        if not value.isascii() or not value.isdigit() or len(value)>18: return JsonResponse({'error':'Invalid id'},status=400)
        changed=DriverServiceNotification.objects.filter(pk=int(value),recipient=request.user,service_request__driver=request.user,read_at__isnull=True).update(read_at=timezone.now())
        return JsonResponse({'updated':bool(changed)})
    if not value.isascii() or not value.isdigit() or len(value)>18: return JsonResponse({'error':'Invalid id'},status=400)
    changed=visible_notices(request.user).filter(pk=int(value),read_at__isnull=True).update(read_at=timezone.now())
    return JsonResponse({'updated':bool(changed)})
