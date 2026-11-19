from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from .models import Trip, Vehicle, User, TripTransfer


def transfer_trip(pk, driver_id, actor):
    """Move ownership and its vehicle together, preserving historical links."""
    from .views import event
    with transaction.atomic():
        initial=get_object_or_404(Trip,pk=pk)
        current=Trip.objects.filter(driver_id=driver_id,status__in=Trip.ACTIVE).exclude(pk=pk).first()
        target_id=current.vehicle_id if current else Vehicle.objects.filter(default_driver_id=driver_id).values_list('pk',flat=True).first() or initial.vehicle_id
        vehicles={vehicle.pk:vehicle for vehicle in Vehicle.objects.select_for_update().filter(pk__in=[initial.vehicle_id,target_id]).order_by('pk')}
        trip=get_object_or_404(Trip.objects.select_for_update(),pk=pk)
        driver=get_object_or_404(User.objects.select_for_update(),pk=driver_id)
        if not driver.is_active or driver.role!='driver': raise ValidationError('Водій недоступний.')
        if trip.status not in Trip.ACTIVE+['queued']: raise ValidationError('Завершений або скасований рейс не можна передати.')
        if trip.driver_id==driver.pk: return trip,False,False
        if trip.vehicle_id!=initial.vehicle_id: raise ValidationError('Авто рейсу вже змінилося. Повторіть передачу з актуальної сторінки.')
        current=Trip.objects.filter(driver=driver,status__in=Trip.ACTIVE).exclude(pk=pk).first()
        actual_target=current.vehicle_id if current else Vehicle.objects.filter(default_driver=driver).values_list('pk',flat=True).first() or trip.vehicle_id
        if actual_target!=target_id: raise ValidationError('У нового водія змінилося авто. Оновіть сторінку та повторіть передачу.')
        target=vehicles[target_id]
        busy=bool(current or target.in_repair or Trip.objects.filter(vehicle=target,status__in=Trip.ACTIVE).exclude(pk=pk).exists())
        previous_driver=trip.driver
        previous_vehicle=vehicles[trip.vehicle_id]
        previous_status=trip.status
        now=timezone.now()
        from .driver_notifications import resolve_assignment
        resolve_assignment(trip,previous_driver.pk)
        trip.driver=driver;trip.vehicle=target
        trip.accepted_at=None;trip.accepted_by=None
        if busy:
            trip.status='queued'
            trip.queue_priority_at=now
        elif trip.status=='queued':
            trip.queue_priority_at=None
        trip.status_changed_at=now
        trip.save(update_fields=['driver','vehicle','accepted_at','accepted_by','status','queue_priority_at','status_changed_at','updated_at'])
        from .driver_notifications import notify_assignment
        notify_assignment(trip)
        TripTransfer.objects.create(trip=trip,previous_driver=previous_driver,next_driver=driver,from_vehicle=previous_vehicle,to_vehicle=target,previous_status=previous_status,next_status=trip.status,moved_by=actor,moved_at=now)
        detail=f'{previous_driver.full_name} → {driver.full_name}. Авто: {previous_vehicle} → {target}. {dict(Trip.Status.choices)[previous_status]} → {trip.get_status_display()}.'
        if busy: detail+=' Рейс поставлено наступним у черзі.'
        event(actor,'Змінено водія',trip,detail)
        return trip,busy,True


def substitute_driver(pk, driver_id, actor, expected_revision, effective_at=None, location='', note=''):
    """A handover changes the driver, retaining the vehicle, cargo and stage."""
    from .views import event
    with transaction.atomic():
        initial=get_object_or_404(Trip,pk=pk)
        vehicle=Vehicle.objects.select_for_update().get(pk=initial.vehicle_id)
        trip=get_object_or_404(Trip.objects.select_for_update(),pk=pk)
        driver=get_object_or_404(User.objects.select_for_update(),pk=driver_id)
        if trip.updated_at!=expected_revision or trip.vehicle_id!=vehicle.pk:
            raise ValidationError('Рейс уже змінився. Відкрийте підміну знову, щоб побачити актуальні дані.')
        if trip.status not in Trip.ACTIVE: raise ValidationError('Підміна доступна лише для поточного рейсу.')
        if not driver.is_active or driver.role!='driver': raise ValidationError('Водій недоступний.')
        if trip.driver_id==driver.pk: raise ValidationError('Цей водій уже виконує рейс.')
        if Trip.objects.filter(driver=driver,status__in=Trip.ACTIVE).exclude(pk=pk).exists():
            raise ValidationError('Цей водій уже виконує інший рейс. Спочатку завершіть або передайте його поточний рейс.')
        now=timezone.now();effective=effective_at or now
        last=trip.transfers.first()
        earliest=last.effective_time if last else trip.created_at
        if effective>now or effective<earliest:
            raise ValidationError('Час підміни має бути після створення рейсу й останньої передачі та не в майбутньому.')
        previous=trip.driver
        from .driver_notifications import resolve_assignment
        resolve_assignment(trip,previous.pk)
        trip.driver=driver;trip.accepted_at=None;trip.accepted_by=None;trip.status_changed_at=now
        trip.save(update_fields=['driver','accepted_at','accepted_by','status_changed_at','updated_at'])
        from .driver_notifications import notify_assignment
        notify_assignment(trip)
        transfer=TripTransfer.objects.create(kind='substitution',trip=trip,previous_driver=previous,next_driver=driver,from_vehicle=vehicle,to_vehicle=vehicle,previous_status=trip.status,next_status=trip.status,moved_by=actor,moved_at=now,effective_at=effective,location=location,note=note)
        detail=f'{previous.full_name} → {driver.full_name}. Тягач / причіп: {vehicle}. Етап: {trip.get_status_display()}. Підміна: {timezone.localtime(effective):%d.%m.%Y %H:%M}. Місце: {location or "не вказано"}. {note}'
        event(actor,'Підміна водія',trip,detail[:1500])
        return transfer
