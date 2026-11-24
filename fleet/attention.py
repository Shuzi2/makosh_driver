"""One definition of unresolved dispatcher issues for cards and filtered lists."""
from functools import reduce
from operator import or_
from django.db.models import Q, F, Exists, OuterRef, Case, When, Value, IntegerField
from .models import Trip, Document, TripTransfer, TripStop, DriverEconomicsDefaults

REPORT_FIELDS = [('price','Ціна рейсу'),('daily_rate','Ставка водія'),('days','Кількість днів'),('bonus_per_km','Бонус водія'),('fuel_price','Ціна пального'),('adblue_price','Ціна AdBlue'),('adblue_rate','Витрата AdBlue'),('depreciation_per_km','Амортизація'),('credit_per_km','Кредит'),('extra_cost','Додаткові витрати')]


def issue_snapshot(queryset=None):
    """Return current issues; skips belong to an occurrence, not to a whole trip."""
    import hashlib
    from django.db.models import Subquery, Prefetch
    from .routes import route_document_status
    from .models import AttentionSkip
    base = filter_attention(queryset if queryset is not None else Trip.objects.all(), 'all').select_related('vehicle','driver','economics').prefetch_related('stops',Prefetch('documents',queryset=Document.objects.only('trip_id','stop_id','kind','ttn_number'),to_attr='attention_documents')).annotate(
        latest_transfer=Subquery(TripTransfer.objects.filter(trip_id=OuterRef('pk')).order_by('-pk').values('pk')[:1]),
    )
    skips = {(s.trip_id,s.category,s.episode):s for s in AttentionSkip.objects.select_related('skipped_by')}
    issues=[]
    defaults=DriverEconomicsDefaults.objects.filter(pk=1).first() or DriverEconomicsDefaults()
    for trip in base:
        points=route_document_status(trip,list(trip.stops.all()),documents=[(d.stop_id,d.kind,d.ttn_number) for d in trip.attention_documents]) if trip.route_enabled else []
        document_checks=[{'stop':p,'complete':p.ttn_complete,'missing_numbers':p.missing_ttn_numbers,'label':p.get_kind_display(),'document_label':'ТТН' if p.kind=='load' else 'Підписана ТТН'} for p in points if p.state!='cancelled']
        keys=[]
        if trip.status=='cancelled' and trip.cancellation_notice_required and not trip.cancellation_acknowledged_at: keys.append('cancellation')
        if trip.status=='planned' and not trip.is_accepted: keys.append('acceptance')
        missing_report=[]
        if trip.status=='completed':
            from .driver_reports import economics_initial
            from .models import TripEconomics
            try: values={field:getattr(trip.economics,field) for field,_ in REPORT_FIELDS}
            except TripEconomics.DoesNotExist: values=economics_initial(trip,None,defaults)
            missing_report=[label for field,label in REPORT_FIELDS if values.get(field) is None]
            if trip.distance_km is None: missing_report.append('Пробіг')
            if trip.fuel_used_l is None: missing_report.append('Витрачене пальне')
            if missing_report: keys.append('report')
        if trip.status=='completed' and not trip.route_enabled:
            if not trip.attention_signed: keys.append('documents')
        candidates=[(key,title,hint,action,None) for key,title,hint,action in CATEGORIES if key in keys]
        if trip.route_enabled and trip.status=='completed':
            for point in trip.stops.all():
                if point.kind!='unload' or point.state!='completed': continue
                for key,title,hint,action in CATEGORIES:
                    if key=='documents' and not point.signed_complete:
                        candidates.append((key,title,hint,action,point))
        for key,title,hint,action,point in candidates:
            if key=='acceptance': identity=(trip.driver_id,trip.latest_transfer)
            elif key=='cancellation': identity=(trip.driver_id,trip.cancelled_at)
            else: identity=(trip.completed_at,)
            if point: identity=(point.pk,point.completed_at,point.documents_revision)
            episode=hashlib.sha256(repr((trip.pk,key,identity)).encode()).hexdigest()
            since=(trip.cancelled_at if key=='cancellation' else trip.completed_at if key in ['weight','documents','report'] else trip.created_at) or trip.created_at
            if point: since=point.completed_at or trip.completed_at or trip.created_at
            record=skips.get((trip.pk,key,episode))
            version=f'{record.pk}:{record.skipped_at.isoformat()}:{record.restored_at.isoformat() if record.restored_at else ""}' if record else ''
            issues.append({'trip':trip,'key':key,'title':title,'hint':hint,'action':action,'episode':episode,'since':since,'skip':record if record and not record.restored_at else None,'skip_version':version,'stop':point,'simple_route':trip.is_simple_route if points else True,'document_checks':document_checks,'missing_report':missing_report, 'legacy_document_checks':[{'label':label,'complete':any(d.kind==kind for d in trip.attention_documents)} for kind,label in [('ttn','ТТН завантаження'),('signed_ttn','Підписана ТТН розвантаження')]] if not trip.route_enabled else []})
    return sorted(issues,key=lambda item:(item['since'],item['trip'].pk,item['key']))

