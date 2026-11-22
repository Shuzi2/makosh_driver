"""Two everyday screens for drivers, scoped to their own trips."""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render, redirect
from .forms import DocumentForm
from .models import Trip
from .trip_stages import status_label, stage_progress, driver_stage_progress, driver_journey_progress, LABELS
import hashlib
import json
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.db.models import Count, Max, Q
from django.db.models import Case, When, DateTimeField
from django.db.models.functions import Cast, Coalesce


def driver_update_version(user):
    own=Trip.objects.filter(driver=user)
    trips=list(own.order_by('pk').values_list('pk','updated_at','vehicle__in_repair','route_enabled'))
    documents=own.aggregate(count=Count('documents',filter=Q(documents__deleted_at__isnull=True)),latest=Max('documents__uploaded_at',filter=Q(documents__deleted_at__isnull=True)))
    # Queue availability may change when another driver frees the same vehicle.
    blockers=list(Trip.objects.filter(vehicle_id__in=own.filter(status='queued').values('vehicle_id'),status__in=Trip.ACTIVE+['queued']).order_by('pk').values_list('pk','updated_at','planned_date'))
    return hashlib.sha256(json.dumps(['driver-finish-details-1',user.rest_started_at,user.rest_changed_at,trips,documents,blockers],default=str).encode()).hexdigest()


@login_required
@require_GET
def driver_updates(request):
    if request.user.role != 'driver': raise PermissionDenied
    from .driver_notifications import visible_notices
    response=JsonResponse({'version':driver_update_version(request.user),'assignment_pending':visible_notices(request.user).filter(read_at__isnull=True).exists(),'route_change_pending':Trip.objects.filter(driver=request.user,status__in=Trip.ACTIVE+['queued'],route_notice_acknowledged_at__isnull=True).exclude(route_notice='').exists(),'cancellation_pending':Trip.objects.filter(driver=request.user,status='cancelled',cancellation_notice_required=True,cancellation_acknowledged_at__isnull=True).exists()})
    response['Cache-Control']='private, no-store'
    return response


ACTIONS = {
    'planned': ('Приїхав на завантаження','loading'),
    'loading': ('Завантажився — виїжджаю','road'),
    'road': ('Приїхав на розвантаження','unloading'),
    'unloading': ('Розвантажився — завершити рейс','completed'),
}

@login_required
@require_POST
def driver_queue_check(request,pk):
    if request.user.role != 'driver': raise PermissionDenied
    from .views import trips_for, queue_block_reason
    from .queue_promotion import promote_trip
    from django.contrib import messages
    trip=get_object_or_404(trips_for(request.user),pk=pk)
    if trip.status=='queued':
        if promote_trip(trip.pk):
            messages.success(request,'Рейс став поточним. Тепер його можна прийняти.')
        else:
            trip.refresh_from_db()
            if trip.status=='queued':
                reason=queue_block_reason(trip) or 'Перевірте закріплення водія за авто у логіста.'
                messages.error(request,'Рейс ще в черзі. '+reason)
    return redirect('trip_detail',pk=pk)


@login_required
@require_GET
def driver_trips(request):
    if request.user.role != 'driver': raise PermissionDenied
    from django.core.paginator import Paginator
    from .views import trips_for
    own = trips_for(request.user)
    active = own.filter(status__in=Trip.ACTIVE).order_by('-updated_at', '-pk')
    upcoming = Trip.ordered_queue(own.filter(status='queued'))
    history = own.filter(status__in=['completed', 'cancelled']).annotate(history_at=Coalesce(
        Case(When(status='completed',then='completed_at'),When(status='cancelled',then='cancelled_at'),output_field=DateTimeField()),
        Cast('planned_date',DateTimeField()),
    )).order_by('-history_at','-id')
    page = Paginator(history, 20).get_page(request.GET.get('page'))
    return render(request,'fleet/driver_trip_overview.html',{
        'driver_update_version':driver_update_version(request.user),
        'current_trips':[document_trip_overview(trip) for trip in active],
        'upcoming_trips':[document_trip_overview(trip) for trip in upcoming],
        'past_trips':[document_trip_overview(trip) for trip in page.object_list],
        'history_page':page,
    })



