import secrets
from datetime import timedelta
from functools import wraps
from pathlib import Path
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction, IntegrityError, connection
from django.db.models import Sum, Q
from django.db.models.functions import Coalesce
from django.http import FileResponse, HttpResponse, JsonResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.dateparse import parse_datetime
from django.core.paginator import Paginator
from django.views.decorators.http import require_POST, require_GET, require_http_methods
from .models import User, Vehicle, Trailer, TrailerTransfer, Trip, Document, AuditEvent, VehicleRepair, TripTransfer
from .forms import TripForm, TripEditForm, DocumentForm, AssignmentForm, DriverSubstitutionForm, AccountForm, AccountCreateForm, AccountDetailsForm, VehicleForm, RepairForm, RepairFinishForm, RepairCostsForm
from .forms import TripFactsForm, TrailerChangeForm, TrailerCreateForm
from .assignment_history import record_assignment
from django.core.exceptions import ValidationError

def staff_required(view):
    @wraps(view)
    @login_required
    def inner(request,*args,**kwargs):
        if not request.user.manages_fleet: raise PermissionDenied
        return view(request,*args,**kwargs)
    return inner

def admin_required(view):
    @wraps(view)
    @login_required
    def inner(request, *args, **kwargs):
        if not request.user.manages_users: raise PermissionDenied
        return view(request, *args, **kwargs)
    return inner

def trips_for(user):
    qs=Trip.objects.select_related("vehicle","driver").prefetch_related("documents","stops")
    if user.manages_fleet: return qs
    return qs.filter(driver=user)

def can_write(user,trip):
    return user.manages_fleet or (user.role=="driver" and trip.driver_id==user.id)

def event(user,action,trip=None,detail=""):
    AuditEvent.objects.create(actor=user,trip=trip,action=action,detail=detail)

def trip_response(request,pk,documents=False):
    if request.user.role == 'driver':
        if documents:
            return redirect(reverse('driver_documents')+f'?trip={pk}')
        return redirect('dashboard')
    return redirect('trip_detail',pk=pk)

def queue_block_reason(trip):
    if trip.vehicle.in_repair: return 'Авто на ремонті.'
    if not trip.vehicle.trailer_id: return 'Для авто не закріплено причіп.'
    if trip.vehicle.trips.filter(status__in=Trip.ACTIVE).exists(): return 'Авто ще виконує поточний рейс.'
    if trip.driver.trips.filter(status__in=Trip.ACTIVE).exists(): return 'У водія ще є поточний рейс.'
    first=Trip.ordered_queue(trip.vehicle.trips.filter(status='queued')).first()
    if first and first.pk!=trip.pk: return 'Спочатку виконайте попередній рейс у черзі.'
    first_driver=Trip.ordered_queue(trip.driver.trips.filter(status='queued')).first()
    if first_driver and first_driver.pk!=trip.pk: return 'У водія є попередній рейс у черзі.'
    if not trip.driver.is_active: return 'Обліковий запис водія вимкнений.'
    return ''

@login_required
@require_POST
def trip_accept(request,pk):
    if request.user.role != 'driver': raise PermissionDenied
    with transaction.atomic():
        trip=get_object_or_404(trips_for(request.user).select_for_update(),pk=pk)
        if trip.status == 'queued':
            messages.error(request,'Це майбутній рейс. Прийняти його можна, коли він стане поточним.')
        elif trip.status not in Trip.ACTIVE:
            messages.error(request,'Цей рейс уже виконується або завершений.')
        elif not trip.is_accepted:
            location=request.POST.get('acceptance_location','').strip()
            if not location or len(location)>250:
                messages.error(request,'Укажіть, де ви зараз стоїте (до 250 символів).')
                return redirect('trip_detail',pk=pk)
            raw_odometer=request.POST.get('acceptance_odometer_km','').strip()
            if not raw_odometer.isascii() or not raw_odometer.isdigit() or len(raw_odometer)>7:
                messages.error(request,'Укажіть показник спідометра цілим числом від 0 до 9 999 999 км.')
                return redirect('trip_detail',pk=pk)
            odometer=int(raw_odometer)
            trip.accepted_at=timezone.now();trip.accepted_by=request.user
            if trip.status in ['planned','queued']:
                trip.acceptance_location=location
                trip.acceptance_odometer_km=odometer
            trip.status_changed_at=trip.accepted_at
            trip.save(update_fields=['accepted_at','accepted_by','acceptance_location','acceptance_odometer_km','status_changed_at','updated_at'])
            from .driver_notifications import resolve_assignment
            resolve_assignment(trip)
            event(request.user,'Водій прийняв рейс',trip,f'Місце водія під час прийняття: {location}. Спідометр: {odometer} км.')
            from .staff_trip_notifications import notify_staff
            notify_staff(trip,request.user,'accepted')
            messages.success(request,'Рейс прийнято. Логіст бачить ваше підтвердження.')
    return redirect('trip_detail',pk=pk)

@login_required
@require_POST
def trip_start(request,pk):
    initial=get_object_or_404(trips_for(request.user),pk=pk)
    try:
        with transaction.atomic():
            Vehicle.objects.select_for_update().get(pk=initial.vehicle_id)
            trip=get_object_or_404(trips_for(request.user).select_for_update(),pk=pk)
            User.objects.select_for_update().get(pk=trip.driver_id)
            if not can_write(request.user,trip): raise PermissionDenied
            if trip.status!='queued':
                raise ValueError('Цей рейс уже не в черзі. Оновіть сторінку.')
            if request.user.role == 'driver' and not trip.is_accepted:
                raise ValueError('Спочатку прийміть рейс.')
            reason=queue_block_reason(trip)
            if reason: raise ValueError(reason)
            trip.status='planned'
            if trip.route_enabled:
                current=trip.current_stop
                if current and current.state=='arrived': trip.status='loading' if current.kind=='load' else 'unloading'
                elif trip.stops.filter(state='completed').exists(): trip.status='road'
            trip.queue_priority_at=None;trip.status_changed_at=timezone.now();trip.save(update_fields=['status','queue_priority_at','updated_at','status_changed_at'])
            event(request.user,'Розпочато рейс із черги',trip)
        messages.success(request,'Рейс став поточним. Можна починати завантаження.')
    except (IntegrityError,ValueError) as exc:
        messages.error(request,str(exc) if isinstance(exc,ValueError) else 'Авто або водій уже виконують поточний рейс.')
    return trip_response(request,pk)

def fleet_event_time(value):
    if not value: return ''
    local=timezone.localtime(value)
    today=timezone.localdate()
    prefix='Сьогодні' if local.date()==today else 'Учора' if local.date()==today-timedelta(days=1) else local.strftime('%d.%m.%Y')
    return f'{prefix}, {local:%H:%M}'

