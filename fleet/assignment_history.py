"""Assignment periods are separate from trip ownership and document access."""
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import VehicleAssignment


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
