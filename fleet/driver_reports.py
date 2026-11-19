"""Driver economics: imported historical snapshots and live trip calculations."""
from decimal import Decimal
from django import forms
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.template.defaultfilters import floatformat
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Sum,Count,Q
from django.shortcuts import get_object_or_404,render,redirect
from django.contrib import messages
from .models import DriverReportHistory,Trip,TripEconomics,DriverEconomicsDefaults,User

def finances(trip,inputs):
    expenses=[item for item in trip.expenses.all() if item.category!='fuel']
    other_uah=sum((item.amount for item in expenses if item.currency=='UAH'),Decimal('0'))
    foreign=any(item.currency!='UAH' for item in expenses)
    if inputs is None:
        km=Decimal(trip.distance_km) if trip.distance_km is not None else None
        return {'other_uah':other_uah,'foreign':foreign,'fuel_rate':trip.fuel_used_l/km*100 if trip.fuel_used_l is not None and km else None}
    km=Decimal(trip.distance_km) if trip.distance_km is not None else None
    def product(*values):
        if any(value is None for value in values): return None
        total=Decimal('1')
        for value in values: total*=value
        return total
    fuel=product(trip.fuel_used_l,inputs.fuel_price)
    adblue=product(km,inputs.adblue_rate,inputs.adblue_price,Decimal('.01'))
    depreciation=product(km,inputs.depreciation_per_km+inputs.credit_per_km) if inputs.depreciation_per_km is not None and inputs.credit_per_km is not None else None
    wage=(product(inputs.daily_rate,inputs.days)+product(km,inputs.bonus_per_km)) if all(x is not None for x in (inputs.daily_rate,inputs.days,km,inputs.bonus_per_km)) else None
    parts=[fuel,adblue,depreciation,wage,inputs.extra_cost,other_uah]
    total=sum(parts,Decimal('0')) if not foreign and all(x is not None for x in parts) else None
    profit=inputs.price-total if inputs.price is not None and total is not None else None
    return {'fuel':fuel,'adblue':adblue,'depreciation':depreciation,'wage':wage,'other_uah':other_uah,'foreign':foreign,'total':total,'profit':profit,'cost_per_km':total/km if total is not None and km else None,'profit_per_km':profit/km if profit is not None and km else None,'margin':profit/inputs.price*100 if profit is not None and inputs.price else None,'fuel_rate':trip.fuel_used_l/km*100 if trip.fuel_used_l is not None and km else None}