@login_required
def dashboard(request):
    if request.GET.get('live') == '1' and not request.user.manages_fleet: raise PermissionDenied
    if request.user.role == 'driver':
        from .driver_views import driver_home
        return driver_home(request)
    qs=trips_for(request.user)
    today=timezone.localdate()
    active=qs.filter(status__in=Trip.ACTIVE)
    queued=Trip.ordered_queue(qs.filter(status='queued'))
    done=qs.filter(status="completed",completed_at__date=today)
    from .routes import signed_expression
    missing=qs.filter(status='completed').annotate(all_signed=signed_expression()).filter(all_signed=False)
    cards=[]
    if request.user.manages_fleet:
        by_vehicle={t.vehicle_id:t for t in active}
        queue_by_vehicle={}
        for trip in queued:
            trip.fleet_accepted_label=fleet_event_time(trip.accepted_at)
            trip.fleet_origin_short=trip.origin.split(',')[0].strip()
            trip.fleet_destination_short=trip.destination.split(',')[0].strip()
            queue_by_vehicle.setdefault(trip.vehicle_id, []).append(trip)
        kind=request.GET.get('kind','')
        kind=kind if kind in ('tent','grain') else ''
        vehicles=Vehicle.objects.select_related("default_driver", "trailer")
        if kind: vehicles=vehicles.filter(kind=kind)
        for vehicle in vehicles:
            trip=by_vehicle.get(vehicle.id)
            repair=vehicle.repairs.filter(finished_at__isnull=True).first() if vehicle.in_repair else None
            changed=trip.status_changed_at if trip else (repair.recorded_at if repair else None)
            if not trip and not vehicle.in_repair:
                last_trip=vehicle.trips.filter(status__in=['completed','cancelled'],status_changed_at__isnull=False).order_by('-status_changed_at').first()
                last_repair=vehicle.repairs.filter(finished_recorded_at__isnull=False).order_by('-finished_recorded_at').first()
                known=[value for value in [last_trip.status_changed_at if last_trip else None,last_repair.finished_recorded_at if last_repair else None] if value]
                last_transfer=vehicle.outgoing_trip_transfers.order_by('-moved_at').first()
                if last_transfer: known.append(last_transfer.moved_at)
                changed=max(known) if known else None
            from .routes import fleet_route_context, fleet_ttn_summary
            summary=fleet_ttn_summary(trip)
            driver=trip.driver if trip else vehicle.default_driver
            cards.append({"vehicle":vehicle,"trip":trip,"route":fleet_route_context(trip),"driver":driver,"resting":bool(driver and driver.rest_started_at),"ttn_summary":summary,"status_changed_at":changed,"status_changed_label":fleet_event_time(changed),"queue":queue_by_vehicle.get(vehicle.id, []),"repair":repair})
    template = "fleet/dashboard.html" if request.user.manages_fleet else "fleet/driver_dashboard.html"
    groups = [{"kind": group_kind, "label": label, "cards": [card for card in cards if card['vehicle'].kind == group_kind]} for group_kind, label in [('tent', 'Тенти'), ('grain', 'Зерновози')] if not kind or kind == group_kind] if request.user.manages_fleet else []
    from .models import FleetCopySettings
    copy_settings=FleetCopySettings.objects.filter(pk=1).first() or FleetCopySettings()
    context={"cards":cards,"fleet_groups":groups,"fleet_kind":kind,"copy_settings":copy_settings,"free_count":sum(not card['trip'] and not card['vehicle'].in_repair for card in cards),"repair_count":sum(card['vehicle'].in_repair for card in cards),"rest_count":sum(card['resting'] for card in cards),"trips":active,"queued_trips":queued,"done_count":done.count(),"tons":done.aggregate(total=Sum(Coalesce("actual_tons","planned_tons")))["total"] or 0,"active_count":sum(bool(card['trip']) for card in cards),"missing_count":missing.count(),"missing":missing[:5],"today":today,"vehicle_count":len(cards)}
    if request.GET.get("live") == "1":
        response=JsonResponse({"cards":render_to_string("fleet/dashboard_cards.html",context,request=request),"metrics":render_to_string("fleet/dashboard_metrics.html",context,request=request),"vehicle_count":len(cards),"copy_carrier":copy_settings.carrier,"copy_storage":copy_settings.storage_address})
        response["Cache-Control"]="private, no-store"
        return response
    return render(request,template,context)

@login_required
def trip_list(request):
    if request.user.role == 'driver':
        from .driver_views import documents_screen
        return documents_screen(request)
    qs=trips_for(request.user)
    if request.GET.get('ttn_live') == '1' and request.user.manages_fleet:
        from .routes import fleet_ttn_summary
        ids=[int(value) for value in request.GET.get('ids', '')[:4000].split(',')[:200] if value.isascii() and value.isdigit() and len(value)<=18]
        response=JsonResponse({'trips': {str(trip.pk): fleet_ttn_summary(trip) for trip in qs.filter(pk__in=ids)}})
        response['Cache-Control']='private, no-store'
        return response
    filter_vehicles=list(qs.order_by('vehicle__plate','vehicle_id').values('vehicle_id','vehicle__plate','vehicle__trailer_plate').distinct())
    filter_drivers=list(qs.order_by('driver__full_name','driver_id').values('driver_id','driver__full_name').distinct())
    selected_vehicle=request.GET.get('vehicle','')[:30]
    selected_driver=request.GET.get('driver','')[:30]
    date_from=request.GET.get('date_from','')[:20]
    date_to=request.GET.get('date_to','')[:20]
    filter_error=''
    for value,field in [(selected_vehicle,'vehicle_id'),(selected_driver,'driver_id')]:
        if value:
            if not value.isascii() or not value.isdigit() or len(value)>18:
                filter_error='Перевірте обрані фільтри.';qs=qs.none()
            else: qs=qs.filter(**{field:int(value)})
    from django.utils.dateparse import parse_date
    def read_filter_date(value):
        try: return parse_date(value) if value else None
        except ValueError: return None
    start,end=read_filter_date(date_from),read_filter_date(date_to)
    if (date_from and not start) or (date_to and not end):
        filter_error='Вкажіть коректні дати періоду.';qs=qs.none()
    elif start and end and start>end:
        filter_error='Дата «Від» має бути не пізніше дати «До».';qs=qs.none()
    else:
        if start: qs=qs.filter(planned_date__gte=start)
        if end: qs=qs.filter(planned_date__lte=end)
    query=request.GET.get("q","")[:100]
    if query: qs=qs.filter(Q(vehicle__plate__icontains=query)|Q(origin__icontains=query)|Q(destination__icontains=query)|Q(driver__full_name__icontains=query)|Q(ttn_number__icontains=query)|Q(stops__address__icontains=query)).distinct()
    status=request.GET.get("status","")
    if status=="missing":
        from .routes import signed_expression
        qs=qs.filter(status='completed').annotate(_has_signed_file=signed_expression()).filter(_has_signed_file=False)
    elif status in Trip.Status.values: qs=qs.filter(status=status)
    from .attention import filter_attention, CATEGORIES
    attention=request.GET.get('attention','')
    if attention:
        qs=filter_attention(qs,attention).order_by('-status_changed_at','-pk')
    sort_options={
        'vehicle':('vehicle__plate','-planned_date','-pk'),
        'vehicle_desc':('-vehicle__plate','-planned_date','-pk'),
        'driver':('driver__full_name','-planned_date','-pk'),
        'driver_desc':('-driver__full_name','-planned_date','-pk'),
    }
    sort=request.GET.get('sort','recent')
    if sort not in sort_options: sort='recent'
    if sort!='recent':
        if not attention:
            from django.db.models import Subquery
            recent_ids=qs.order_by('-planned_date','-pk').values('pk')[:200]
            qs=qs.filter(pk__in=Subquery(recent_ids))
        qs=qs.order_by(*sort_options[sort])
    from django.db.models import Prefetch
    qs = qs.prefetch_related(Prefetch('transfers', queryset=TripTransfer.objects.select_related('previous_driver').order_by('moved_at', 'pk'), to_attr='list_driver_transfers'))
    attention_page=Paginator(qs,50).get_page(request.GET.get('page')) if attention else None
    params=request.GET.copy();params.pop('page',None)
    # Bounded first release: most recent 200; reports support full selected period.
    trips=list(attention_page.object_list if attention_page else qs[:200])
    from .routes import trip_list_route_context, fleet_ttn_summary
    for trip in trips:
        trip.list_route=trip_list_route_context(trip)
        trip.ttn_summary=fleet_ttn_summary(trip)
        trip.list_has_ttn = any(document.kind in (Document.Kind.TTN, Document.Kind.SIGNED_TTN) and document.file for document in trip.documents.all())
    return render(request,"fleet/trip_list.html" if request.user.manages_fleet else "fleet/driver_trip_list.html",{"trips":trips,"statuses":Trip.Status.choices,"q":query,"selected":status,'sort':sort,'attention':attention,'attention_options':[(key,title) for key,title,hint,action in CATEGORIES],'attention_page':attention_page,'attention_query':params.urlencode(),'filter_vehicles':filter_vehicles,'filter_drivers':filter_drivers,'selected_vehicle':selected_vehicle,'selected_driver':selected_driver,'date_from':date_from,'date_to':date_to,'filter_error':filter_error})