def driver_home(request, selected_trip=None):
    from .views import trips_for, queue_block_reason
    from .permissions_models import DriverPermissionSettings
    operation_permissions,_=DriverPermissionSettings.objects.get_or_create(pk=1)
    version=driver_update_version(request.user)
    own = trips_for(request.user)
    if selected_trip: selected_trip=get_object_or_404(own,pk=selected_trip.pk)
    active = own.filter(status__in=Trip.ACTIVE).first()
    queue = list(Trip.ordered_queue(own.filter(status='queued')))
    history = own.filter(status__in=['completed','cancelled']).annotate(history_at=Coalesce(
        Case(When(status='completed',then='completed_at'),When(status='cancelled',then='cancelled_at'),output_field=DateTimeField()),
        Cast('planned_date',DateTimeField()),
    )).order_by('-history_at','-id')
    trip = selected_trip or active or (queue[0] if queue else None)
    action,next_status=ACTIONS.get(trip.status,('','')) if trip else ('','')
    next_label=LABELS.get(next_status,'')
    label=status_label(trip) if trip else ''
    needs_acceptance=bool(trip and trip.requires_acceptance)
    if needs_acceptance: action,next_status='Прийняти рейс','accepted'
    from .routes import route_context, fleet_ttn_summary
    route_data=route_context(trip)
    history_trips=list(history.prefetch_related('documents','stops')[:20])
    for past in history_trips:
        past.ttn_summary=fleet_ttn_summary(past)
        past.ttn_all_complete=all(item['state']=='complete' for item in past.ttn_summary)
    steps=stage_progress(trip)
    driver_steps=driver_stage_progress(trip, route_data.get('driver_simple_route',True))
    journey_simple=not trip or not trip.route_enabled or route_data.get('driver_simple_route',True)
    journey_steps=driver_journey_progress(trip, journey_simple)
    action_hint=''
    if trip and trip.is_active and not needs_acceptance:
        if trip.route_enabled:
            stop=route_data.get('current_stop');mode=route_data.get('stop_mode')
            if mode=='depart' and route_data.get('route_last_stop'): action_hint='Відкриється форма завершення рейсу.'
            elif mode=='depart': action_hint='Підтвердите виїзд і побачите наступну точку маршруту.'
            elif stop: action_hint='Логіст побачить, що ви прибули до цієї точки.'
        else:
            action_hint={'planned':'Логіст побачить, що ви прибули на завантаження.','loading':'Підтвердите виїзд із завантаження.','road':'Логіст побачить, що ви прибули на розвантаження.','unloading':'Відкриється форма завершення рейсу.'}.get(trip.status,'')
    return render(request,'fleet/driver_home.html',{
        **route_data,'driver_update_version':version,'operation_permissions':operation_permissions,
        'pending_cancellations':own.filter(status='cancelled',cancellation_notice_required=True,cancellation_acknowledged_at__isnull=True).select_related('cancelled_by','created_by').order_by('-cancelled_at','-pk'),
        'trip':trip, 'active_trip':active, 'queued_trips':[item for item in queue if not trip or item.pk != trip.pk],
        'vehicle_current_trip':trip.vehicle.trips.filter(status__in=Trip.ACTIVE).exclude(pk=trip.pk).exists() if trip and trip.status=='queued' else False, 'status_label':label,
        'needs_acceptance':needs_acceptance, 'history_trips':history_trips, 'history_count':history.count(),
        'history_selected':bool(trip and trip.status in ['completed','cancelled']),
        'action_label':action, 'next_status':next_status, 'next_label':next_label, 'steps':steps, 'driver_steps':driver_steps, 'journey_steps':journey_steps, 'journey_simple':journey_simple, 'action_hint':action_hint,
        'queue_block_reason':queue_block_reason(trip) if trip and trip.status=='queued' else '',
        'viewing_other':bool(selected_trip and active and selected_trip.pk!=active.pk),
        'cards':[], 'vehicle_count':0, 'assignment_form':None, 'events':[],
        'can_write':True, 'documents_screen':False,
        'driver_ttn_summary':fleet_ttn_summary(trip) if trip else [],
    })


@login_required
def driver_documents(request):
    if request.user.role != 'driver': raise PermissionDenied
    return documents_screen(request)


