"""Promote accepted queued trips only when both resources and queue order allow it."""
from django.db import IntegrityError, transaction
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
            # An accepted trip kept with the previous driver needs a deliberate
            # dispatcher decision once the vehicle has been reassigned.
            assigned=trip.vehicle.default_driver_id
            if assigned and assigned != trip.driver_id: return False
            if not assigned and trip.vehicle.assignments.filter(driver_id=trip.driver_id,ended_at__isnull=False).exists(): return False
            if not trip.is_accepted or queue_block_reason(trip): return False
            trip.status='planned'
            if trip.route_enabled:
                current=trip.current_stop
                if current and current.state=='arrived': trip.status='loading' if current.kind=='load' else 'unloading'
                elif trip.stops.filter(state='completed').exists(): trip.status='road'
            trip.queue_priority_at=None;trip.status_changed_at=timezone.now()
            trip.save(update_fields=['status','queue_priority_at','status_changed_at','updated_at'])
            AuditEvent.objects.create(trip=trip,action='Рейс автоматично переведено з черги',detail='Авто й водій вільні; водій прийняв рейс.')
            return True
    except IntegrityError:
        return False


def promote_ready():
    ids=Trip.ordered_queue(Trip.objects.filter(status='queued',accepted_at__isnull=False)).values_list('pk',flat=True)
    for pk in list(ids): promote_trip(pk)