@staff_required
def trip_create(request):
    initial = {"planned_date":timezone.localdate()}
    copy_from=None
    source_id=request.POST.get('copy_from') if request.method=='POST' else request.GET.get('copy_from')
    if source_id:
        try: source_pk=int(source_id)
        except (ValueError,TypeError): raise Http404
        if not 0<source_pk<=9223372036854775807: raise Http404
        copy_from=get_object_or_404(Trip.objects.select_related('vehicle','driver'),pk=source_pk)
        initial.update({field:getattr(copy_from,field) for field in ['origin','destination','origin_lat','origin_lon','destination_lat','destination_lon','loading_contact_name','loading_contact_phone','contact_name','contact_phone','cargo','planned_tons','notes']})
    if not copy_from and request.method == 'GET' and request.GET.get('vehicle', '').isdigit():
        selected = Vehicle.objects.filter(pk=request.GET['vehicle']).first()
        if selected:
            current_trip=selected.trips.select_related('driver').filter(status__in=Trip.ACTIVE).first()
            initial.update(vehicle=selected, driver=current_trip.driver if current_trip else selected.default_driver)
            if request.GET.get('queue') == '1' or selected.in_repair or current_trip:
                initial['scheduling']='queued'
    from .route_forms import create_formset
    route_mode=request.method=='GET' or 'stops-TOTAL_FORMS' in request.POST
    route_formset=create_formset(request.POST if request.method=='POST' else None,copy_from) if route_mode else None
    form_data=request.POST.copy() if request.method=='POST' else None
    route_valid=route_formset.is_valid() if request.method=='POST' and route_mode else True
    if request.method=='POST' and route_mode and route_valid:
        stops=[f.cleaned_data for f in route_formset.ordered_forms]
        first,last=stops[0],stops[-1]
        for target,source in [('origin',first),('destination',last)]:
            form_data[target]=source['address'][:180] if target=='origin' else source['address']
            for axis in ['lat','lon']: form_data[target+'_'+axis]=source.get(axis) if source.get(axis) is not None else ''
            form_data[target+'_point_confirmed']='on'
        for target,source in [('loading_contact',first),('contact',last)]:
            form_data[target+'_name']=source.get('contact_name','');form_data[target+'_phone']=source.get('contact_phone','')
        form_data['cargo']=', '.join(dict.fromkeys(d['cargo'] for d in stops if d['kind']=='load'))[:120]
        form_data['planned_tons']=str(sum(d['planned_tons'] for d in stops if d['kind']=='unload'))
        form_data['ttn_number']=''
    form=TripForm(form_data,initial=initial,location_reference=copy_from)
    instruction_forms=[];instruction_errors=[]
    if request.method=='POST':
        instruction_files=request.FILES.getlist('instructions')
        if len(instruction_files)>5 or sum(f.size for f in instruction_files)>40*1024*1024:
            instruction_errors.append('Оберіть до 5 файлів загальним розміром до 40 МБ.')
        else:
            for file in instruction_files:
                instruction=DocumentForm({'kind':'other'},{'file':file})
                if not instruction.is_valid():
                    instruction_errors.append(f'{Path(file.name).name}: '+ ' '.join(str(error) for values in instruction.errors.values() for error in values))
                elif instruction.detected_type not in ('application/pdf','image/jpeg','image/png','image/webp'):
                    instruction_errors.append(f'{Path(file.name).name}: оберіть PDF або фото.')
                else: instruction_forms.append(instruction)
    form_valid=form.is_valid() if request.method=='POST' else False
    if request.method=="POST" and route_valid and form_valid and not instruction_errors:
        saved_files=[]
        try:
            with transaction.atomic():
                vehicle=Vehicle.objects.select_for_update().get(pk=form.cleaned_data["vehicle"].pk)
                driver=User.objects.select_for_update().get(pk=form.cleaned_data["driver"].pk)
                queue_mode = form.cleaned_data.get('scheduling') == 'queued'
                if not queue_mode and not vehicle.trailer_id:
                    raise ValueError('Спочатку закріпіть причіп за тягачем або заплануйте рейс у чергу.')
                if (vehicle.in_repair and not queue_mode) or not driver.is_active or driver.role!="driver":
                    raise ValueError("Авто або водій зараз недоступні.")
                trip=form.save(commit=False); trip.created_by=request.user
                trip.status = 'queued' if queue_mode else 'planned'
                trip.save()
                from .driver_notifications import notify_assignment
                notify_assignment(trip)
                if route_mode: route_formset.save_route(trip,request.user)
                for instruction in instruction_forms:
                    doc=instruction.save(commit=False);doc.trip=trip;doc.kind=Document.Kind.INSTRUCTION;doc.uploaded_by=request.user
                    doc.original_name=Path(instruction.cleaned_data['file'].name).name[:250]
                    doc.content_type=instruction.detected_type;doc.size=instruction.cleaned_data['file'].size
                    saved_files.append(doc);doc.save()
                    event(request.user,'Додано інструкцію для водія',trip,doc.original_name)
                event(request.user,"Додано рейс у чергу" if queue_mode else "Створено рейс",trip,f'{trip.origin} → {trip.destination} · Водій: {driver.full_name} · Тягач: {vehicle.plate} · Причіп: {vehicle.trailer_plate}')
                if copy_from: event(request.user,'Скопійовано рейс',trip,f'Новий рейс створено на основі рейсу №{copy_from.pk}. Перенесено маршрут, контакти, вантаж, планову вагу та вказівки.')
            messages.success(request,format_html('{} <a href="{}#vehicle-{}">Переглянути авто →</a>','Рейс додано в чергу.' if queue_mode else 'Рейс створено.',reverse('dashboard'),trip.vehicle_id))
            return redirect("trip_detail",pk=trip.pk)
        except (IntegrityError,ValueError) as exc:
            for doc in saved_files:
                if doc.file and doc.file._committed: doc.file.delete(save=False)
            form.add_error(None,str(exc) if isinstance(exc,ValueError) else "Авто або водій уже мають активний рейс. Оновіть сторінку.")
        except Exception:
            for doc in saved_files:
                if doc.file and doc.file._committed: doc.file.delete(save=False)
            raise
    return render(request,"fleet/trip_create.html",{"form":form,"copy_from":copy_from,"route_formset":route_formset,'instruction_errors':instruction_errors})

@login_required
@require_GET
def trip_instruction(request,pk):
    doc=get_object_or_404(Document.objects.select_related('trip'),pk=pk,kind=Document.Kind.INSTRUCTION,trip__in=trips_for(request.user))
    if doc.content_type not in ('application/pdf','image/jpeg','image/png','image/webp'): raise Http404
    try: handle=doc.file.open('rb')
    except FileNotFoundError: raise Http404('Файл не знайдено.')
    response=FileResponse(handle,as_attachment=False,filename=doc.original_name,content_type=doc.content_type)
    response['Cache-Control']='private, no-store';response['X-Content-Type-Options']='nosniff'
    response['Content-Security-Policy']="default-src 'none'; frame-ancestors 'self'; sandbox"
    return response

@login_required
def trip_detail(request,pk):
    trip=get_object_or_404(trips_for(request.user),pk=pk)
    if request.user.role == 'driver':
        from .driver_views import driver_home
        return driver_home(request,trip)
    live_version=f'{trip.updated_at.isoformat()}|{trip.driver.rest_changed_at.isoformat()}'
    driver_delays=trip.delays.select_related('driver').order_by('-created_at','-pk')[:5] if request.user.manages_fleet else []
    if request.GET.get('live')=='1':
        response=JsonResponse({'version':live_version,'driver_messages_html':render_to_string('fleet/trip_driver_messages.html',{'driver_delays':driver_delays},request=request) if request.user.manages_fleet else ''})
        response['Cache-Control']='private, no-store'
        return response
    chain=list(Trip.Status.values[:5])
    next_status=chain[chain.index(trip.status)+1] if trip.status in chain[:-1] else None
    from .trip_stages import stage_progress, driver_stage_progress, LABELS
    history_page=Paginator(trip.events.select_related('actor').order_by('-created_at','-pk'),40).get_page(request.GET.get('history_page'))
    facts_form=TripFactsForm(instance=trip)
    from .quick_edit import editors
    quick_editors=editors(trip)
    from .routes import route_context, fleet_ttn_summary
    route_data=route_context(trip)
    route_data['trip_ttn_summary']=fleet_ttn_summary(trip)
    route_data['driver_steps']=driver_stage_progress(trip,route_data.get('driver_simple_route',True))
    route_data['trip_live_version']=live_version
    route_data['driver_delays']=driver_delays
    from .test_trip_delete import can_delete_test_trip
    route_data['can_delete_test_trip']=can_delete_test_trip(request.user)
    from .dispatcher_stages import DispatcherStageForm
    route_data['dispatcher_stage_form']=DispatcherStageForm(trip=trip)
    document_stop=trip.stops.filter(pk=request.GET.get('document_stop')).first() if request.GET.get('document_stop','').isdigit() else trip.current_stop
    upload_initial={'stop':document_stop}
    if document_stop: upload_initial['kind']='ttn' if document_stop.kind=='load' else 'signed_ttn'
    return render(request,"fleet/trip_detail.html",{**route_data,"trip":trip,"expenses":trip.expenses.select_related('driver'),"stage_steps":stage_progress(trip),'stage_options':[(key,LABELS[key]) for key in ['loading','road','unloading','completed'] if key!=trip.status],"upload_form":DocumentForm(trip=trip,initial=upload_initial),"assignment_form":AssignmentForm(initial={"driver":trip.driver}) if request.user.manages_fleet else None,"can_write":can_write(request.user,trip),"queue_block_reason":queue_block_reason(trip) if trip.status == "queued" else "","next_status":next_status,"next_label":dict(Trip.Status.choices).get(next_status),"quick_editors":quick_editors,"facts_form":facts_form,"events":history_page.object_list,"history_page":history_page})