# The archive keys are intentionally kept separate from the live calculation keys.
RESULT_FIELDS = {
    'fuel': ('fuel_cost', 'Витрати на пальне'), 'adblue': ('adblue_cost', 'Витрати на AdBlue'),
    'depreciation': ('depreciation_cost', 'Амортизація і кредит'), 'wage': ('driver_cost', 'Оплата водія'),
    'total': ('expenses', 'Загальні витрати'), 'profit': ('profit', 'Прибуток'),
    'cost_per_km': ('cost_per_km', 'Собівартість'), 'profit_per_km': ('profit_per_km', 'Прибуток на кілометр'),
    'margin': ('margin', 'Маржа'),
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

def guard(request):
    if not request.user.manages_fleet: raise PermissionDenied

def archive_prices_corrected(row):
    return any(key in row.report_corrections for key in ('revenue','fuel_price','adblue_price'))

def history_totals(rows):
    from .management.commands.import_driver_reports import number
    result={}
    for key in ('kilometers','revenue','expenses','profit'):
        values=[None if key in ('expenses','profit') and archive_prices_corrected(row) else number((row.source_values | row.report_corrections).get(key)) for row in rows]
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
            if revenue is not None and revenue>0 and profit is not None:
                margin_revenue+=revenue; margin_profit+=profit;result['margin_count']+=1
            if km is not None and km>0 and litres is not None:
                fuel_l+=litres;fuel_km+=km;result['fuel_count']+=1
        result.update(revenue_per_km=revenue_sum/revenue_km if revenue_km else None,
                      margin=margin_profit/margin_revenue*100 if margin_revenue else None,
                      fuel_rate=fuel_l/fuel_km*100 if fuel_km else None)
        return result
    historic=[]
    for row in history:
        values=row.source_values | row.report_corrections
        ab=next((entry.get('value') for entry in (row.display_values or {}).get('extra_columns',[]) if entry.get('column')=='AB'),None)
        historic.append({'km':number(values.get('kilometers')),'revenue':number(values.get('revenue')),'profit':None if archive_prices_corrected(row) else number(values.get('profit')),'litres':number(ab)})
    live=[]
    for trip in trips:
        try: economics=trip.economics
        except TripEconomics.DoesNotExist: economics=None
        result=finances(trip,economics)
        live.append({'km':number(trip.distance_km),'revenue':economics.price if economics else None,'profit':result.get('profit'),'litres':number(trip.fuel_used_l)})
    return {'historical':calculate(historic),'live':calculate(live)}

@login_required
def overview(request):
    guard(request)
    users=User.objects.filter(role='driver').order_by('full_name')
    period=PeriodForm(request.GET or None)
    history_query=DriverReportHistory.objects.all()
    trip_query=Trip.objects.filter(status='completed').select_related('economics').prefetch_related('expenses')
    if period.is_bound:
        if period.is_valid():
            for field,value in period.cleaned_data.items():
                if value:
                    lookup='gte' if field=='start' else 'lte'
                    history_query=history_query.filter(**{f'trip_date__{lookup}':value})
                    trip_query=trip_query.filter(**{f'completed_at__date__{lookup}':value})
        else: history_query=history_query.none();trip_query=trip_query.none()
    historical=list(history_query);completed=list(trip_query)
    by_driver={};live_driver={};unlinked={}
    for item in historical:
        if item.driver_id: by_driver.setdefault(item.driver_id,[]).append(item)
        else: unlinked.setdefault(item.driver_label,[]).append(item)
    for item in completed: live_driver.setdefault(item.driver_id,[]).append(item)
    rows=[{'driver':user,'historical':len(by_driver.get(user.pk,[])),'live':len(live_driver.get(user.pk,[])), 'history_sums':history_totals(by_driver.get(user.pk,[])), 'live_finance':live_financial_totals(live_driver.get(user.pk,[]))} for user in users]
    unmatched=[{'driver_label':label,'count':len(items)} for label,items in unlinked.items()]
    live_totals={'km':sum((t.distance_km for t in completed if t.distance_km is not None),0) if any(t.distance_km is not None for t in completed) else None,'fuel':sum((t.fuel_used_l for t in completed if t.fuel_used_l is not None),Decimal('0')) if any(t.fuel_used_l is not None for t in completed) else None}
    insights=report_insights(historical,completed)
    return render(request,'fleet/driver_reports.html',{'drivers':rows,'unmatched':unmatched,'history_total':len(historical),'live_total':len(completed),'history_sums':history_totals(historical),'live_sums':live_totals,'live_finance':live_financial_totals(completed),'insight_groups':[('Архів таблиці',insights['historical']),('Рейси сайту',insights['live'])],'period':period,'warning_count':sum(bool(row.anomalies) or archive_prices_corrected(row) for row in historical)})

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
    trips=Trip.objects.filter(status='completed',driver=driver).select_related('vehicle','driver','economics').prefetch_related('expenses').order_by('-completed_at','-pk') if driver else Trip.objects.none()
    if period and period.is_valid():
        for field,value in period.cleaned_data.items():
            if value:
                lookup='gte' if field=='start' else 'lte'
                historical=historical.filter(**{f'trip_date__{lookup}':value})
                trips=trips.filter(**{f'completed_at__date__{lookup}':value})
    elif period and period.is_bound:
        historical=historical.none();trips=trips.none()
    historical=list(historical)
    trips=list(trips)
    defaults=DriverEconomicsDefaults.objects.filter(pk=1).first() or DriverEconomicsDefaults()
    for row in historical:
        row.display=(row.display_values or row.source_values) | row.report_corrections
        row.result_cells=make_result_cells(row.display,"archive")
        row.source_number = row.source_raw[0] if row.source_sheet != 'Базюнь підмінний Зерновоз' and row.source_raw else None
        row.extra_ab = next((item['value'] for item in row.display.get('extra_columns', []) if item.get('column') == 'AB'), None)
        from .management.commands.import_driver_reports import number
        raw=row.source_values | row.report_corrections
        row.prices_form=ArchivePricesForm(initial={'revision':archive_revision(row),**{key:number(raw.get(key)) for key in ('revenue','fuel_price','adblue_price')}})
        row.prices_corrected=archive_prices_corrected(row)
    for trip in trips:
        try: trip.report_economics=trip.economics;trip.report_finances=finances(trip,trip.economics)
        except TripEconomics.DoesNotExist:
            trip.report_economics=None
            preview=economics_initial(trip,None,defaults); preview.pop("revision",None)
            trip.report_finances=finances(trip,TripEconomics(trip=trip,**preview))
        trip.result_cells=make_result_cells(trip.report_finances,"live")
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
        historical=[row for row in historical if (str(row.source_number)==number_query.group(1) if number_query else matches(row.source_number,row.display.get('date'),row.display.get('empty_route'),row.display.get('loading'),row.display.get('unloading'),row.display.get('route')))]
        from django.utils import timezone
        trips=[trip for trip in trips if (str(trip.pk)==number_query.group(1) if number_query else matches(trip.pk,timezone.localtime(trip.completed_at).strftime('%d.%m.%Y') if trip.completed_at else '',trip.acceptance_location,trip.origin,trip.destination,trip.report_tractor_plate))]
    insights=report_insights(historical,trips)
    return {'driver':driver,'name':driver.full_name if driver else label,'historical':historical,'trips':trips,'history_count':len(historical),'trip_count':len(trips),'history_sums':history_totals(historical),'live_finance':live_financial_totals(trips),'insight_groups':[('Архів таблиці',insights['historical']),('Рейси сайту',insights['live'])],'period':period,'query':query}

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
        form=ArchivePricesForm(request.POST)
        if not form.is_valid():
            messages.error(request,'Перевірте ціни: введіть невід’ємні числа з не більш ніж двома знаками після коми.')
        elif form.cleaned_data['revision']!=archive_revision(row):
            messages.error(request,'Цей запис уже змінено. Оновіть сторінку та повторіть редагування.')
        else:
            corrections=dict(row.report_corrections)
            from .management.commands.import_driver_reports import number
            changes=[]
            for key in ('revenue','fuel_price','adblue_price'):
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
                event(request.user,'Виправлено ціни архівного звіту',detail=f'{row.source_sheet}, рядок {row.source_row}: '+ '; '.join(changes))
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
