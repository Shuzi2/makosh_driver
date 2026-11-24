"""Driver economics: imported historical snapshots and live trip calculations."""
from decimal import Decimal
from django import forms
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_POST
from django.template.defaultfilters import floatformat
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Sum,Count,Q
from django.shortcuts import get_object_or_404,render,redirect
from django.contrib import messages
from .models import DriverReportHistory,Trip,TripEconomics,DriverEconomicsDefaults,User

def report_kilometers(trip,inputs):
    manual=((inputs.report_overrides or {}).get('manual_v2',{}) if inputs else {})
    return Decimal(str(manual['kilometers'])) if 'kilometers' in manual else (Decimal(trip.distance_km) if trip.distance_km is not None else None)

def report_date(row,kind):
    from datetime import date
    from django.utils import timezone
    if kind=='archive':
        value=(row.report_corrections or {}).get('date')
        fallback=row.trip_date
    else:
        try: value=(row.economics.report_overrides or {}).get('manual_v2',{}).get('date')
        except TripEconomics.DoesNotExist: value=None
        fallback=timezone.localtime(row.completed_at).date() if row.completed_at else None
    if value:
        try: return date.fromisoformat(value)
        except ValueError: return fallback
    return fallback

def in_report_period(row,kind,period):
    if period and period.is_bound and not period.is_valid(): return False
    if not period or not period.is_valid(): return True
    value=report_date(row,kind)
    start,end=period.cleaned_data.get('start'),period.cleaned_data.get('end')
    return (not start and not end) or (value is not None and (not start or value>=start) and (not end or value<=end))

def finances(trip,inputs):
    expenses=[item for item in trip.expenses.all() if item.category!='fuel']
    other_uah=sum((item.amount for item in expenses if item.currency=='UAH'),Decimal('0'))
    foreign=any(item.currency!='UAH' for item in expenses)
    if inputs is None:
        km=Decimal(trip.distance_km) if trip.distance_km is not None else None
        return {'other_uah':other_uah,'foreign':foreign,'fuel_rate':trip.fuel_used_l/km*100 if trip.fuel_used_l is not None and km else None}
    km=report_kilometers(trip,inputs)
    def product(*values):
        if any(value is None for value in values): return None
        total=Decimal('1')
        for value in values: total*=value
        return total
    manual=(inputs.report_overrides or {}).get('manual_v2',{})
    def corrected(key,value):
        return Decimal(str(manual[key])) if key in manual else value
    rate_override=manual.get('fuel_rate')
    rate_override=Decimal(str(rate_override)) if rate_override is not None else None
    report_fuel=product(km,rate_override,Decimal('.01')) if rate_override is not None else corrected('fuel_litres',trip.fuel_used_l)
    fuel=corrected('fuel',product(report_fuel,inputs.fuel_price))
    adblue=corrected('adblue',product(km,inputs.adblue_rate,inputs.adblue_price,Decimal('.01')))
    depreciation=corrected('depreciation',product(km,inputs.depreciation_per_km+inputs.credit_per_km) if inputs.depreciation_per_km is not None and inputs.credit_per_km is not None else None)
    wage=corrected('wage',(product(inputs.daily_rate,inputs.days)+product(km,inputs.bonus_per_km)) if all(x is not None for x in (inputs.daily_rate,inputs.days,km,inputs.bonus_per_km)) else None)
    parts=[fuel,adblue,depreciation,wage,inputs.extra_cost,other_uah]
    total=corrected('total',sum(parts,Decimal('0')) if not foreign and all(x is not None for x in parts) else None)
    profit=corrected('profit',inputs.price-total if inputs.price is not None and total is not None else None)
    return {'fuel':fuel,'report_fuel_litres':report_fuel,'adblue':adblue,'depreciation':depreciation,'wage':wage,'other_uah':other_uah,'foreign':foreign,'total':total,'profit':profit,'cost_per_km':corrected('cost_per_km',total/km if total is not None and km else None),'profit_per_km':corrected('profit_per_km',profit/km if profit is not None and km else None),'margin':corrected('margin',profit/inputs.price*100 if profit is not None and inputs.price else None),'fuel_rate':rate_override if rate_override is not None else (report_fuel/km*100 if report_fuel is not None and km else None)}


# The archive keys are intentionally kept separate from the live calculation keys.
RESULT_FIELDS = {
    'fuel': ('fuel_cost', 'Витрати на пальне'), 'adblue': ('adblue_cost', 'Витрати на AdBlue'),
    'depreciation': ('depreciation_cost', 'Амортизація і кредит'), 'wage': ('driver_cost', 'Оплата водія'),
    'total': ('expenses', 'Загальні витрати'), 'profit': ('profit', 'Прибуток'),
    'cost_per_km': ('cost_per_km', 'Собівартість'), 'profit_per_km': ('profit_per_km', 'Прибуток на кілометр'),
    'margin': ('margin', 'Маржа'),
}
REPORT_LABELS={key:label for key,(_archive,label) in RESULT_FIELDS.items()} | {
    'fuel_rate':'Витрата пального, л/100 км','kilometers':'Пробіг, км','fuel_litres':'Пальне, л',
    'report_number':'Номер у звіті','source_number':'Номер у звіті','date':'Дата','empty_route':'Переїзд',
    'loading':'Точка завантаження','unloading':'Точка розвантаження','route':'Маршрут',
}