@staff_required
def trip_facts(request,pk):
    if get_object_or_404(Trip,pk=pk).route_enabled: return redirect('trip_edit',pk=pk)
    if request.method=='POST':
        with transaction.atomic():
            trip=get_object_or_404(Trip.objects.select_for_update(),pk=pk)
            before={field:getattr(trip,field) for field in TripFactsForm.Meta.fields}
            form=TripFactsForm(request.POST,instance=trip)
            if form.is_valid():
                changed=[field for field in TripFactsForm.Meta.fields if before[field]!=form.cleaned_data[field]]
                if changed:
                    edited=form.save(commit=False)
                    edited.save(update_fields=changed+['updated_at'])
                    for field in changed:
                        old=before[field] if before[field] is not None and before[field]!='' else 'не вказано'
                        new=form.cleaned_data[field] if form.cleaned_data[field] is not None and form.cleaned_data[field]!='' else 'не вказано'
                        event(request.user,'Уточнено дані рейсу',trip,f'{form.fields[field].label}: {old} → {new}')
                    messages.success(request,'Вагу, пробіг і номер ТТН збережено. Зміни записані в історії рейсу.')
                else: messages.info(request,'Дані не змінилися.')
                return redirect('trip_detail',pk=pk)
    else:
        trip=get_object_or_404(Trip,pk=pk)
        form=TripFactsForm(instance=trip)
    return render(request,'fleet/trip_facts.html',{'trip':trip,'facts_form':form})


@staff_required
def trip_edit(request,pk):
    existing=get_object_or_404(Trip,pk=pk)
    if existing.route_enabled:
        from .route_forms import StopFormSet,RouteTripEditForm
        if request.method=='POST':
            with transaction.atomic():
                trip=get_object_or_404(Trip.objects.select_for_update(),pk=pk)
                before={field:getattr(trip,field) for field in RouteTripEditForm.Meta.fields}
                form=RouteTripEditForm(request.POST,instance=trip)
                route_formset=StopFormSet(request.POST,prefix='stops',trip=trip)
                form_valid=form.is_valid();route_valid=route_formset.is_valid()
                if form_valid and route_valid:
                    edited=form.save(commit=False)
                    changed=[f for f in before if before[f]!=getattr(edited,f)]
                    if changed:
                        edited.save(update_fields=changed+['updated_at'])
                        for field in changed: event(request.user,'Оновлено інформацію рейсу',trip,f'{form.fields[field].label}: {before[field]} → {getattr(edited,field)}')
                    route_changes=route_formset.save_route(edited,request.user)
                    if changed and not route_changes:
                        from .push import queue_trip_change
                        queue_trip_change(edited)
                    messages.success(request,'Маршрут і дані рейсу збережено. Водій побачить зміни автоматично.')
                    return redirect('trip_detail',pk=pk)
        else:
            trip=existing;form=RouteTripEditForm(instance=trip);route_formset=StopFormSet(prefix='stops',trip=trip)
        return render(request,'fleet/route_edit.html',{'trip':trip,'form':form,'route_formset':route_formset})
    if request.method=='POST':
        with transaction.atomic():
            trip=get_object_or_404(Trip.objects.select_for_update(),pk=pk)
            before={field:getattr(trip,field) for field in TripEditForm.Meta.fields}
            form=TripEditForm(request.POST,instance=trip)
            if form.is_valid():
                changed=[field for field in TripEditForm.Meta.fields if before[field]!=form.cleaned_data[field]]
                if changed:
                    edited=form.save(commit=False)
                    edited.save(update_fields=changed+['updated_at'])
                    from .push import queue_trip_change
                    queue_trip_change(edited)
                    for field in changed:
                        old=str(before[field] if before[field] is not None else '') or '—'
                        new=str(form.cleaned_data[field] if form.cleaned_data[field] is not None else '') or '—'
                        event(request.user,'Оновлено інформацію рейсу',trip,f'{form.fields[field].label}: {old[:650]} → {new[:650]}')
                    messages.success(request,'Рейс оновлено. Водій побачить зміни автоматично у відкритому кабінеті.')
                else: messages.info(request,'Інформація рейсу не змінилася.')
                return redirect('trip_detail',pk=pk)
    else:
        trip=get_object_or_404(Trip,pk=pk)
        form=TripEditForm(instance=trip)
    return render(request,'fleet/trip_edit.html',{'trip':trip,'form':form})

@login_required
@require_POST
def trip_status(request,pk):
    with transaction.atomic():
        trip=get_object_or_404(trips_for(request.user).select_for_update(),pk=pk)
        if not can_write(request.user,trip): raise PermissionDenied
        if request.user.manages_fleet and request.POST.get('revision'):
            try: revision=parse_datetime(request.POST['revision'])
            except ValueError: revision=None
            if revision!=trip.updated_at:
                messages.error(request,'Рейс уже змінився. Перевірте актуальні дані перед оновленням етапу.');return trip_response(request,pk)
        target=request.POST.get("status")
        if trip.route_enabled and target!='cancelled':
            messages.error(request,'Для цього рейсу змінюйте стан поточної точки маршруту.');return trip_response(request,pk)
        chain=list(Trip.Status.values[:5])
        allowed=[]
        if trip.status in chain[:-1]: allowed=[chain[chain.index(trip.status)+1]]
        if request.user.manages_fleet and trip.is_active: allowed=chain[1:]+["cancelled"]
        if request.user.manages_fleet and trip.status == 'queued': allowed=['cancelled']
        if target not in allowed:
            messages.error(request,"Цей перехід статусу недоступний."); return trip_response(request,pk)
        if request.user.role == 'driver' and trip.requires_acceptance:
            messages.error(request,'Спочатку прийміть рейс.'); return trip_response(request,pk)
        finish_form=None
        if request.user.role=='driver' and target=='completed':
            from .finish_details import DriverFinishForm,render_finish
            finish_form=DriverFinishForm(request.POST,trip=trip)
            if not finish_form.is_valid(): return render_finish(request,trip,request.POST,finish_form)
        previous_tons=trip.actual_tons
        tons=request.POST.get("actual_tons","").strip()
        if tons:
            try:
                value=Decimal(tons.replace(",","."))
                if not value.is_finite() or not Decimal("0.01")<=value<=60 or value.as_tuple().exponent < -3: raise ValueError()
                trip.actual_tons=value
            except Exception:
                messages.error(request,"Вага має бути від 0,01 до 60 т, до трьох знаків після коми."); return trip_response(request,pk)
        if request.user.role == 'driver' and target == 'completed' and not trip.has_signed_ttn:
            return render_finish(request,trip,request.POST,finish_form)
        if finish_form:
            from .finish_details import save_finish_details
            save_finish_details(trip,finish_form,request.user)
        if target=='cancelled':
            reason=request.POST.get('cancellation_reason','').strip()
            if len(reason)>500:
                messages.error(request,'Причина для водія має містити не більше 500 символів.');return trip_response(request,pk)
            trip.cancelled_at=timezone.now();trip.cancelled_by=request.user
            trip.cancellation_reason=reason;trip.cancellation_notice_required=True;trip.cancellation_acknowledged_at=None
            if trip.route_enabled: trip.stops.filter(state__in=['pending','arrived']).update(state='cancelled')
        previous=trip.status
        trip.status=target
        if target != previous: trip.status_changed_at=timezone.now()
        if target=="completed": trip.completed_at=timezone.now()
        trip.save()
        if target=='cancelled':
            from .push import enqueue
            enqueue(trip.driver_id,'cancelled',trip)
        if target=='completed':
            from .driver_reports import snapshot_completion_rates
            snapshot_completion_rates(trip)
            from .queue_promotion import promote_after_completion
            transaction.on_commit(lambda vehicle_id=trip.vehicle_id, driver_id=trip.driver_id: promote_after_completion(vehicle_id,driver_id),robust=True)
        if previous_tons!=trip.actual_tons: event(request.user,'Оновлено фактичну вагу',trip,f'{previous_tons if previous_tons is not None else "не вказано"} → {trip.actual_tons} т')
        event(request.user,"Змінено статус",trip,f"{dict(Trip.Status.choices)[previous]} → {trip.get_status_display()}")
        if request.user.role=='driver' and target!=previous:
            from .staff_trip_notifications import notify_staff
            transitions={'loading':('arrived_load',trip.origin),'road':('departed_load',trip.origin),'unloading':('arrived_unload',trip.destination),'completed':('departed_unload',trip.destination)}
            if target in transitions:
                kind,location=transitions[target];notify_staff(trip,request.user,kind,location)
        if target=='cancelled': event(request.user,'Скасовано рейс — очікуємо ознайомлення',trip,trip.cancellation_reason or 'Без пояснення для водія. Очікуємо підтвердження ознайомлення.')
    messages.success(request,'Рейс скасовано. Очікуємо підтвердження ознайомлення водія.' if target=='cancelled' else 'Статус оновлено. Документи можна додати після завершення рейсу.')
    return trip_response(request,pk)


