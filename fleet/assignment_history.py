"""Assignment periods are separate from trip ownership and document access."""
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from .models import VehicleAssignment

AUTO_COMPLETION_NOTE = 'Автоматично після завершення рейсу'

def assign_driver_after_completion(trip, actor):
    """Keep the latest trip driver assigned; preserve both vehicles' history."""
    from .models import AuditEvent, User, Vehicle
    try:
        with transaction.atomic():
            User.objects.select_for_update().get(pk=trip.driver_id)
            source_id=Vehicle.objects.filter(default_driver_id=trip.driver_id).exclude(pk=trip.vehicle_id).values_list('pk',flat=True).first()
            ids=sorted({trip.vehicle_id} | ({source_id} if source_id else set()))
            vehicles={vehicle.pk:vehicle for vehicle in Vehicle.objects.select_for_update().filter(pk__in=ids).order_by('pk')}
            target=vehicles[trip.vehicle_id]
            source=vehicles.get(source_id)
            if source and source.default_driver_id!=trip.driver_id:
                raise ValidationError('Закріплення водія змінилося одночасно. Потрібна перевірка логіста.')
            now=timezone.now()
            note=f'{AUTO_COMPLETION_NOTE} №{trip.pk}'
            if source:
                source.default_driver=None;source.save(update_fields=['default_driver'])
                record_assignment(source,trip.driver_id,now,actor,note)
                AuditEvent.objects.create(actor=actor,trip=trip,action='Автоматично змінено закріплення',detail=f'{source.plate}: водія переведено на {target.plate} після рейсу №{trip.pk}.')
            previous=target.default_driver_id
            if previous!=trip.driver_id:
                target.default_driver_id=trip.driver_id;target.save(update_fields=['default_driver'])
                record_assignment(target,previous,now,actor,note)
                AuditEvent.objects.create(actor=actor,trip=trip,action='Автоматично змінено закріплення',detail=f'{target.plate}: закріплено водія {trip.driver.full_name} після рейсу №{trip.pk}.')
            return True
    except (ValidationError, IntegrityError):
        AuditEvent.objects.create(actor=actor,trip=trip,action='Не вдалося автоматично закріпити водія',detail='Рейс завершено. Логісту потрібно перевірити закріплення авто та водія.')
        return False


def record_assignment(vehicle, previous_driver_id, effective_at, actor=None, note=''):
    """Call inside a transaction, with the vehicle and target driver locked."""
    if previous_driver_id == vehicle.default_driver_id:
        return
    if effective_at > timezone.now():
        raise ValidationError('Дата пересадки не може бути в майбутньому.')
    current = vehicle.assignments.select_for_update().filter(ended_at__isnull=True).first()
    if current and current.driver_id != previous_driver_id:
        raise ValidationError('Закріплення вже змінилося. Оновіть сторінку.')
    if current and current.started_at and effective_at < current.started_at:
        raise ValidationError('Дата пересадки раніша за початок поточного закріплення.')
    if vehicle.assignments.filter(ended_at__gt=effective_at).exists():
        raise ValidationError('Дата пересадки перетинається з уже записаною історією цього авто.')
    if vehicle.default_driver_id and VehicleAssignment.objects.filter(driver_id=vehicle.default_driver_id).exclude(vehicle=vehicle).filter(ended_at__isnull=True).exists():
        raise ValidationError('Водій має незакрите закріплення за іншим авто.')
    if vehicle.default_driver_id and VehicleAssignment.objects.filter(driver_id=vehicle.default_driver_id, ended_at__gt=effective_at).exists():
        raise ValidationError('На цю дату водій ще був закріплений за іншим авто. Перевірте дату передачі.')
    if current:
        current.ended_at = effective_at
        current.ended_note = note
        current.ended_by = actor
        current.ended_recorded_at = timezone.now()
        current.save(update_fields=['ended_at', 'ended_note', 'ended_by', 'ended_recorded_at'])
    elif previous_driver_id:
        # Covers older imports without inventing a historical start date.
        from .models import User
        previous = User.objects.get(pk=previous_driver_id)
        VehicleAssignment.objects.create(vehicle=vehicle, driver=previous, driver_name=previous.full_name,
            tractor_plate=vehicle.plate, trailer_plate=vehicle.trailer_plate or '',
            ended_at=effective_at, note='Початкове закріплення; дата початку невідома.',
            ended_note=note, ended_by=actor, ended_recorded_at=timezone.now())
    if vehicle.default_driver_id:
        VehicleAssignment.objects.create(vehicle=vehicle, driver=vehicle.default_driver,
            driver_name=vehicle.default_driver.full_name, tractor_plate=vehicle.plate,
            trailer_plate=vehicle.trailer_plate or '', started_at=effective_at, note=note, recorded_by=actor)
