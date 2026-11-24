"""Promote the next queued trip when both resources and queue order allow it."""
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from .models import AuditEvent, Trip, User, Vehicle


def promote_trip(pk):
    from .views import queue_block_reason
    initial=Trip.objects.filter(pk=pk,status='queued').values('vehicle_id').first()
    if not initial: return False
    try:
        with transaction.atomic():
            Vehicle.objects.select_for_update().get(pk=initial['vehicle_id'])
            trip=Trip.objects.select_related('vehicle','driver','accepted_by').select_for_update(of=('self',)).filter(pk=pk,status='queued',vehicle_id=initial['vehicle_id']).first()
            if not trip: return False
            User.objects.select_for_update().get(pk=trip.driver_id)
            # A trip intentionally kept with a driver after their vehicle
            # assignment ended still needs a dispatcher decision. A different
            # driver explicitly assigned before completion is otherwise valid.
            assigned=trip.vehicle.default_driver_id
            if assigned != trip.driver_id:
                last_transfer=trip.transfers.filter(next_driver_id=trip.driver_id,to_vehicle_id=trip.vehicle_id).order_by('-moved_at','-pk').values_list('moved_at',flat=True).first()
                since=max(trip.created_at,last_transfer) if last_transfer else trip.created_at
                from .assignment_history import AUTO_COMPLETION_NOTE
                ended=trip.vehicle.assignments.filter(driver_id=trip.driver_id,ended_at__isnull=False).exclude(ended_note__startswith=AUTO_COMPLETION_NOTE).filter(Q(ended_recorded_at__gte=since)|Q(ended_recorded_at__isnull=True,ended_at__gte=since))
                if ended.exists(): return False
            if queue_block_reason(trip): return False
            trip.status='planned'
            if trip.route_enabled:
                current=trip.current_stop
                if current and current.state=='arrived': trip.status='loading' if current.kind=='load' else 'unloading'
                elif trip.stops.filter(state='completed').exists(): trip.status='road'
            trip.queue_priority_at=None;trip.status_changed_at=timezone.now()
            trip.save(update_fields=['status','queue_priority_at','status_changed_at','updated_at'])
            AuditEvent.objects.create(trip=trip,action='Рейс автоматично переведено з черги',detail='Авто й водій вільні; рейс став поточним.')
            if not trip.is_accepted:
                from .driver_notifications import resolve_assignment, notify_assignment
                resolve_assignment(trip)
                notify_assignment(trip)
            return True
    except IntegrityError:
        return False


def promote_ready():
    ids=Trip.ordered_queue(Trip.objects.filter(status='queued')).values_list('pk',flat=True)
    for pk in list(ids): promote_trip(pk)

def promote_after_completion(vehicle_id,driver_id):
    """Check queues affected by the freed vehicle or driver, in queue order."""
    candidates=Trip.objects.filter(status='queued').filter(Q(vehicle_id=vehicle_id)|Q(driver_id=driver_id))
    for pk in list(Trip.ordered_queue(candidates).values_list('pk',flat=True)):
        promote_trip(pk)