@login_required
@require_POST
def trip_ack_cancel(request,pk):
    if request.user.role!='driver': raise PermissionDenied
    with transaction.atomic():
        trip=get_object_or_404(trips_for(request.user).select_for_update(),pk=pk)
        if trip.status!='cancelled' or not trip.cancellation_notice_required:
            messages.error(request,'Повідомлення про скасування недоступне.')
        elif not trip.cancellation_acknowledged_at:
            trip.cancellation_acknowledged_at=timezone.now()
            trip.save(update_fields=['cancellation_acknowledged_at','updated_at'])
            event(request.user,'Водій підтвердив скасування',trip)
            messages.success(request,'Ви підтвердили, що побачили скасування рейсу.')
    return redirect('dashboard')

@staff_required
@require_POST
def assign_driver(request,pk):
    from .transfers import transfer_trip
    form=AssignmentForm(request.POST)
    if form.is_valid():
        try:
            trip,queued,changed=transfer_trip(pk,form.cleaned_data['driver'].pk,request.user)
            if not changed: messages.info(request,'Цей водій уже призначений на рейс.')
            elif queued: messages.success(request,f'Рейс передано: {trip.driver.full_name}. Авто: {trip.vehicle}. Завдання поставлено наступним у черзі.')
            else: messages.success(request,f'Рейс передано: {trip.driver.full_name}. Авто: {trip.vehicle}. Новий водій має прийняти завдання.')
        except ValidationError as exc:
            messages.error(request,exc.messages[0])
        except IntegrityError:
            messages.error(request,'Водій або авто вже отримали інший рейс. Оновіть сторінку та повторіть передачу.')
    else: messages.error(request,'Оберіть доступного водія.')
    return trip_response(request,pk)


@login_required
@require_POST
def document_upload(request,pk):
    from .uploads import upload_documents
    return upload_documents(request,pk)


@staff_required
def driver_substitution(request,pk):
    from .transfers import substitute_driver
    trip=get_object_or_404(Trip.objects.select_related('vehicle','driver'),pk=pk)
    if trip.status not in Trip.ACTIVE:
        messages.error(request,'Підміна доступна лише для поточного рейсу.')
        return redirect('trip_detail',pk=pk)
    form=DriverSubstitutionForm(request.POST if request.method=='POST' else None,trip=trip)
    if request.method=='POST' and form.is_valid():
        try:
            substitute_driver(pk,form.cleaned_data['driver'].pk,request.user,form.cleaned_data['revision'],form.cleaned_data['effective_at'],form.cleaned_data['location'],form.cleaned_data['note'])
            messages.success(request,'Водія підмінено. Авто та етап рейсу збережено. Новий водій побачить завдання автоматично.')
            return redirect('trip_detail',pk=pk)
        except ValidationError as exc: form.add_error(None,exc)
        except IntegrityError: form.add_error(None,'Новий водій уже отримав інший рейс. Оновіть дані та виберіть вільного водія.')
    return render(request,'fleet/driver_substitution.html',{'trip':trip,'form':form})

@login_required
@require_GET
def document_download(request,pk):
    doc=get_object_or_404(Document.objects.select_related("trip"),pk=pk,trip__in=trips_for(request.user))
    try: handle=doc.file.open("rb")
    except FileNotFoundError: raise Http404("Файл не знайдено. Зверніться до адміністратора.")
    response=FileResponse(handle,as_attachment=True,filename=doc.original_name,content_type=doc.content_type)
    response["Cache-Control"]="private, no-store"
    response["X-Content-Type-Options"]="nosniff"
    return response

@staff_required
@require_POST
def original_received(request,pk):
    from django.http import HttpResponseGone
    return HttpResponseGone('Облік паперових оригіналів вимкнено.')

from .reports import report, report_edit

@login_required
def accounts(request):
    if not request.user.manages_users: raise PermissionDenied
    form=AccountForm(request.POST or None)
    temporary=None
    if request.method=="POST" and form.is_valid():
        user=form.save(commit=False);temporary=secrets.token_urlsafe(15);user.set_password(temporary);user.must_change_password=True;user.save()
        event(request.user,"Створено обліковий запис",detail=user.full_name)
        form=AccountForm()
    search = request.GET.get('q', '').strip()[:150]
    all_users = User.objects.all()
    counts = {'all': all_users.count(), 'active': all_users.filter(is_active=True).count(), 'inactive': all_users.filter(is_active=False).count(), 'admin': all_users.filter(role='admin').count(), 'moderator': all_users.filter(role='moderator').count(), 'driver': all_users.filter(role='driver').count()}
    state = request.GET.get('state', 'all')
    role = request.GET.get('role', 'all')
    if state not in ('all', 'active', 'inactive'): state = 'all'
    if role not in ('all', 'admin', 'moderator', 'driver'): role = 'all'
    users = all_users.prefetch_related('default_vehicles').order_by('role', 'full_name')
    if state != 'all': users = users.filter(is_active=state == 'active')
    if role != 'all': users = users.filter(role=role)
    if search:
        users = users.filter(Q(full_name__icontains=search) | Q(phone__icontains=search))
    from .session_activity import presence_for_accounts
    users = list(users)
    presence = presence_for_accounts(users)
    for account in users:
        account.presence = presence[account.pk]
    response=render(request,"fleet/accounts.html",{"users":users,"search":search,"counts":counts,"state":state,"selected_role":role,"vehicles":Vehicle.objects.select_related('default_driver'),"form":form,"temporary":temporary})
    response["Cache-Control"]="no-store"
    return response

@login_required
@require_GET
def accounts_presence(request):
    if not request.user.manages_users: raise PermissionDenied
    from .session_activity import presence_for_accounts
    presence = presence_for_accounts(User.objects.all())
    payload = {}
    for pk, status in presence.items():
        last = status['last_seen']
        payload[pk] = {'online': status['online'], 'last_seen': timezone.localtime(last).strftime('%d.%m.%Y %H:%M') if last else ''}
    response = JsonResponse({'users': payload})
    response['Cache-Control'] = 'no-store'
    return response

@login_required
def account_create(request):
    if not request.user.manages_users:
        raise PermissionDenied
    form = AccountCreateForm(request.POST or None)
    temporary = None
    invite_result = None
    if request.method == 'POST' and form.is_valid():
        from .models import MailSettings
        config = MailSettings.objects.filter(pk=1, enabled=True).first()
        if form.cleaned_data['access_method'] == 'invite' and not config:
            form.add_error('access_method', 'Спочатку налаштуйте та увімкніть SMTP.')
            return render(request, 'fleet/account_create.html', {'form': form})
        account = form.save(commit=False)
        temporary = secrets.token_urlsafe(15)
        account.set_password(temporary)
        account.must_change_password = True
        account.save()
        event(request.user, 'Створено обліковий запис', detail=account.full_name)
        if form.cleaned_data['access_method'] == 'invite':
            from django.contrib.auth.tokens import default_token_generator
            from django.utils.http import urlsafe_base64_encode
            from django.utils.encoding import force_bytes
            from .mail_service import send_mail
            link = config.site_url.rstrip('/') + reverse('account_invitation', args=[urlsafe_base64_encode(force_bytes(account.pk)), default_token_generator.make_token(account)])
            try:
                send_mail(config, account.email, 'Запрошення до M TRANS', f'Вітаємо, {account.full_name}!\n\nДля встановлення пароля відкрийте посилання: {link}\n\nЛогін — ваш номер телефону. Посилання одноразове та діє 3 доби.')
                invite_result = 'Запрошення надіслано.'
                temporary = None
            except Exception:
                invite_result = 'Лист не надіслано. Передайте тимчасовий пароль особисто або налаштуйте пошту.'
        form = AccountCreateForm()
    response = render(request, 'fleet/account_create.html', {'form': form, 'temporary': temporary, 'invite_result': invite_result})
    response['Cache-Control'] = 'no-store'
    return response

@login_required
@require_http_methods(['GET', 'POST'])
def account_detail(request, pk):
    if not request.user.manages_users:
        raise PermissionDenied
    account = get_object_or_404(User.objects.prefetch_related('default_vehicles'), pk=pk)
    if request.method == 'POST' and account.is_superuser and account.pk != request.user.pk:
        raise PermissionDenied
    form = AccountDetailsForm(request.POST if request.method == 'POST' else None, instance=account)
    if account.is_superuser and account.pk != request.user.pk:
        for field in form.fields.values():
            field.disabled = True
    if request.method == 'POST' and form.is_valid():
        changed_fields = form.changed_data
        if changed_fields:
            form.save()
            event(request.user, 'Змінено дані користувача', detail=f'{account.full_name} ({account.phone}): {", ".join(changed_fields)}')
        messages.success(request, 'Дані користувача збережено.')
        return redirect('account_detail', pk=account.pk)
    # Old subject records identify users by name; only use an unambiguous name.
    relevant = Q(actor=account) | Q(detail__startswith=f'{account.full_name} ({account.phone}):')
    if not User.objects.filter(full_name=account.full_name).exclude(pk=pk).exists():
        relevant |= Q(detail=account.full_name)
    history = AuditEvent.objects.filter(relevant).select_related('actor', 'trip').order_by('-created_at', '-pk')[:100]
    from .session_activity import valid_sessions
    from .models import UserSessionActivity
    account_sessions=valid_sessions(account, request.session.session_key)
    last_activity=UserSessionActivity.objects.filter(user=account).order_by('-last_seen_at').values_list('last_seen_at',flat=True).first()
    recent_actions=AuditEvent.objects.filter(actor=account).order_by('-created_at','-pk')[:6]
    response = render(request, 'fleet/account_detail.html', {'account': account, 'history': history, 'role_choices': User.Role.choices, 'form': form,'account_sessions':account_sessions,'last_activity':last_activity,'recent_actions':recent_actions})
    response['Cache-Control'] = 'no-store'
    return response

