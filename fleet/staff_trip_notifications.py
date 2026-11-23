from .models import StaffTripNotification, User
from .push import enqueue_staff, staff_can_receive

LABELS={
    'accepted':'Водій прийняв рейс',
    'arrived_load':'Прибув на завантаження',
    'departed_load':'Завершив завантаження',
    'arrived_unload':'Прибув на розвантаження',
    'departed_unload':'Завершив розвантаження',
}


def notify_staff(trip,driver,kind,location=''):
    if kind not in LABELS: raise ValueError('Невідома подія рейсу')
    location=(location or '')[:250]
    users=User.objects.filter(is_active=True,role__in=['admin','moderator'])
    StaffTripNotification.objects.bulk_create([
        StaffTripNotification(recipient=user,trip=trip,driver=driver,kind=kind,location=location)
        for user in users if staff_can_receive(user,'staff_trip_event')
    ])
    details=f'{driver.full_name} · рейс №{trip.pk} · {trip.vehicle.plate}: {LABELS[kind].lower()}'
    if location: details+=f' · {location}'
    enqueue_staff('staff_trip_event',trip,details)
