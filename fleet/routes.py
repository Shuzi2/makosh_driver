"""Ordered operations and projections shared by driver, dispatcher and reports."""
from decimal import Decimal
from django.db.models import Exists, OuterRef, Q, Case, When, BooleanField
from django.utils import timezone
from .models import Trip, TripStop, Document, AuditEvent


def signed_expression():
    legacy=Exists(Document.objects.filter(trip_id=OuterRef('pk'),kind='signed_ttn'))
    missing=Exists(TripStop.objects.filter(trip_id=OuterRef('pk'),kind='unload',signed_complete=False).exclude(state='cancelled'))
    has_unload=Exists(TripStop.objects.filter(trip_id=OuterRef('pk'),kind='unload').exclude(state='cancelled'))
    return Case(When(route_enabled=True,then=has_unload & ~missing),default=legacy,output_field=BooleanField())


def refresh_signed(stop):
    docs=list(Document.objects.filter(stop=stop,kind='signed_ttn').values_list('ttn_number',flat=True))
    expected=stop.ttn_numbers
    signed=bool(docs) and (not expected or all(number in docs for number in expected))
    if stop.signed_complete!=signed:
        stop.signed_complete=signed;stop.save(update_fields=['signed_complete'])


def route_document_status(trip,points=None,documents=None):
    """Check each live operation and every expected TTN number separately."""
    points=list(trip.stops.all()) if points is None else points
    rows=documents if documents is not None else Document.objects.filter(trip=trip,stop__isnull=False).values_list('stop_id','kind','ttn_number')
    documents={}
    for stop_id,kind,number in rows:
        documents.setdefault((stop_id,kind),set()).add(number)
    for point in points:
        point.ttn_upload_kind='ttn' if point.kind=='load' else 'signed_ttn'
        numbers=documents.get((point.pk,point.ttn_upload_kind),set())
        point.ttn_has_file=bool(numbers)
        point.missing_ttn_numbers=[number for number in point.ttn_numbers if number not in numbers]
        point.ttn_complete=bool(numbers) and not point.missing_ttn_numbers
    return points


def fleet_ttn_summary(trip):
    """Summarize uploaded loading and signed unloading TTNs for a vehicle card."""
    if not trip:
        return []
    documents=list(trip.documents.all())
    if not trip.route_enabled:
        return [{'label': label, 'state': 'complete' if any(doc.kind==kind for doc in documents) else 'missing',
                 'text': 'Завантажено' if any(doc.kind==kind for doc in documents) else 'Не завантажено'}
                for kind,label in [('ttn','ТТН завантаження'),('signed_ttn','Підписана ТТН розвантаження')]]
    points=[point for point in trip.stops.all() if point.state!='cancelled']
    rows=[(doc.stop_id,doc.kind,doc.ttn_number) for doc in documents if doc.stop_id]
    route_document_status(trip,points,rows)
    result=[]
    for kind,label,doc_kind in [('load','ТТН завантаження','ttn'),('unload','Підписана ТТН розвантаження','signed_ttn')]:
        group=[point for point in points if point.kind==kind]
        if not group:
            result.append({'label':label,'state':'neutral','text':'Немає точки'})
            continue
        complete=sum(point.ttn_complete for point in group)
        any_file=any(stop_id in {point.pk for point in group} and doc_type==doc_kind for stop_id,doc_type,_ in rows)
        state='complete' if complete==len(group) else 'partial' if any_file else 'missing'
        text='Завантажено' if state=='complete' else 'Не завантажено' if state=='missing' else 'Частково'
        if len(group)>1:
            text+=f' · {complete}/{len(group)} точок'
        result.append({'label':label,'state':state,'text':text})
    return result


def sync_trip(trip):
    """Trip fields are searchable/reportable projections, never independent route totals."""
    stops=list(trip.stops.exclude(state='cancelled').order_by('position','pk'))
    if not stops: return
    loads=[s for s in stops if s.kind=='load'];unloads=[s for s in stops if s.kind=='unload']
    first,last=stops[0],stops[-1]
    trip.origin=first.address[:180];trip.origin_lat=first.lat;trip.origin_lon=first.lon
    trip.destination=last.address;trip.destination_lat=last.lat;trip.destination_lon=last.lon
    trip.loading_contact_name=first.contact_name;trip.loading_contact_phone=first.contact_phone
    trip.contact_name=last.contact_name;trip.contact_phone=last.contact_phone
    trip.cargo=', '.join(dict.fromkeys(s.cargo for s in loads))[:120]
    trip.planned_tons=sum((s.planned_tons for s in unloads),Decimal('0'))
    trip.actual_tons=sum((s.actual_tons for s in unloads),Decimal('0')) if unloads and all(s.actual_tons is not None for s in unloads) else None
    trip.ttn_number=', '.join(dict.fromkeys(n for s in stops for n in s.ttn_numbers))[:100]
    trip.route_enabled=True
    trip.save(update_fields=['origin','origin_lat','origin_lon','destination','destination_lat','destination_lon','loading_contact_name','loading_contact_phone','contact_name','contact_phone','cargo','planned_tons','actual_tons','ttn_number','route_enabled','updated_at'])
    if hasattr(trip,'_prefetched_objects_cache'): trip._prefetched_objects_cache.pop('stops',None)