@admin_required
@require_POST
def account_sessions_end(request, pk):
    from django.contrib.sessions.models import Session
    from .session_activity import valid_sessions
    from .models import UserSessionActivity
    from django.utils.crypto import constant_time_compare
    account=get_object_or_404(User,pk=pk)
    if account.is_superuser and account.pk != request.user.pk: raise PermissionDenied
    choice=request.POST.get('session','')
    sessions=valid_sessions(account,request.session.session_key)
    selected=sessions if choice=='all' else [item for item in sessions if choice and constant_time_compare(item['token'],choice)]
    if choice!='all' and not selected: raise Http404
    if selected:
        with transaction.atomic():
            Session.objects.filter(session_key__in=[item['key'] for item in selected]).delete()
            event(request.user,'Завершено сесії користувача',detail=f'{account.full_name} ({account.phone}): {len(selected)}')
    if any(item['current'] for item in selected):
        from django.contrib.auth import logout
        logout(request)
        return redirect('login')
    messages.success(request,'Сесії завершено.' if selected else 'Активних сесій не знайдено.')
    return redirect('account_detail',pk=pk)

@staff_required
def vehicle_trailer_change(request, pk):
    with transaction.atomic():
        source_id=None
        selected_id=request.POST.get('trailer','') if request.method=='POST' else ''
        if selected_id.isascii() and selected_id.isdigit() and len(selected_id)<19:
            source_id=Vehicle.objects.filter(trailer_id=int(selected_id)).values_list('pk',flat=True).first()
        locked_ids=sorted({pk}|({source_id} if source_id else set()))
        locked={v.pk:v for v in Vehicle.objects.select_for_update().filter(pk__in=locked_ids).order_by('pk')}
        vehicle=locked.get(pk)
        if not vehicle: raise Http404
        old_plate=vehicle.trailer_plate
        form=TrailerChangeForm(request.POST or None,vehicle=vehicle)
        active=vehicle.trips.select_for_update().filter(status__in=Trip.ACTIVE).first()
        if request.method=='POST' and form.is_valid():
            selected=form.cleaned_data['trailer']
            current_source=Vehicle.objects.filter(trailer_id=selected.pk).values_list('pk',flat=True).first()
            if active:
                form.add_error(None,'Замінити причіп можна після завершення поточного рейсу.')
            elif current_source and current_source not in locked:
                form.add_error(None,'Закріплення причепа змінилося. Оновіть сторінку та повторіть дію.')
            elif current_source and current_source != vehicle.pk and locked[current_source].trips.filter(status__in=Trip.ACTIVE).exists():
                form.add_error('trailer','Причіп закріплений за тягачем у рейсі. Дочекайтеся завершення рейсу.')
            elif selected.pk != vehicle.trailer_id:
                try:
                    with transaction.atomic():
                        old_trailer=vehicle.trailer
                        source=locked.get(current_source)
                        if source and source.pk!=vehicle.pk:
                            source.trailer_plate=None; source.save(update_fields=['trailer_plate'])
                            TrailerTransfer.objects.create(trailer=selected,from_vehicle=source,to_vehicle=vehicle,actor=request.user,note=form.cleaned_data['note'])
                        else:
                            TrailerTransfer.objects.create(trailer=selected,to_vehicle=vehicle,actor=request.user,note=form.cleaned_data['note'])
                        if old_trailer:
                            TrailerTransfer.objects.create(trailer=old_trailer,from_vehicle=vehicle,actor=request.user,note=form.cleaned_data['note'])
                        vehicle.trailer_plate=selected.plate
                        vehicle.kind=selected.kind
                        vehicle.save(update_fields=['trailer_plate','kind'])
                        detail=f'Тягач {vehicle.plate}: причіп {old_plate or "не закріплено"} → {selected.plate}'
                        if source and source.pk!=vehicle.pk: detail+=f'. Переведено з тягача {source.plate}, який залишився без причепа'
                        if form.cleaned_data['note']: detail+=f'. {form.cleaned_data["note"]}'
                        event(request.user,'Змінено причіп',detail=detail)
                        queued=Trip.ordered_queue(vehicle.trips.filter(status='queued')).first()
                        if queued:
                            from .queue_promotion import promote_trip
                            transaction.on_commit(lambda trip_pk=queued.pk: promote_trip(trip_pk),robust=True)
                except IntegrityError:
                    form.add_error('trailer','Закріплення змінилося. Оновіть сторінку й спробуйте знову.')
                else:
                    messages.success(request,'Причіп змінено. Подію збережено в історії.')
                    return redirect(reverse('dashboard')+f'#vehicle-{vehicle.pk}')
            else:
                messages.info(request,'Номер причепа не змінився.')
                return redirect(reverse('dashboard')+f'#vehicle-{vehicle.pk}')
    return render(request,'fleet/trailer_change.html',{'vehicle':vehicle,'form':form,'active_trip':active,'old_trailer':old_plate})


@staff_required
def trailer_create(request):
    form=TrailerCreateForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        try:
            with transaction.atomic():
                trailer=form.save()
                event(request.user,'Додано причіп',detail=f'Причіп {trailer.plate}')
        except IntegrityError:
            form.add_error('plate','Причіп із таким номером уже існує.')
        else:
            messages.success(request,'Причіп додано до списку вільних.')
            vehicle_id=request.POST.get('vehicle','')
            if vehicle_id.isascii() and vehicle_id.isdigit() and len(vehicle_id)<19 and Vehicle.objects.filter(pk=int(vehicle_id)).exists():
                return redirect('vehicle_trailer_change',pk=int(vehicle_id))
            return redirect('dashboard')
    vehicle_id=request.POST.get('vehicle','') if request.method=='POST' else request.GET.get('vehicle','')
    if not (vehicle_id.isascii() and vehicle_id.isdigit() and len(vehicle_id)<19 and Vehicle.objects.filter(pk=int(vehicle_id)).exists()): vehicle_id=''
    return render(request,'fleet/trailer_create.html',{'form':form,'vehicle_id':vehicle_id})