CATEGORIES=[
    ('cancellation','Не підтверджено скасування','Водій ще не натиснув «Зрозуміло».','Переглянути'),
    ('acceptance','Рейси ще не прийняті','Поточні рейси без підтвердження водія.','Відкрити рейс'),
    ('documents','Немає підписаної ТТН','Завершені рейси, для яких очікуємо документ.','Додати ТТН'),
    ('report','Не заповнено звіт рейсу','Завершені рейси з неповними даними для розрахунку.','Заповнити звіт'),
]

def annotated(queryset):
    from .routes import signed_expression
    common=[field for field,_ in REPORT_FIELDS if field!='depreciation_per_km']
    missing_saved=reduce(or_,(Q(**{f'economics__{field}__isnull':True}) for field,_ in REPORT_FIELDS))
    missing_default=reduce(or_,(Q(**{f'{field}__isnull':True}) for field in common))
    defaults=DriverEconomicsDefaults.objects.filter(pk=1)
    return queryset.annotate(
        attention_signed=signed_expression(),
        attention_stop_documents=Exists(TripStop.objects.filter(trip_id=OuterRef('pk'),kind='unload',state='completed',signed_complete=False)),
        attention_transferred=Exists(TripTransfer.objects.filter(trip_id=OuterRef('pk'))),
        report_defaults_exist=Exists(defaults),
        report_defaults_missing=Exists(defaults.filter(missing_default)),
        report_tent_missing=Exists(defaults.filter(depreciation_tent__isnull=True)),
        report_grain_missing=Exists(defaults.filter(depreciation_grain__isnull=True)),
    )

def conditions():
    missing_saved=reduce(or_,(Q(**{f'economics__{field}__isnull':True}) for field,_ in REPORT_FIELDS))
    kind_tent=Q(completed_vehicle_kind='tent') | Q(completed_vehicle_kind__isnull=True,vehicle__kind='tent')
    kind_grain=Q(completed_vehicle_kind='grain') | Q(completed_vehicle_kind__isnull=True,vehicle__kind='grain')
    missing_default=Q(report_defaults_exist=False)|Q(report_defaults_missing=True)|(kind_tent & Q(report_tent_missing=True))|(kind_grain & Q(report_grain_missing=True))|(~kind_tent & ~kind_grain)
    return {
        'cancellation':Q(status='cancelled',cancellation_notice_required=True,cancellation_acknowledged_at__isnull=True),
        'acceptance':Q(status='planned') & (Q(accepted_at__isnull=True)|~Q(accepted_by_id=F('driver_id'))),
        'documents':Q(status='completed') & (Q(route_enabled=False,attention_signed=False)|Q(route_enabled=True,attention_stop_documents=True)),
        'report':Q(status='completed') & (Q(distance_km__isnull=True)|Q(fuel_used_l__isnull=True)|(Q(economics__isnull=False)&missing_saved)|(Q(economics__isnull=True)&missing_default)),
    }

def filter_attention(queryset,key):
    rules=conditions()
    if key not in [*rules,'all']: return queryset.none()
    return annotated(queryset).filter(reduce(or_,rules.values()) if key=='all' else rules[key])

def dashboard_attention(queryset):
    base=annotated(queryset)
    groups=[]
    for key,title,hint,action in CATEGORIES:
        matches=base.filter(conditions()[key])
        count=matches.count()
        if not count: continue
        if key=='acceptance':
            matches=matches.alias(queue_order=Case(When(status='queued',then=Value(1)),default=Value(0),output_field=IntegerField())).order_by('queue_order','planned_date','created_at','pk')
        elif key=='cancellation': matches=matches.order_by('cancelled_at','pk')
        else: matches=matches.order_by('completed_at','pk')
        groups.append({'key':key,'title':title,'hint':hint,'action':action,'count':count,'trips':list(matches[:3])})
    return {'attention_groups':groups,'attention_total':base.filter(reduce(or_,conditions().values())).count() if groups else 0,'attention_issues':sum(group['count'] for group in groups)}
