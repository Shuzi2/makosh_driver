"""Shared stage names and progress for dispatcher and driver screens."""
STAGES=[
    ('planned','Призначено','Рейс призначено'),
    ('accepted','Прийнято','Рейс прийнято'),
    ('loading','Завантаження','Завантажуюсь'),
    ('road','У дорозі','У дорозі'),
    ('unloading','Розвантаження','Розвантажуюсь'),
    ('completed','Завершено','Рейс завершено'),
]
LABELS={key:label for key,short,label in STAGES}


def driver_stage_progress(trip, simple_route=True):
    """Display confirmed stages; repeated stops have their own operation cycle."""
    if not trip or trip.status == 'cancelled':
        return []
    stages=[(key,short) for key,short,_ in STAGES]
    current='planned' if trip.requires_acceptance else 'accepted' if trip.status in ['planned','queued'] else trip.status
    if trip.route_enabled and not simple_route:
        stop=trip.current_stop
        operation='Завантаження' if stop and stop.kind == 'load' else 'Розвантаження'
        stages=[('planned','Призначено'),('accepted','Прийнято'),('road','Дорога до точки'),
                ('operation',operation),('stop_done','Точку виконано'),('completed','Рейс завершено')]
        if not trip.requires_acceptance and stop and stop.state == 'arrived':
            current='operation'
    keys=[key for key,_ in stages]
    index=keys.index(current) if current in keys else -1
    return [{'key':key,'label':label,'current':i == index,'done':i < index}
            for i,(key,label) in enumerate(stages)]

def driver_journey_progress(trip, simple_route=True):
    """Show a compact journey only when it accurately represents the route."""
    if not trip or trip.status in ('cancelled','queued'): return []
    if not simple_route: return driver_stage_progress(trip,False)
    stages=[('loading','Завантаження'),('road','Дорога'),('unloading','Розвантаження'),('completed','Завершення')]
    key=None if trip.requires_acceptance else ('loading' if trip.status=='planned' else trip.status)
    keys=[item[0] for item in stages]
    index=keys.index(key) if key in keys else -1
    return [{'key':item_key,'label':label,'current':i==index,'done':index>=0 and i<index,
             'current_label':'Далі' if trip.status=='planned' else 'Зараз'} for i,(item_key,label) in enumerate(stages)]

def elapsed_label(start, now=None):
    from django.utils import timezone
    if not start: return ''
    minutes=max(0,int(((now or timezone.now())-start).total_seconds()//60))
    days, remainder=divmod(minutes,1440)
    hours, mins=divmod(remainder,60)
    if days: return f'{days} дн {hours} год'
    if hours: return f'{hours} год {mins} хв'
    return f'{mins} хв' if mins else 'щойно'

def status_label(trip):
    if trip.requires_acceptance: return 'Очікує прийняття'
    if trip.route_enabled and trip.is_active and trip.current_stop:
        stop=trip.current_stop
        if stop.state=='arrived': return 'Завантаження' if stop.kind=='load' else 'Розвантаження'
    if trip.status=='planned': return LABELS['accepted'] if trip.is_accepted else LABELS['planned']
    return LABELS.get(trip.status,{'queued':'У черзі','cancelled':'Рейс скасовано'}.get(trip.status,trip.status))

def stage_progress(trip):
    keys=[key for key,_,_ in STAGES]
    current='accepted' if trip and trip.status=='planned' and trip.is_accepted else trip.status if trip else None
    index=keys.index(current) if current in keys else -1
    return [{'label':short,'key':key,'current':key==current,'done':bool(trip and (trip.is_accepted if key=='accepted' else key=='planned' and trip.status!='cancelled' or i<=index))} for i,(key,short,label) in enumerate(STAGES)]