def make_result_cells(values, kind):
    return [{'key':key,'value':values.get(archive_key if kind == 'archive' else key),'kind':kind}
            for key,(archive_key,label) in RESULT_FIELDS.items()]

def archive_revision(row):
    import hashlib, json
    return hashlib.sha256(json.dumps(row.report_corrections,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

class ArchivePricesForm(forms.Form):
    revision=forms.CharField(widget=forms.HiddenInput)
    revenue=forms.DecimalField(label='Ціна рейсу, грн',required=False,min_value=0,max_digits=12,decimal_places=2)
    fuel_price=forms.DecimalField(label='Ціна пального, грн/л',required=False,min_value=0,max_digits=8,decimal_places=2)
    adblue_price=forms.DecimalField(label='Ціна AdBlue, грн/л',required=False,min_value=0,max_digits=8,decimal_places=2)

ARCHIVE_EDIT_FIELDS = ('daily_rate','days','bonus_per_km','adblue_price','fuel_price','fuel_rate','adblue_rate','depreciation_per_km','credit_per_km','revenue','extra_cost')

class ArchiveEditForm(forms.Form):
    revision=forms.CharField(widget=forms.HiddenInput)

for _key in ARCHIVE_EDIT_FIELDS:
    _field=(forms.DecimalField(label='Витрата пального, л/100 км',required=False,min_value=0,max_digits=7,decimal_places=2)
            if _key == 'fuel_rate' else TripEconomics._meta.get_field('price' if _key == 'revenue' else _key).formfield(required=False))
    ArchiveEditForm.base_fields[_key]=_field

def guard(request):
    if not request.user.manages_fleet: raise PermissionDenied

def archive_effective(row):
    """Preserve the imported snapshot and recalculate only affected or missing values."""
    from .management.commands.import_driver_reports import number
    values=dict(row.source_values or {}) | dict(row.report_corrections or {})
    if 'fuel_litres' not in values:
        values['fuel_litres']=next((item.get('value') for item in (row.display_values or {}).get('extra_columns',[]) if item.get('column')=='AB'),None)
    changed=set(row.report_corrections or {})
    def n(key): return number(values.get(key))
    def product(*items):
        if any(item is None for item in items): return None
        result=Decimal('1')
        for item in items: result*=item
        return result
    def derived(key,deps,compute):
        if key in changed: return
        if n(key) is None or changed.intersection(deps):
            values[key]=compute()
            if changed.intersection(deps): changed.add(key)
    def ratio(a,b,factor=1):
        x,y=n(a),n(b)
        return x/y*factor if x is not None and y else None
    derived('fuel_litres',('kilometers','fuel_rate'),lambda:product(n('kilometers'),n('fuel_rate'),Decimal('.01')))
    derived('fuel_cost',('kilometers','fuel_rate','fuel_price','fuel_litres'),lambda:product(n('fuel_litres'),n('fuel_price')))
    derived('adblue_cost',('kilometers','adblue_rate','adblue_price'),lambda:product(n('kilometers'),n('adblue_rate'),n('adblue_price'),Decimal('.01')))
    derived('depreciation_cost',('kilometers','depreciation_per_km','credit_per_km'),lambda:product(n('kilometers'),n('depreciation_per_km')+n('credit_per_km')) if n('depreciation_per_km') is not None and n('credit_per_km') is not None else None)
    derived('driver_cost',('daily_rate','days','kilometers','bonus_per_km'),lambda:n('daily_rate')*n('days')+n('kilometers')*n('bonus_per_km') if all(n(k) is not None for k in ('daily_rate','days','kilometers','bonus_per_km')) else None)
    def total():
        parts=[n(k) for k in ('fuel_cost','adblue_cost','depreciation_cost','driver_cost','extra_cost')]
        return sum(parts,Decimal('0')) if all(p is not None for p in parts) else None
    derived('expenses',('fuel_cost','adblue_cost','depreciation_cost','driver_cost','extra_cost'),total)
    derived('profit',('revenue','expenses'),lambda:n('revenue')-n('expenses') if n('revenue') is not None and n('expenses') is not None else None)
    derived('cost_per_km',('expenses','kilometers'),lambda:ratio('expenses','kilometers'))
    derived('profit_per_km',('profit','kilometers'),lambda:ratio('profit','kilometers'))
    derived('margin',('profit','revenue'),lambda:ratio('profit','revenue',100))
    return values

@login_required
@require_POST
def edit_archive_cell(request,pk):
    guard(request)
    from .management.commands.import_driver_reports import KEYS
    field=request.POST.get('field','')
    text_fields={'date','empty_route','loading','unloading','route','source_number'}
    if field not in set(KEYS)|{'fuel_litres','source_number'}: return JsonResponse({'error':'Невідоме поле.'},status=400)
    form=forms.Form(request.POST)
    form.fields['revision']=forms.CharField()
    form.fields['value']=(forms.DateField(required=False,input_formats=['%d.%m.%Y','%Y-%m-%d']) if field=='date' else forms.CharField(required=False,max_length=500,strip=True) if field in text_fields else forms.DecimalField(required=False,max_digits=14,decimal_places=2,min_value=None if field in ('profit','profit_per_km','margin') else 0))
    if not form.is_valid(): return JsonResponse({'error':'Перевірте введене значення.'},status=400)
    with transaction.atomic():
        row=get_object_or_404(DriverReportHistory.objects.select_for_update(),pk=pk)
        if form.cleaned_data['revision']!=archive_revision(row): return JsonResponse({'error':'Запис змінився. Оновіть сторінку.'},status=409)
        corrections=dict(row.report_corrections or {})
        before=corrections.get(field)
        value=form.cleaned_data['value']
        if value is None or value=='': corrections.pop(field,None)
        else: corrections[field]=str(value)
        row.report_corrections=corrections;row.save(update_fields=['report_corrections'])
        from .views import event
        event(request.user,'Виправлено клітинку архівного звіту',detail=f'{row.source_sheet}, рядок {row.source_row}: {field}: {before or "оригінал"} → {value if value not in (None, "") else "оригінал"}')
        return JsonResponse({'revision':archive_revision(row)})

def history_totals(rows):
    from .management.commands.import_driver_reports import number
    result={}
    for key in ('kilometers','revenue','expenses','profit'):
        values=[number(archive_effective(row).get(key)) for row in rows]
        valid=[value for value in values if value is not None]
        result[key]=sum(valid,Decimal('0')) if valid else None
        result[key+'_missing']=len(values)-len(valid)
    return result

def live_financial_totals(trips):
    totals={'revenue':None,'expenses':None,'profit':None,'complete':0,'count':len(trips)}
    for trip in trips:
        try: economics=trip.economics
        except TripEconomics.DoesNotExist: continue
        result=finances(trip,economics)
        for key,value in [('revenue',economics.price),('expenses',result.get('total')),('profit',result.get('profit'))]:
            if value is not None: totals[key]=(totals[key] or Decimal('0'))+value
        if result.get('profit') is not None: totals['complete']+=1
    return totals

PERIOD_TOTAL_FIELDS = [
    ('kilometers', 'Пробіг', 'км'), ('days', 'Днів за рейсами', 'дн.'), ('fuel_litres', 'Витрачено пального', 'л'),
    ('revenue', 'Ціна рейсів', 'грн'), ('wage', 'Оплата водія / бонус', 'грн'),
    ('fuel_cost', 'Витрати на пальне', 'грн'), ('adblue_cost', 'Витрати на AdBlue', 'грн'),
    ('depreciation_cost', 'Амортизація і кредит', 'грн'), ('extra_cost', 'Додаткові витрати', 'грн'),
    ('expenses', 'Загальні витрати', 'грн'), ('profit', 'Прибуток', 'грн'),
]

def period_totals(history, trips):
    """Sum recorded additive values, preserving missing values and separate sources."""
    from .management.commands.import_driver_reports import number
    def summarize(rows):
        result = {}
        for key, label, unit in PERIOD_TOTAL_FIELDS:
            values = [row.get(key) for row in rows]
            known = [value for value in values if value is not None]
            result[key] = {'key': key, 'value': sum(known, Decimal('0')) if known else None,
                           'known': len(known), 'missing': len(values) - len(known), 'label': label, 'unit': unit}
        return result
    archived = []
    for row in history:
        values = archive_effective(row)
        raw_ab = values.get('fuel_litres')
        archived.append({
            'kilometers': number(values.get('kilometers')), 'days': number(values.get('days')), 'fuel_litres': number(raw_ab),
            'revenue': number(values.get('revenue')), 'wage': number(values.get('driver_cost')),
            'fuel_cost': number(values.get('fuel_cost')),
            'adblue_cost': number(values.get('adblue_cost')),
            'depreciation_cost': number(values.get('depreciation_cost')), 'extra_cost': number(values.get('extra_cost')),
            'expenses': number(values.get('expenses')), 'profit': number(values.get('profit')),
        })
    live = []
    for trip in trips:
        try: economics = trip.economics
        except TripEconomics.DoesNotExist: economics = None
        result = finances(trip, economics)
        live.append({
            'kilometers': report_kilometers(trip,economics), 'days': economics.days if economics else None, 'fuel_litres': result.get('report_fuel_litres',number(trip.fuel_used_l)),
            'revenue': economics.price if economics else None, 'wage': result.get('wage'),
            'fuel_cost': result.get('fuel'), 'adblue_cost': result.get('adblue'),
            'depreciation_cost': result.get('depreciation'), 'extra_cost': economics.extra_cost if economics else None,
            'expenses': result.get('total'), 'profit': result.get('profit'),
        })
    return {'archive': summarize(archived), 'live': summarize(live)}

def report_insights(history, trips):
    """Compute comparable ratios only from rows with both inputs present."""
    from .management.commands.import_driver_reports import number
    def calculate(items):
        result={'count':len(items),'loss_count':0,'profit_count':0,'missing_profit':0,'revenue_km_count':0,'margin_count':0,'fuel_count':0}
        revenue_sum=revenue_km=margin_profit=margin_revenue=fuel_l=fuel_km=Decimal('0')
        for item in items:
            km,revenue,profit,litres=(item.get(key) for key in ('km','revenue','profit','litres'))
            if profit is None: result['missing_profit']+=1
            else:
                result['profit_count']+=1
                if profit < 0: result['loss_count']+=1
            if km is not None and km>0 and revenue is not None:
                revenue_sum+=revenue;revenue_km+=km;result['revenue_km_count']+=1
            margin=item.get('margin')
            if revenue is not None and revenue>0 and margin is not None:
                margin_revenue+=revenue; margin_profit+=revenue*margin/100;result['margin_count']+=1
            if km is not None and km>0 and litres is not None:
                fuel_l+=litres;fuel_km+=km;result['fuel_count']+=1
        result.update(revenue_per_km=revenue_sum/revenue_km if revenue_km else None,
                      margin=margin_profit/margin_revenue*100 if margin_revenue else None,
                      fuel_rate=fuel_l/fuel_km*100 if fuel_km else None)
        return result
    historic=[]
    for row in history:
        values=archive_effective(row)
        historic.append({'km':number(values.get('kilometers')),'revenue':number(values.get('revenue')),'profit':number(values.get('profit')),'margin':number(values.get('margin')),'litres':number(values.get('fuel_litres'))})
    live=[]
    for trip in trips:
        try: economics=trip.economics
        except TripEconomics.DoesNotExist: economics=None
        result=finances(trip,economics)
        live.append({'km':report_kilometers(trip,economics),'revenue':economics.price if economics else None,'profit':result.get('profit'),'margin':result.get('margin'),'litres':result.get('report_fuel_litres',number(trip.fuel_used_l))})
    return {'historical':calculate(historic),'live':calculate(live)}

@login_required
def overview(request):
    guard(request)
    users=User.objects.filter(role='driver').order_by('full_name')
    period=PeriodForm(request.GET or None)
    history_query=DriverReportHistory.objects.all()
    trip_query=Trip.objects.filter(status='completed').select_related('economics').prefetch_related('expenses')
    historical=[row for row in history_query if in_report_period(row,'archive',period)]
    completed=[trip for trip in trip_query if in_report_period(trip,'live',period)]
    by_driver={};live_driver={};unlinked={}
    for item in historical:
        if item.driver_id: by_driver.setdefault(item.driver_id,[]).append(item)
        else: unlinked.setdefault(item.driver_label,[]).append(item)
    for item in completed: live_driver.setdefault(item.driver_id,[]).append(item)
    rows=[{'driver':user,'historical':len(by_driver.get(user.pk,[])),'live':len(live_driver.get(user.pk,[])), 'history_sums':history_totals(by_driver.get(user.pk,[])), 'live_finance':live_financial_totals(live_driver.get(user.pk,[]))} for user in users]
    unmatched=[{'driver_label':label,'count':len(items)} for label,items in unlinked.items()]
    live_km=[];live_fuel=[]
    for trip in completed:
        try: economics=trip.economics
        except TripEconomics.DoesNotExist: economics=None
        km=report_kilometers(trip,economics);result=finances(trip,economics)
        fuel=result.get('report_fuel_litres',trip.fuel_used_l)
        if km is not None: live_km.append(km)
        if fuel is not None: live_fuel.append(fuel)
    live_totals={'km':sum(live_km,Decimal('0')) if live_km else None,'fuel':sum(live_fuel,Decimal('0')) if live_fuel else None}
    insights=report_insights(historical,completed)
    return render(request,'fleet/driver_reports.html',{'drivers':rows,'unmatched':unmatched,'history_total':len(historical),'live_total':len(completed),'history_sums':history_totals(historical),'live_sums':live_totals,'live_finance':live_financial_totals(completed),'insight_groups':[('Архів таблиці',insights['historical']),('Рейси сайту',insights['live'])],'period':period,'warning_count':sum(bool(row.anomalies) or bool(row.report_corrections) for row in historical)})

class PeriodForm(forms.Form):
    start=forms.DateField(label='З',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    end=forms.DateField(label='По',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    def clean(self):
        data=super().clean()
        if data.get('start') and data.get('end') and data['start']>data['end']:
            raise forms.ValidationError('Початок періоду має бути не пізніше завершення.')
        return data

def report_context(driver=None,label=None,period=None,query=''):
    historical=DriverReportHistory.objects.filter(driver=driver) if driver else DriverReportHistory.objects.filter(driver__isnull=True,driver_label=label)
    trips=Trip.objects.filter(status='completed',driver=driver).select_related('vehicle','driver','economics').prefetch_related('expenses','stops').order_by('-completed_at','-pk') if driver else Trip.objects.none()
    historical=[row for row in historical if in_report_period(row,'archive',period)]
    trips=[trip for trip in trips if in_report_period(trip,'live',period)]
    defaults=DriverEconomicsDefaults.objects.filter(pk=1).first() or DriverEconomicsDefaults()
    for row in historical:
        row.display=(row.display_values or row.source_values) | row.report_corrections
        if 'date' in row.report_corrections and report_date(row,'archive'): row.display['date']=report_date(row,'archive').strftime('%d.%m.%Y')
        row.report_revision=archive_revision(row)
        effective=archive_effective(row)
        for archive_key,_label in RESULT_FIELDS.values():
            if row.report_corrections or row.display.get(archive_key) in (None,''):
                row.display[archive_key]=floatformat(effective.get(archive_key),2) if effective.get(archive_key) is not None else None
        row.result_cells=make_result_cells(row.display,"archive")
        for cell in row.result_cells:
            key=RESULT_FIELDS[cell['key']][0]
            cell['field']=key;cell['manual']=row.report_corrections.get(key,'');cell['row_pk']=row.pk;cell['revision']=archive_revision(row)
        row.source_number = row.report_corrections.get('source_number',row.source_raw[0] if row.source_sheet != 'Базюнь підмінний Зерновоз' and row.source_raw else None)
        row.extra_ab = effective.get('fuel_litres')
        from .management.commands.import_driver_reports import number
        raw=row.source_values | row.report_corrections
        row.prices_form=ArchivePricesForm(initial={'revision':archive_revision(row),**{key:number(raw.get(key)) for key in ('revenue','fuel_price','adblue_price')}})
        row.prices_corrected=bool(row.report_corrections)
        row.edit_form=ArchiveEditForm(prefix=f'archive-{row.pk}', initial={'revision':archive_revision(row),**{key:number(raw.get(key)) for key in ARCHIVE_EDIT_FIELDS}})
        from .management.commands.import_driver_reports import KEYS
        offset=0 if row.source_sheet=='Базюнь підмінний Зерновоз' else 1
        row.report_formula={key:bool(len(row.source_formulas)>offset+i and str(row.source_formulas[offset+i]).lstrip().startswith('=')) for i,key in enumerate(KEYS)}
        row.report_formula['source_number']=bool(offset and row.source_formulas and str(row.source_formulas[0]).lstrip().startswith('='))
        for key in ARCHIVE_EDIT_FIELDS:
            if row.report_formula.get(key): row.edit_form.fields[key].widget.attrs['data_formula']='1'
            if key in row.report_corrections: row.edit_form.fields[key].widget.attrs['data_corrected']='1'
        labels=REPORT_LABELS | {archive_key:label for archive_key,label in RESULT_FIELDS.values()}
        row.correction_list=[{'label':row.edit_form.fields[key].label if key in row.edit_form.fields else labels.get(key,key),
                              'original':(next((item.get('value') for item in (row.display_values or {}).get('extra_columns',[]) if item.get('column')=='AB'),None) if key=='fuel_litres' else row.source_raw[0] if key=='source_number' and row.source_raw else row.source_values.get(key)),
                              'current':value} for key,value in row.report_corrections.items()]
    for trip in trips:
        try: trip.report_economics=trip.economics;trip.report_finances=finances(trip,trip.economics)
        except TripEconomics.DoesNotExist:
            trip.report_economics=None
            preview=economics_initial(trip,None,defaults); preview.pop("revision",None)
            trip.report_finances=finances(trip,TripEconomics(trip=trip,**preview))
        trip.result_cells=make_result_cells(trip.report_finances,"live")
        trip.report_fuel_manual=(trip.report_economics.report_overrides or {}).get('manual_v2',{}).get('fuel_rate','') if trip.report_economics else ''
        trip.report_revision=trip.report_economics.updated_at.isoformat() if trip.report_economics else ''
        trip.report_kilometers=report_kilometers(trip,trip.report_economics)
        trip.report_km_manual=(trip.report_economics.report_overrides or {}).get('manual_v2',{}).get('kilometers','') if trip.report_economics else ''
        trip.report_manual=(trip.report_economics.report_overrides or {}).get('manual_v2',{}) if trip.report_economics else {}
        points=[point for point in trip.stops.all() if point.state!='cancelled'] if trip.route_enabled else []
        loading='; '.join(point.address for point in points if point.kind=='load') or trip.origin
        unloading='; '.join(point.address for point in points if point.kind=='unload') or trip.destination
        route_points=[point.address for point in points] if points else [trip.origin,trip.destination]
        start=(trip.acceptance_location or '').strip()
        if start and (not route_points or start.casefold()!=route_points[0].strip().casefold()):
            route_points.insert(0,start)
        route=' → '.join(route_points)
        trip.report_fields={
            'report_number':trip.report_manual.get('report_number',f'№{trip.pk}'),
            'date':report_date(trip,'live').strftime('%d.%m.%Y') if report_date(trip,'live') else '',
            'empty_route':trip.report_manual.get('empty_route',f'{trip.acceptance_location} → {trip.origin}' if trip.acceptance_location else ''),
            'loading':trip.report_manual.get('loading',loading),
            'unloading':trip.report_manual.get('unloading',unloading),
            'route':trip.report_manual.get('route',route),
        }
        import copy
        base_economics=copy.copy(trip.report_economics) if trip.report_economics else None
        if base_economics:
            base_economics.report_overrides={key:value for key,value in (base_economics.report_overrides or {}).items() if key!='manual_v2'}
        base_finances=finances(trip,base_economics)
        original_values=base_finances | {'kilometers':trip.distance_km,'fuel_litres':trip.fuel_used_l,'report_number':f'№{trip.pk}',
            'date':trip.completed_at.strftime('%d.%m.%Y') if trip.completed_at else '',
            'empty_route':f'{trip.acceptance_location} → {trip.origin}' if trip.acceptance_location else '',
            'loading':loading,'unloading':unloading,'route':route}
        trip.correction_list=[{'label':REPORT_LABELS.get(key,key),'original':original_values.get(key),'current':value} for key,value in trip.report_manual.items()]
        for cell in trip.result_cells:
            cell['manual']=(trip.report_economics.report_overrides or {}).get('manual_v2',{}).get(cell['key'],'') if trip.report_economics else ''
            cell['trip_pk']=trip.pk
            cell['revision']=trip.report_revision
        trip.economics_form=EconomicsForm(instance=trip.report_economics,initial=economics_initial(trip,trip.report_economics,defaults),prefix=f'trip-{trip.pk}')
    import re
    def terms(value):
        return re.sub(r'[№#]',' ',str(value).casefold()).split()
    search_terms=terms(query)
    number_query=re.fullmatch(r'\s*[№#]\s*(\d+)\s*',query)
    def matches(*values):
        text=' '.join(str(v or '') for v in values).casefold()
        return all(term in text for term in search_terms)
    if search_terms:
        historical=[row for row in historical if (str(row.source_number).lstrip('№# ')==number_query.group(1) if number_query else matches(row.source_number,row.display.get('date'),row.display.get('empty_route'),row.display.get('loading'),row.display.get('unloading'),row.display.get('route')))]
        from django.utils import timezone
        trips=[trip for trip in trips if (str(trip.report_fields['report_number']).lstrip('№# ')==number_query.group(1) or str(trip.pk)==number_query.group(1) if number_query else matches(trip.report_fields['report_number'],trip.pk,trip.report_fields['date'],trip.report_fields['empty_route'],trip.report_fields['loading'],trip.report_fields['unloading'],trip.report_fields['route'],trip.report_tractor_plate))]
    insights=report_insights(historical,trips)
    return {'driver':driver,'name':driver.full_name if driver else label,'historical':historical,'trips':trips,'history_count':len(historical),'trip_count':len(trips),'history_sums':history_totals(historical),'live_finance':live_financial_totals(trips),'period_totals':period_totals(historical,trips),'insight_groups':[('Архів таблиці',insights['historical']),('Рейси сайту',insights['live'])],'period':period,'query':query}

@login_required
def detail(request,pk):
    guard(request);driver=get_object_or_404(User,pk=pk,role='driver')
    return render(request,'fleet/driver_report_detail.html',report_context(driver=driver,period=PeriodForm(request.GET or None),query=request.GET.get('q','').strip()[:150]))

@login_required
def unmatched_detail(request,label):
    guard(request)
    if not DriverReportHistory.objects.filter(driver__isnull=True,driver_label=label).exists():
        from django.http import Http404
        raise Http404
    return render(request,'fleet/driver_report_detail.html',report_context(label=label,period=PeriodForm(request.GET or None),query=request.GET.get('q','').strip()[:150]))

@login_required
@require_POST
def edit_archive_prices(request,pk):
    guard(request)
    with transaction.atomic():
        row=get_object_or_404(DriverReportHistory.objects.select_for_update(),pk=pk)
        inline=request.POST.get('inline') == '1'
        prefix=f'archive-{row.pk}'
        form=ArchiveEditForm(request.POST,prefix=prefix) if inline else ArchivePricesForm(request.POST)
        if not form.is_valid():
            if inline: return HttpResponse('<ul class="errorlist"><li>Перевірте значення: введіть невід’ємні числа з не більш ніж двома знаками після коми.</li></ul>',status=400)
            messages.error(request,'Перевірте ціни: введіть невід’ємні числа з не більш ніж двома знаками після коми.')
        elif form.cleaned_data['revision']!=archive_revision(row):
            if inline: return HttpResponse('<ul class="errorlist"><li>Цей запис уже змінено. Оновіть сторінку.</li></ul>',status=409)
            messages.error(request,'Цей запис уже змінено. Оновіть сторінку та повторіть редагування.')
        else:
            corrections=dict(row.report_corrections)
            from .management.commands.import_driver_reports import number
            changes=[]
            for key in (ARCHIVE_EDIT_FIELDS if inline else ('revenue','fuel_price','adblue_price')):
                if inline and f'{prefix}-{key}' not in request.POST: continue
                value=form.cleaned_data[key]
                previous=number((row.source_values | corrections).get(key))
                if value != previous:
                    if value == number(row.source_values.get(key)):
                        corrections.pop(key,None)
                    else:
                        corrections[key]=str(value) if value is not None else None
                    changes.append(f'{form.fields[key].label}: {previous if previous is not None else "—"} → {value if value is not None else "—"}')
            if changes:
                row.report_corrections=corrections;row.save(update_fields=['report_corrections'])
                from .views import event
                event(request.user,'Виправлено дані архівного звіту',detail=f'{row.source_sheet}, рядок {row.source_row}: '+ '; '.join(changes))
                messages.success(request,'Ціни виправлено. Початкові дані збережено; архівні підсумки потрібно перевірити.')
            else: messages.info(request,'Ціни не змінилися.')
    if row.driver_id: return redirect('driver_report_detail',pk=row.driver_id)
    return redirect('driver_report_unmatched',label=row.driver_label)

class EconomicsForm(forms.ModelForm):
    revision=forms.CharField(required=False,widget=forms.HiddenInput)
    class Meta:
        model=TripEconomics
        fields=['price','daily_rate','days','bonus_per_km','fuel_price','adblue_price','adblue_rate','depreciation_per_km','credit_per_km','extra_cost']
        help_texts={'extra_cost':'Додаткові витрати поза витратами, які вже записав водій. Якщо таких витрат немає, введіть 0.'}

@login_required
@require_POST
def edit_fuel_rate(request,pk):
    guard(request)
    field=request.POST.get('field','fuel_rate')
    text_fields=('report_number','date','empty_route','loading','unloading','route')
    if field not in ('fuel_rate','kilometers','fuel_litres',*text_fields,*RESULT_FIELDS): return JsonResponse({'error':'Невідоме поле.'},status=400)
    form=forms.Form(request.POST)
    form.fields['value']=(forms.DateField(required=False,input_formats=['%d.%m.%Y','%Y-%m-%d']) if field=='date' else forms.CharField(required=False,max_length=500) if field in text_fields else forms.DecimalField(required=False,min_value=None if field in ('profit','profit_per_km','margin') else 0,max_digits=14,decimal_places=2))
    form.fields['revision']=forms.CharField(required=False)
    if not form.is_valid(): return JsonResponse({'error':'Перевірте введене значення.'},status=400)
    with transaction.atomic():
        trip=get_object_or_404(Trip.objects.select_for_update(),pk=pk,status='completed')
        record=TripEconomics.objects.select_for_update().filter(trip=trip).first()
        if form.cleaned_data['revision'] != (record.updated_at.isoformat() if record else ''):
            return JsonResponse({'error':'Рейс уже змінено. Оновіть сторінку.'},status=409)
        if record is None: record=snapshot_completion_rates(trip)
        overrides=dict(record.report_overrides or {})
        manual=dict(overrides.get('manual_v2') or {})
        before=manual.get(field)
        value=form.cleaned_data['value']
        if value is None: manual.pop(field,None)
        else: manual[field]=str(value)
        if manual: overrides['manual_v2']=manual
        else: overrides.pop('manual_v2',None)
        record.report_overrides=overrides
        record.save(update_fields=['report_overrides','updated_at'])
        from .views import event
        event(request.user,'Виправлено формулу у звіті',trip,f'{field}: {before or "автоматично"} → {value if value is not None else "автоматично"}')
        return JsonResponse({'revision':record.updated_at.isoformat()})

def economics_initial(trip, existing, defaults=None):
    initial={'revision':existing.updated_at.isoformat() if existing else ''}
    if existing is None:
        if defaults is None: defaults=DriverEconomicsDefaults.objects.filter(pk=1).first() or DriverEconomicsDefaults()
        for name in ('daily_rate','days','bonus_per_km','adblue_price','fuel_price','adblue_rate','credit_per_km','price','extra_cost'):
            value=getattr(defaults,name)
            if value is not None: initial[name]=value
        kind=trip.completed_vehicle_kind if trip.completed_vehicle_kind is not None else trip.vehicle.kind
        depreciation=defaults.depreciation_tent if kind == 'tent' else defaults.depreciation_grain if kind == 'grain' else None
        if depreciation is not None: initial['depreciation_per_km']=depreciation
    return initial

def snapshot_completion_rates(trip):
    """Keep the current rates for a newly completed trip without replacing edits."""
    defaults=DriverEconomicsDefaults.objects.filter(pk=1).first() or DriverEconomicsDefaults()
    values=economics_initial(trip,None,defaults);values.pop('revision',None)
    return TripEconomics.objects.get_or_create(trip=trip,defaults=values)[0]

@login_required
@require_POST
def preview_economics(request,pk):
    guard(request)
    trip=get_object_or_404(Trip.objects.select_related('vehicle').prefetch_related('expenses'),pk=pk,status='completed')
    form=EconomicsForm(request.POST, prefix=f'trip-{trip.pk}')
    if not form.is_valid():
        return JsonResponse({'error':'Перевірте введені числа; попередній розрахунок недоступний.'},status=400)
    inputs=TripEconomics(trip=trip,**{name:form.cleaned_data[name] for name in EconomicsForm.Meta.fields})
    saved=TripEconomics.objects.filter(trip=trip).first()
    if saved: inputs.report_overrides=saved.report_overrides
    values=finances(trip,inputs)
    return JsonResponse({'values':{key:str(floatformat(values[key],2)) if values.get(key) is not None else '—' for key in RESULT_FIELDS}})

class DriverDefaultsForm(forms.ModelForm):
    class Meta:
        model=DriverEconomicsDefaults
        fields=['daily_rate','days','bonus_per_km','adblue_price','fuel_price','adblue_rate','depreciation_tent','depreciation_grain','credit_per_km','price','extra_cost']

@login_required
def edit_defaults(request):
    guard(request)
    with transaction.atomic():
        current=DriverEconomicsDefaults.objects.select_for_update().filter(pk=1).first()
        original=current or DriverEconomicsDefaults()
        before={name:getattr(original,name) for name in DriverDefaultsForm.Meta.fields}
        previous_defaults=DriverEconomicsDefaults(**before)
        form=DriverDefaultsForm(request.POST or None,instance=current or DriverEconomicsDefaults())
        if request.method=='POST' and form.is_valid():
            # Preserve the values currently displayed for older trips before changing defaults.
            # The actual historical rates cannot be reconstructed if they were never saved.
            unsaved=Trip.objects.select_for_update(of=('self',)).filter(status='completed',economics__isnull=True).select_related('vehicle')
            for trip in unsaved.iterator(chunk_size=250):
                initial=economics_initial(trip,None,previous_defaults);initial.pop('revision',None)
                TripEconomics.objects.get_or_create(trip=trip,defaults=initial)
            saved=form.save(commit=False);saved.pk=1;saved.save()
            from .views import event
            changes=[f'{form.fields[name].label}: {before[name] if before[name] is not None else "—"} → {getattr(saved,name) if getattr(saved,name) is not None else "—"}' for name in DriverDefaultsForm.Meta.fields if before[name]!=getattr(saved,name)]
            if changes: event(request.user,'Змінено значення звіту водіїв',detail='; '.join(changes)[:1500])
            return redirect('driver_reports')
    return render(request,'fleet/driver_defaults_form.html',{'form':form})

@login_required
def edit_economics(request,pk):
    guard(request)
    with transaction.atomic():
        trip=get_object_or_404(Trip.objects.select_for_update(),pk=pk,status='completed')
        existing=TripEconomics.objects.filter(trip=trip).first()
        prior={name:getattr(existing,name,None) for name in EconomicsForm.Meta.fields}
        prefix=f'trip-{trip.pk}' if request.POST.get('inline') == '1' else None
        form=EconomicsForm(request.POST or None,instance=existing,initial=economics_initial(trip,existing),prefix=prefix)
        if request.method=='POST' and form.is_valid():
            if form.cleaned_data['revision']!=(existing.updated_at.isoformat() if existing else ''):
                form.add_error(None,'Ставки вже змінилися. Оновіть сторінку перед збереженням.')
                return render(request,'fleet/driver_economics_form.html',{'trip':trip,'form':form})
            record=form.save(commit=False);record.trip=trip;record.save()
            from .views import event
            changes=[f'{form.fields[name].label}: {prior[name] if prior[name] is not None else "—"} → {form.cleaned_data[name] if form.cleaned_data[name] is not None else "—"}' for name in EconomicsForm.Meta.fields if prior[name]!=form.cleaned_data[name]]
            if changes: event(request.user,'Оновлено економіку рейсу',trip,'; '.join(changes)[:1500])
            return redirect('driver_report_detail',pk=trip.driver_id)
    return render(request,'fleet/driver_economics_form.html',{'trip':trip,'form':form})