def document_trip_overview(trip):
    """A short, owned-trip label for the driver's document archive."""
    from .settlements import resolve_address
    from .routes import fleet_ttn_summary
    points=list(trip.stops.all()) if trip.route_enabled else []
    live=[point for point in points if point.state!='cancelled']
    shown=live or points
    def place_name(address):
        place=resolve_address(address)
        return place['title'] if place else address.split(',')[0].strip()
    origin=shown[0].driver_place['name'] if shown else place_name(trip.origin)
    destination=shown[-1].driver_place['name'] if shown else place_name(trip.destination)
    unloads=[point for point in live if point.kind=='unload']
    summary=fleet_ttn_summary(trip)
    loading_ok=summary[0]['state']=='complete'
    signed=summary[1]['state']=='complete'
    if trip.status=='cancelled': state,hint='neutral','Збережені документи'
    elif loading_ok and signed: state,hint='complete','Усі ТТН завантажені'
    elif trip.status=='completed':
        state='missing'
        hint='Додати ТТН з підписом' if not signed else 'Додати ТТН завантаження'
    else: state,hint='neutral','Документи рейсу'
    missing_kind='signed_ttn' if not signed else 'ttn'
    target=None
    if trip.route_enabled:
        from .routes import route_document_status
        checked=route_document_status(trip,live,documents=[(doc.stop_id,doc.kind,doc.ttn_number) for doc in trip.documents.all() if doc.stop_id])
        target=next((point for point in checked if point.ttn_upload_kind==missing_kind and not point.ttn_complete),None)
        if target is None and state=='complete': target=next((point for point in checked if point.kind=='unload'),None)
    return {'trip':trip,'origin':origin,'destination':destination,'point_count':len(shown),
            'state':state,'hint':hint,'has_signed':signed,'ttn_summary':summary,'stop_id':target.pk if target else None,
            'kind':missing_kind if state=='missing' else 'signed_ttn' if state=='complete' else ''}



def documents_screen(request):
    from .views import trips_for
    version=driver_update_version(request.user)
    own = trips_for(request.user)
    missing_only = request.GET.get('status') == 'missing'
    if missing_only:
        from .routes import signed_expression
        own = own.filter(status='completed').annotate(all_signed=signed_expression()).filter(all_signed=False)
    selected = request.GET.get('trip')
    if not selected:
        from django.core.paginator import Paginator
        page = Paginator(own.prefetch_related('stops','documents').order_by('-planned_date','-id'), 20).get_page(request.GET.get('page'))
        return render(request, 'fleet/driver_document_list.html', {
            'driver_update_version': version, 'documents_screen': True,
            'document_trips': [document_trip_overview(trip) for trip in page.object_list],
            'page_obj': page, 'missing_only': missing_only,
        })
    trip = get_object_or_404(own,pk=selected) if selected.isdigit() else None
    if trip is None:
        from django.http import Http404
        raise Http404
    choices = list(own.prefetch_related('stops','documents').order_by('-planned_date','-id')[:100])
    if trip and trip not in choices: choices.insert(0,trip)
    from .routes import route_document_status
    points=route_document_status(trip,list(trip.stops.exclude(state='cancelled'))) if trip and trip.route_enabled else []
    selected_stop=request.GET.get('stop')
    initial_stop=None
    if selected_stop and points:
        initial_stop=next((point for point in points if str(point.pk)==selected_stop),None)
        if initial_stop is None:
            from django.http import Http404
            raise Http404
    initial_stop=initial_stop or next((p for p in points if trip.current_stop and p.pk==trip.current_stop.pk),None) or next((p for p in points if not p.ttn_complete),None) or next(iter(points),None)
    initial_kind=request.GET.get('kind')
    if initial_kind not in ['ttn','signed_ttn','weight','other']:
        initial_kind=('signed_ttn' if initial_stop.kind=='unload' else 'ttn') if initial_stop else ('signed_ttn' if trip and trip.status in ['unloading','completed'] else 'ttn')
    if initial_stop and initial_kind in ['ttn','signed_ttn']: initial_kind=initial_stop.ttn_upload_kind
    documents=list(trip.documents.select_related('stop').order_by('-uploaded_at','-pk')) if trip else []
    selected_documents=[doc for doc in documents if not initial_stop or doc.stop_id==initial_stop.pk]
    other_documents=[doc for doc in documents if initial_stop and doc.stop_id!=initial_stop.pk]
    numbers=initial_stop.ttn_numbers if initial_stop else []
    overview=document_trip_overview(trip) if trip else None
    other_trips=[document_trip_overview(choice) for choice in choices if not trip or choice.pk!=trip.pk]
    return render(request,'fleet/driver_documents.html',{
        'driver_update_version':version,
        'trip':trip,'trip_choices':choices,'upload_form':DocumentForm(trip=trip,initial={'stop':initial_stop,'kind':initial_kind}),'documents_screen':True,
        'missing_only':missing_only,
        'document_points':points,'document_stop':initial_stop,'document_kind':initial_kind,
        'document_origin':overview['origin'] if overview else '',
        'document_destination':overview['destination'] if overview else '',
        'document_ttn_summary':overview['ttn_summary'] if overview else [],
        'other_document_trips':other_trips,
        'document_numbers':numbers,'document_number':request.GET.get('ttn_number') if request.GET.get('ttn_number') in numbers else next(iter(initial_stop.missing_ttn_numbers or numbers),'') if initial_stop else '',
        'selected_documents':selected_documents,'other_documents':other_documents,
    })
