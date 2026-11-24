from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404,redirect,render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from .models import Trip,TripStop
from .routes import audit,sync_trip,route_context


@login_required
@require_POST
def stop_action(request,pk):
    from .views import trips_for,trip_response
    with transaction.atomic():
        trip=get_object_or_404(trips_for(request.user).select_for_update(),pk=pk,route_enabled=True)
        try:
            revision=parse_datetime(request.POST.get('expected_revision',request.POST.get('revision','')))
        except ValueError: revision=None
        if revision!=trip.updated_at:
            messages.error(request,'Маршрут уже змінився. Відкрийте актуальні дані.');return trip_response(request,pk)
        if not trip.is_active or (request.user.role=='driver' and trip.requires_acceptance):
            messages.error(request,'Спочатку прийміть і почніть рейс.');return trip_response(request,pk)
        stop=trip.current_stop
        if not stop or str(stop.pk)!=request.POST.get('stop_id'):
            messages.error(request,'Ця точка вже не є поточною. Перевірте маршрут.');return trip_response(request,pk)
        mode=request.POST.get('stop_mode')
        if mode=='arrive' and stop.state=='pending':
            stop.state='arrived';stop.arrived_at=timezone.now();stop.save(update_fields=['state','arrived_at'])
            trip.status='loading' if stop.kind=='load' else 'unloading'
            audit(request.user,trip,'Прибув до точки маршруту',f'Точка {stop.position}: {stop.get_kind_display()}, {stop.address}')
            if request.user.role=='driver':
                from .staff_trip_notifications import notify_staff
                notify_staff(trip,request.user,'arrived_load' if stop.kind=='load' else 'arrived_unload',stop.address)
        elif mode=='depart' and stop.state=='arrived':
            final=not trip.stops.filter(state__in=['pending','arrived']).exclude(pk=stop.pk).exists()
            finish_form=None
            if request.user.role=='driver' and final:
                from .finish_details import DriverFinishForm,render_finish
                finish_form=DriverFinishForm(request.POST,trip=trip)
                if not finish_form.is_valid(): return render_finish(request,trip,request.POST,finish_form)
            value=request.POST.get('actual_tons','').strip()
            if request.user.role=='driver' and stop.kind=='load' and not value and stop.actual_tons is None:
                messages.error(request,'Вкажіть фактичну вагу завантаження перед виїздом.');return trip_response(request,pk)
            if value:
                from decimal import Decimal,InvalidOperation
                try:
                    tons=Decimal(value.replace(',','.'))
                    if not tons.is_finite() or not Decimal('0.01')<=tons<=60 or tons.as_tuple().exponent < -3: raise ValueError()
                except (ValueError,InvalidOperation):
                    messages.error(request,'Вага цієї операції має бути від 0,01 до 60 т, до трьох знаків після коми.');return trip_response(request,pk)
            else: tons=stop.actual_tons
            if request.user.role=='driver' and (final or request.POST.get('confirm_without_signed_ttn')!='1'):
                from .routes import route_reminder_context
                reminder=route_reminder_context(trip,value)
                if (reminder['route_last_stop'] or stop.kind=='unload') and reminder['missing_ttn_stops']:
                    if final: return render_finish(request,trip,request.POST,finish_form)
                    return render(request,'fleet/route_finish_reminder.html',reminder)
            if finish_form:
                from .finish_details import save_finish_details
                for field,point in finish_form.weight_points:
                    if point.pk == stop.pk:
                        tons=finish_form.cleaned_data[field]
                        continue
                    before_weight=point.actual_tons;point.actual_tons=finish_form.cleaned_data[field]
                    if before_weight!=point.actual_tons:
                        point.weight_revision+=1;point.save(update_fields=['actual_tons','weight_revision'])
                        audit(request.user,trip,'Фактична вага точки',f'Точка {point.position}, {point.address}: {before_weight if before_weight is not None else "—"} → {point.actual_tons} т')
                save_finish_details(trip,finish_form,request.user)
            before=stop.actual_tons;stop.actual_tons=tons
            if before!=tons: stop.weight_revision+=1
            stop.state='completed';stop.completed_at=timezone.now();stop.completed_by=request.user;stop.save(update_fields=['actual_tons','weight_revision','state','completed_at','completed_by'])
            if before!=tons: audit(request.user,trip,'Фактична вага точки',f'Точка {stop.position}, {stop.address}: {before if before is not None else "—"} → {tons} т')
            audit(request.user,trip,'Виконано точку маршруту',f'Точка {stop.position}: {stop.get_kind_display()}, {stop.address}')
            if request.user.role=='driver':
                from .staff_trip_notifications import notify_staff
                notify_staff(trip,request.user,'departed_load' if stop.kind=='load' else 'departed_unload',stop.address)
            remaining=trip.stops.filter(state__in=['pending','arrived']).exists()
            trip.status='road' if remaining else 'completed'
            if not remaining: trip.completed_at=timezone.now()
        else:
            messages.error(request,'Цю дію вже виконано або вона недоступна.');return trip_response(request,pk)
        trip.status_changed_at=timezone.now();trip.save(update_fields=['status','completed_at','status_changed_at','updated_at'])
        if trip.status=='completed':
            from .assignment_history import assign_driver_after_completion
            if not assign_driver_after_completion(trip,request.user):
                messages.warning(request,'Рейс завершено, але не вдалося оновити закріплення водія. Повідомте логіста.')
            from .driver_reports import snapshot_completion_rates
            snapshot_completion_rates(trip)
            from .queue_promotion import promote_after_completion
            transaction.on_commit(lambda vehicle_id=trip.vehicle_id, driver_id=trip.driver_id: promote_after_completion(vehicle_id,driver_id),robust=True)
        sync_trip(trip)
        if trip.status=='completed': audit(request.user,trip,'Завершено рейс','Усі точки маршруту виконано.')
    return trip_response(request,pk)


@login_required
@require_POST
def acknowledge_route(request,pk):
    from .views import trips_for
    if request.user.role!='driver': raise PermissionDenied
    with transaction.atomic():
        trip=get_object_or_404(trips_for(request.user).select_for_update(),pk=pk)
        try: revision=parse_datetime(request.POST.get('expected_revision',''))
        except ValueError: revision=None
        if revision!=trip.updated_at:
            messages.error(request,'Маршрут змінився. Перегляньте актуальні дані перед підтвердженням.');return redirect('dashboard')
        if trip.route_notice and not trip.route_notice_acknowledged_at:
            trip.route_notice_acknowledged_at=timezone.now();trip.save(update_fields=['route_notice_acknowledged_at','updated_at'])
            audit(request.user,trip,'Водій ознайомився зі змінами маршруту','Водій підтвердив поточну версію маршруту.')
    return redirect('dashboard')