@staff_required
def vehicle_edit(request, pk=None):
    with transaction.atomic():
        locked_ids=[]
        if pk and request.method=='POST' and request.POST.get('transfer_from_vehicle') and request.POST.get('default_driver','').isdigit():
            source_id=Vehicle.objects.filter(default_driver_id=request.POST['default_driver']).exclude(pk=pk).values_list('pk',flat=True).first()
            locked_ids=sorted({int(pk)} | ({source_id} if source_id else set()))
            locked={item.pk:item for item in Vehicle.objects.select_for_update().filter(pk__in=locked_ids).order_by('pk')}
            vehicle=locked.get(int(pk))
            if not vehicle: raise Http404
        else:
            vehicle = get_object_or_404(Vehicle.objects.select_for_update(), pk=pk) if pk else Vehicle()
        previous_driver = str(vehicle.default_driver) if vehicle.default_driver_id else 'Без водія'
        previous_driver_id = vehicle.default_driver_id
        initial = {}
        if not pk and request.GET.get('driver', '').isdigit():
            initial['default_driver'] = request.GET['driver']
        form = VehicleForm(request.POST or None, instance=vehicle, initial=initial)
        if request.method == 'POST' and form.is_valid():
            try:
                with transaction.atomic():
                    # Lock the target driver so simultaneous assignments serialize.
                    driver = form.cleaned_data.get('default_driver')
                    if driver:
                        locked_driver = User.objects.select_for_update().get(pk=driver.pk)
                        if locked_driver.role != User.Role.DRIVER or not locked_driver.is_active:
                            raise ValidationError('Водій уже неактивний або його роль змінилася. Оновіть сторінку.')
                    effective_at = form.cleaned_data.get('effective_at') or timezone.now()
                    note = form.cleaned_data.get('assignment_note', '')
                    queued=[]
                    if pk and previous_driver_id != (driver.pk if driver else None):
                        if request.POST.get('expect_free') == '1' and vehicle.trips.filter(status__in=Trip.ACTIVE).exists():
                            raise ValidationError('Авто вже виконує рейс. Для зміни водія в поточному рейсі скористайтеся підміною водія.')
                        if driver and driver.trips.filter(status__in=Trip.ACTIVE).exists():
                            raise ValidationError('Обраний водій уже виконує інший рейс. Дочекайтеся його завершення.')
                        queued=list(Trip.objects.select_for_update().filter(vehicle_id=vehicle.pk,status='queued').select_related('driver').order_by('pk'))
                        if any(request.POST.get(f'queue_choice_{item.pk}') not in ('keep','transfer') for item in queued):
                            raise ValidationError('Оберіть рішення для кожного рейсу в черзі.')
                        if not driver and any(request.POST.get(f'queue_choice_{item.pk}')=='transfer' for item in queued):
                            raise ValidationError('Щоб передати рейс, оберіть нового водія.')
                    if driver and form.cleaned_data.get('transfer_from_vehicle'):
                        source=Vehicle.objects.filter(default_driver=driver).exclude(pk=vehicle.pk).first()
                        if source:
                            if source.pk not in locked_ids: raise ValidationError('Закріплення змінилося. Оновіть сторінку і повторіть дію.')
                            source=locked[source.pk]
                            if vehicle.in_repair or source.in_repair or vehicle.trips.filter(status__in=Trip.ACTIVE).exists() or source.trips.filter(status__in=Trip.ACTIVE).exists() or driver.trips.filter(status__in=Trip.ACTIVE).exists():
                                raise ValidationError('Переведення доступне, коли обидва авто і водій вільні та не перебувають на ремонті.')
                            old_driver=source.default_driver_id
                            source.default_driver=None;source.save(update_fields=['default_driver'])
                            record_assignment(source,old_driver,effective_at,request.user,note or f'Переведено на {vehicle.plate}')
                            event(request.user,'Водія переведено на інше авто',detail=f'{driver.full_name}: {source.plate} → {vehicle.plate}. Рейси в черзі не змінені.')
                    saved = form.save()
                    record_assignment(saved, previous_driver_id, effective_at, request.user, note)
                    for item in queued:
                        if request.POST.get(f'queue_choice_{item.pk}') != 'transfer' or item.driver_id == driver.pk:
                            continue
                        old_driver=item.driver
                        from .driver_notifications import resolve_assignment
                        resolve_assignment(item,old_driver.pk)
                        item.driver=driver
                        item.accepted_at=None;item.accepted_by=None
                        item.save(update_fields=['driver','accepted_at','accepted_by','updated_at'])
                        from .driver_notifications import notify_assignment
                        notify_assignment(item)
                        TripTransfer.objects.create(trip=item,previous_driver=old_driver,next_driver=driver,from_vehicle=saved,to_vehicle=saved,previous_status='queued',next_status='queued',moved_by=request.user)
                        event(request.user,'Змінено водія',item,f'{old_driver.full_name} → {driver.full_name}. Рейс залишається в черзі та очікує прийняття нового водія.')
                    event(request.user, 'Оновлено закріплення авто' if pk else 'Додано авто',
                          detail=f'{saved.plate} / {saved.trailer_plate} · {previous_driver} → {saved.default_driver or "Без водія"} · {timezone.localtime(effective_at):%d.%m.%Y %H:%M} · {note}')
                if pk:
                    message='Водія змінено.' if previous_driver_id != saved.default_driver_id else 'Дані авто збережено.'
                    messages.success(request,format_html('{} <a href="{}#vehicle-{}">Переглянути авто →</a>',message,reverse('dashboard'),saved.pk))
                else:
                    messages.success(request,'Авто додано.')
                if pk:
                    return redirect('vehicle_history', pk=saved.pk)
                return redirect('accounts')
            except IntegrityError:
                form.add_error(None, 'Цей номер або водій уже використовується. Оновіть сторінку й перевірте закріплення.')
            except ValidationError as exc:
                form.add_error(None, exc)
    queued_trips=list(Trip.ordered_queue(vehicle.trips.filter(status='queued').select_related('driver'))) if pk else []
    for queued in queued_trips:
        queued.choice=request.POST.get(f'queue_choice_{queued.pk}','')
    return render(request, 'fleet/vehicle_form.html', {'form': form, 'vehicle': vehicle, 'editing': bool(pk),'queued_trips':queued_trips,'vehicle_busy':bool(pk and vehicle.trips.filter(status__in=Trip.ACTIVE).exists())})

@staff_required
@require_GET
def vehicle_history(request, pk):
    vehicle = get_object_or_404(Vehicle.objects.select_related('default_driver'), pk=pk)
    return render(request, 'fleet/vehicle_history.html', {'vehicle':vehicle,
        'assignments':vehicle.assignments.select_related('recorded_by', 'ended_by'),
        'trailer_history':TrailerTransfer.objects.filter(Q(from_vehicle=vehicle)|Q(to_vehicle=vehicle)).select_related('trailer','from_vehicle','to_vehicle','actor'),
        'repairs':vehicle.repairs.select_related('created_by','finished_by'),'trips':Trip.objects.filter(Q(vehicle=vehicle)|Q(transfers__from_vehicle=vehicle)|Q(transfers__to_vehicle=vehicle)).distinct().select_related('driver','vehicle').order_by('-planned_date','-id')[:100], 'transfers':TripTransfer.objects.filter(Q(from_vehicle=vehicle)|Q(to_vehicle=vehicle)).select_related('previous_driver','next_driver','from_vehicle','to_vehicle','moved_by')})

@login_required
@require_POST
def account_action(request,pk):
    if not request.user.manages_users: raise PermissionDenied
    user=get_object_or_404(User,pk=pk)
    if user.pk==request.user.pk or user.is_superuser:
        raise PermissionDenied
    action=request.POST.get("action")
    if action in ('reset_attempts', 'email_password', 'email_invite'):
        from .models import LoginThrottle, MailSettings
        import hashlib
        if action == 'reset_attempts':
            key = hashlib.sha256(('phone:' + user.phone).encode()).hexdigest()
            LoginThrottle.objects.filter(key=key).delete()
            event(request.user, 'Скинуто спроби входу', detail=f'{user.full_name} ({user.phone}): телефон')
            messages.success(request, 'Спроби входу за номером скинуто. Спільне обмеження за IP залишається чинним.')
            return redirect('account_detail', pk=pk)
        config = MailSettings.objects.filter(pk=1, enabled=True).first()
        if not user.is_active or not user.email or not config or (action == 'email_invite' and not config.site_url):
            messages.error(request, 'Для надсилання потрібні активний обліковий запис, збережена пошта користувача та налаштована SMTP-пошта; для посилання також потрібна адреса сайту.')
            return redirect('account_detail', pk=pk)
        from .mail_service import send_mail
        try:
            with transaction.atomic():
                target = User.objects.select_for_update().get(pk=pk)
                if not target.is_active or not target.email:
                    raise ValueError('Account unavailable')
                if action == 'email_password':
                    temporary = secrets.token_urlsafe(15)
                    target.set_password(temporary)
                    target.must_change_password = True
                    target.save(update_fields=['password', 'must_change_password'])
                    send_mail(config, target.email, 'Тимчасовий пароль M TRANS', f'Вітаємо, {target.full_name}!\n\nВаш тимчасовий пароль: {temporary}\nЛогін — номер телефону {target.phone}. Після входу змініть пароль.')
                    audit_action = 'Надіслано тимчасовий пароль'
                else:
                    from django.contrib.auth.tokens import default_token_generator
                    from django.utils.http import urlsafe_base64_encode
                    from django.utils.encoding import force_bytes
                    link = config.site_url.rstrip('/') + reverse('account_invitation', args=[urlsafe_base64_encode(force_bytes(target.pk)), default_token_generator.make_token(target)])
                    send_mail(config, target.email, 'Посилання для встановлення пароля M TRANS', f'Вітаємо, {target.full_name}!\n\nДля встановлення пароля відкрийте посилання: {link}\n\nЛогін — номер телефону {target.phone}. Посилання одноразове та діє 3 доби.')
                    audit_action = 'Надіслано посилання для пароля'
                event(request.user, audit_action, detail=f'{target.full_name} ({target.phone})')
        except Exception:
            messages.error(request, 'Лист не надіслано. Перевірте SMTP та адресу користувача; пароль не змінено.')
            return redirect('account_detail', pk=pk)
        messages.success(request, 'Лист успішно надіслано на збережену електронну адресу користувача.')
        return redirect('account_detail', pk=pk)
    temporary=None
    with transaction.atomic():
        from .models import DriverPermissionSettings
        DriverPermissionSettings.objects.get_or_create(pk=1)
        DriverPermissionSettings.objects.select_for_update().get(pk=1)
        actor = User.objects.filter(pk=request.user.pk, is_active=True).first()
        if not actor or not actor.manages_users:
            raise PermissionDenied
        user=User.objects.select_for_update().get(pk=pk)
        if action=="reset":
            temporary=secrets.token_urlsafe(15);user.set_password(temporary);user.must_change_password=True;user.save()
            # Existing sessions are invalidated by Django's password hash check.
            event(request.user,"Скинуто пароль",detail=user.full_name)
        elif action=="toggle":
            if user.is_active and user.role == User.Role.ADMIN and User.objects.filter(role=User.Role.ADMIN, is_active=True).count() <= 1:
                messages.error(request, 'Потрібно зберегти хоча б одного активного адміністратора.')
            elif user.is_active and user.trips.filter(status__in=Trip.ACTIVE+['queued']).exists():
                messages.error(request,"Спочатку передайте поточні рейси й рейси в черзі іншому водію.")
            else:
                user.is_active=not user.is_active;user.save(update_fields=["is_active"])
                event(request.user,"Змінено активність користувача",detail=user.full_name)
        else: raise PermissionDenied
    if temporary:
        response=render(request,"fleet/temporary_password.html",{"target":user,"temporary":temporary});response["Cache-Control"]="no-store";return response
    return redirect("accounts")