def route_context(trip):
    if not trip or not trip.route_enabled: return {}
    points=route_document_status(trip,list(trip.stops.all()))
    current=next((s for s in points if s.state in ['pending','arrived']),None) if trip.status not in ['completed','cancelled'] else None
    remaining=[s for s in points if s.state in ['pending','arrived']]
    action='';mode=''
    if current:
        mode='depart' if current.state=='arrived' else 'arrive'
        action=('Завантажився — далі' if current.kind=='load' else 'Розвантажився — далі') if mode=='depart' else ('Приїхав на завантаження' if current.kind=='load' else 'Приїхав на розвантаження')
        if mode=='depart' and len(remaining)==1: action='Розвантажився — завершити рейс'
    simple=trip.is_simple_route
    displayed=[s for s in points if s.state!='cancelled' and (simple or s.state!='completed')]
    if current and current in displayed:
        displayed=[current]+[s for s in displayed if s.pk!=current.pk]
    return {'driver_simple_route':simple,'driver_display_stops':displayed,'driver_completed_stops':[s for s in points if s.state=='completed'] if not simple else [],'route_stops':points,'missing_ttn_stops':[s for s in points if s.state!='cancelled' and not s.ttn_complete],'current_stop':current,'next_stop':remaining[1] if len(remaining)>1 else None,'route_done_count':sum(s.state=='completed' for s in points),'route_stop_count':sum(s.state!='cancelled' for s in points),'stop_mode':mode,'stop_action':action,'route_last_stop':len(remaining)==1,'route_balance':trip.route_balance}


def route_reminder_context(trip,pending_tons=''):
    context=route_context(trip)
    stop=context['current_stop']
    if not context['route_last_stop']:
        context['missing_ttn_stops']=[s for s in context['missing_ttn_stops'] if s.pk==stop.pk]
    return {'trip':trip,'stop':stop,'pending_tons':pending_tons,**context}


def fleet_route_context(trip):
    if not trip or not trip.route_enabled: return {}
    stops=[s for s in trip.stops.all() if s.state!='cancelled']
    current=next((s for s in stops if s.state in ['pending','arrived']),None)
    if not current: return {}
    if trip.requires_acceptance:
        label='Очікує прийняття';heading='Перша точка'
    elif current.state=='arrived':
        label='На завантаженні' if current.kind=='load' else 'На розвантаженні';heading='Зараз у точці'
    elif trip.status=='road':
        label='Їде на завантаження' if current.kind=='load' else 'Їде на розвантаження';heading='Прямує до точки'
    else:
        label='Завантаження' if current.kind=='load' else 'Розвантаження';heading='Наступна точка'
    return {'origin_place':stops[0].driver_place,'destination_place':stops[-1].driver_place,'current':current,'heading':heading,'label':label,'progress':trip.route_progress,'count':len(stops),'simple':len(stops)==2 and stops[0].kind=='load' and stops[1].kind=='unload','points':[{'stop':s,'number':i,'current':s.pk==current.pk,'label':'Виконано' if s.state=='completed' else label if s.pk==current.pk else 'Попереду'} for i,s in enumerate(stops,1)]}


def trip_list_route_context(trip):
    stops=list(trip.stops.all()) if trip.route_enabled else []
    live=[s for s in stops if s.state!='cancelled']
    shown=live or stops
    current=trip.current_stop if trip.status in Trip.ACTIVE+['queued'] and trip.route_enabled else None
    points=[{'address':s.address,'kind':s.kind,'kind_label':s.get_kind_display(),'state':s.state,'completed_at':s.completed_at,'cargo':s.cargo,'tons':s.planned_tons,'current':bool(current and s.pk==current.pk)} for s in shown]
    if not points:
        points=[{'address':address,'kind':kind,'kind_label':label,'state':'completed' if trip.status=='completed' else 'cancelled' if trip.status=='cancelled' else 'pending','current':False} for address,kind,label in [(trip.origin,'load','Завантаження'),(trip.destination,'unload','Розвантаження')]]
    progress=fleet_route_context(trip) if current else {}
    # Existing structured settlement names are preferred; older trips can
    # still show the first part of their stored address without losing it.
    def short_place(address, stop=None):
        return ((stop.settlement_name if stop else '') or (address or '').split(',', 1)[0]).strip()
    origin = short_place(points[0]['address'], shown[0] if shown else None)
    destination = short_place(points[-1]['address'], shown[-1] if shown else None)
    return {'points':points,'origin_short':origin,'destination_short':destination,'count':len(points),'simple':len(points)==2 and points[0]['kind']=='load' and points[1]['kind']=='unload','loads':sum(p['kind']=='load' for p in points),'unloads':sum(p['kind']=='unload' for p in points),'cancelled':[s for s in stops if s.state=='cancelled'] if live else [],'current':progress}


def audit(actor,trip,action,detail):
    AuditEvent.objects.create(actor=actor,trip=trip,action=action,detail=detail[:1500])


def notify_route(trip,summary):
    if trip.status in Trip.ACTIVE+['queued']:
        previous=trip.route_notice if not trip.route_notice_acknowledged_at else ''
        trip.route_notice=(previous+'\n'+summary).strip()[-2000:]
        trip.route_notice_acknowledged_at=None
        trip.save(update_fields=['route_notice','route_notice_acknowledged_at','updated_at'])
        from .push import queue_trip_change
        queue_trip_change(trip)