@staff_required
def vehicle_repair(request,pk):
    with transaction.atomic():
        vehicle=get_object_or_404(Vehicle.objects.select_for_update(),pk=pk)
        repair=vehicle.repairs.select_for_update().filter(finished_at__isnull=True).first()
        closing=vehicle.in_repair
        initial={('finished_at' if closing else 'started_at'):timezone.localtime().replace(second=0,microsecond=0)}
        form_class=RepairFinishForm if closing else RepairForm
        form=form_class(request.POST if request.method=='POST' else None, request.FILES if request.method=='POST' else None,initial=initial)
        if request.method=='POST' and form.is_valid():
            if request.POST.get('operation', 'finish' if closing else 'start') != ('finish' if closing else 'start'):
                form.add_error(None,'Стан ремонту вже змінився. Оновіть сторінку.')
            elif closing:
                finished=form.cleaned_data['finished_at']
                if not repair:
                    form.add_error(None,'Історія ремонту не знайдена. Оновіть сторінку.')
                elif repair.started_at and finished<repair.started_at:
                    form.add_error('finished_at','Завершення раніше початку ремонту.')
                else:
                    repair.finished_at=finished;repair.result=form.cleaned_data['result']
                    repair.amount=form.cleaned_data['amount']
                    for name in ('invoice', 'act'):
                        if form.cleaned_data[name]: setattr(repair, name, form.cleaned_data[name])
                    repair.finished_by=request.user;repair.finished_recorded_at=timezone.now();repair.save()
                    vehicle.in_repair=False;vehicle.save(update_fields=['in_repair'])
                    event(request.user,'Завершено ремонт',detail=f'{vehicle.plate}: {repair.result}')
                    messages.success(request,'Авто повернуто в роботу. Ремонт збережено в історії.')
                    return redirect('vehicle_history',pk=pk)
            elif vehicle.trips.filter(status__in=Trip.ACTIVE).exists():
                form.add_error(None,'Спочатку завершіть або скасуйте поточний рейс. Рейси в черзі можна залишити.')
            elif vehicle.repairs.filter(finished_at__gt=form.cleaned_data['started_at']).exists():
                form.add_error('started_at','Дата перетинається з попереднім ремонтом.')
            else:
                VehicleRepair.objects.create(vehicle=vehicle,created_by=request.user,**form.cleaned_data)
                vehicle.in_repair=True;vehicle.save(update_fields=['in_repair'])
                event(request.user,'Відправлено на ремонт',detail=f"{vehicle.plate}: {form.cleaned_data['reason']}")
                messages.success(request,'Авто на ремонті. Причина та дата записані в історію.')
                return redirect('vehicle_history',pk=pk)
    return render(request,'fleet/vehicle_repair.html',{'vehicle':vehicle,'repair':repair,'form':form,'closing':closing})

@staff_required
@require_GET
def repair_document_download(request, pk, kind):
    if kind not in ('invoice', 'act'): raise Http404
    repair=get_object_or_404(VehicleRepair, pk=pk)
    file=getattr(repair, kind)
    if not file: raise Http404
    try:
        handle=file.open('rb')
    except (OSError, ValueError): raise Http404
    response=FileResponse(handle, as_attachment=True, filename=f'{kind}-{repair.pk}{Path(file.name).suffix.lower()}', content_type='application/octet-stream')
    response['Cache-Control']='private, no-store'
    response['X-Content-Type-Options']='nosniff'
    return response

@staff_required
@require_http_methods(['GET', 'POST'])
def repair_costs_edit(request, pk):
    repair=get_object_or_404(VehicleRepair.objects.select_related('vehicle'), pk=pk, finished_at__isnull=False)
    form=RepairCostsForm(request.POST if request.method=='POST' else None, request.FILES if request.method=='POST' else None, initial={'amount':repair.amount})
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            repair=VehicleRepair.objects.select_for_update().get(pk=pk, finished_at__isnull=False)
            repair.amount=form.cleaned_data['amount']
            for name in ('invoice','act'):
                if form.cleaned_data[name]: setattr(repair,name,form.cleaned_data[name])
            repair.save(update_fields=['amount','invoice','act'])
            event(request.user,'Змінено витрати ремонту',detail=f'{repair.vehicle.plate}: ремонт №{repair.pk}, сума {repair.amount if repair.amount is not None else "не вказана"} грн')
        messages.success(request,'Витрати й документи ремонту збережено.')
        return redirect('vehicle_history',pk=repair.vehicle_id)
    return render(request,'fleet/repair_costs_edit.html',{'repair':repair,'form':form})

@staff_required
@require_GET
def repair_report(request):
    import csv
    from django.db.models import Count
    from django.utils.dateparse import parse_date
    raw_start=request.GET.get('start','')[:20]; raw_end=request.GET.get('end','')[:20]
    vehicle=request.GET.get('vehicle','')[:20]
    def parse(value):
        try: return parse_date(value) if value else None
        except ValueError: return None
    start,end=parse(raw_start),parse(raw_end)
    repairs=VehicleRepair.objects.filter(finished_at__isnull=False).select_related('vehicle','finished_by').order_by('-finished_at','-pk')
    error=''
    if (raw_start and not start) or (raw_end and not end) or (start and end and start>end):
        error='Перевірте дати періоду.';repairs=repairs.none()
    else:
        if start: repairs=repairs.filter(finished_at__date__gte=start)
        if end: repairs=repairs.filter(finished_at__date__lte=end)
    if vehicle:
        if vehicle.isascii() and vehicle.isdigit() and len(vehicle)<=18: repairs=repairs.filter(vehicle_id=int(vehicle))
        else: error='Перевірте вибраний автомобіль.';repairs=repairs.none()
    totals=repairs.aggregate(total=Sum('amount'),count=Count('pk'),unknown=Count('pk',filter=Q(amount__isnull=True)))
    if request.GET.get('format')=='csv' and not error:
        response=HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition']='attachment; filename="repair-report.csv"'
        response.write('\ufeff')
        writer=csv.writer(response,delimiter=';')
        writer.writerow(['Авто','Причіп','Причина','Завершення','Результат','Витрати, грн','Рахунок','Акт'])
        def safe(value):
            value=str(value if value is not None else '')
            return "'"+value if value.lstrip().startswith(('=','+','-','@','\t','\r')) else value
        for repair in repairs.iterator(chunk_size=100):
            writer.writerow([safe(v) for v in (repair.vehicle.plate,repair.vehicle.trailer_plate,repair.reason,timezone.localtime(repair.finished_at).strftime('%d.%m.%Y %H:%M'),repair.result,str(repair.amount).replace('.',',') if repair.amount is not None else '', 'Так' if repair.invoice else 'Ні','Так' if repair.act else 'Ні')])
        return response
    query=request.GET.copy();query.pop('page',None);query.pop('format',None)
    page=Paginator(repairs,50).get_page(request.GET.get('page'))
    return render(request,'fleet/repair_report.html',{'page':page,'totals':totals,'vehicles':Vehicle.objects.order_by('plate'),'selected_vehicle':vehicle,'start':raw_start,'end':raw_end,'error':error,'filter_query':query.urlencode()})

@require_GET
def health(request):
    try:
        with connection.cursor() as cursor: cursor.execute("SELECT 1"); cursor.fetchone()
    except Exception: return JsonResponse({"status":"unavailable"},status=503)
    return JsonResponse({"status":"ok"})
