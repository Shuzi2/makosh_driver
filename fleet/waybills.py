"""Printable waybills built from completed trip facts."""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render, redirect
from django import forms
from django.db import transaction
from django.db.models import Q, Exists, OuterRef
from django.db.models.functions import Coalesce
from django.utils.dateparse import parse_date
from django.http import QueryDict
from django.urls import reverse
from urllib.parse import urlencode
from .models import Trip, TripStop

class WaybillFilterForm(forms.Form):
    q=forms.CharField(label='Пошук',required=False,max_length=150,widget=forms.TextInput(attrs={'placeholder':'№ рейсу або причіп'}))
    driver=forms.ChoiceField(label='Водій',required=False)
    vehicle=forms.ChoiceField(label='Тягач',required=False)
    start=forms.DateField(label='Завершено від',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    end=forms.DateField(label='Завершено до',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    loading=forms.CharField(label='Завантаження',required=False,max_length=150,widget=forms.TextInput(attrs={'placeholder':'Місто або адреса завантаження'}))
    unloading=forms.CharField(label='Розвантаження',required=False,max_length=150,widget=forms.TextInput(attrs={'placeholder':'Місто або адреса розвантаження'}))

    def __init__(self,*args,base=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['driver'].choices=[('','Усі водії')]+[(v,v) for v in base.order_by('filter_driver').values_list('filter_driver',flat=True).distinct() if v]
        self.fields['vehicle'].choices=[('','Усі тягачі')]+[(v,v) for v in base.order_by('filter_vehicle').values_list('filter_vehicle',flat=True).distinct() if v]

    def clean(self):
        data=super().clean()
        if data.get('start') and data.get('end') and data['start']>data['end']:
            raise forms.ValidationError('Початок періоду має бути не пізніше його завершення.')
        return data

@login_required
def waybill_list(request):
    if not request.user.manages_fleet: raise PermissionDenied
    base=Trip.objects.filter(status='completed').annotate(filter_driver=Coalesce('completed_driver_name','driver__full_name'),filter_vehicle=Coalesce('completed_tractor_plate','vehicle__plate'),filter_trailer=Coalesce('completed_trailer_plate','vehicle__trailer_plate'))
    form=WaybillFilterForm(request.GET or None,base=base)
    trips=base.select_related('vehicle','driver')
    if request.GET and not form.is_valid(): trips=trips.none()
    elif form.is_valid():
        data=form.cleaned_data
        if data['q']:
            term=data['q'].strip(); number=term.lstrip('№# ')
            criteria=Q(filter_trailer__icontains=term)|Q(waybill_overrides__fields__number__icontains=term)
            if number.isdigit() and len(number)<19: criteria|=Q(pk=int(number))
            trips=trips.filter(criteria)
        for field in ('driver','vehicle'):
            if data[field]: trips=trips.filter(**{f'filter_{field}':data[field]})
        if data['start']: trips=trips.filter(completed_at__date__gte=data['start'])
        if data['end']: trips=trips.filter(completed_at__date__lte=data['end'])
        for field,kind,legacy in [('loading','load','origin'),('unloading','unload','destination')]:
            if data[field]:
                matching=TripStop.objects.filter(trip_id=OuterRef('pk'),kind=kind,address__icontains=data[field]).exclude(state='cancelled')
                trips=trips.filter(Q(**{f'{legacy}__icontains':data[field]})|Exists(matching))
    trips=trips.order_by('-completed_at','-pk')
    page=Paginator(trips,50).get_page(request.GET.get('page'))
    params=request.GET.copy();params.pop('page',None)
    return_query=clean_return_query(request.GET.urlencode())
    return render(request,'fleet/waybill_list.html',{'trips':page.object_list,'page':page,'form':form,'filter_query':params.urlencode(),'filtered':bool(params),'return_query':return_query})

def clean_return_query(raw):
    """Preserve only list filters and page; never accept a redirect URL."""
    if len(raw)>2048: return ''
    source=QueryDict(raw);result=QueryDict(mutable=True)
    for key in ('q','driver','vehicle','start','end','loading','unloading','page'):
        value=source.get(key,'')
        if value and len(value)<=150: result[key]=value
    return result.urlencode()

def detail_url(pk,query):
    return reverse('waybill_detail',args=[pk])+('?' + urlencode({'return_query':query}) if query else '')

def waybill_data(trip, use_overrides=True):
    stops=list(trip.stops.exclude(state='cancelled').order_by('position','pk')) if trip.route_enabled else []
    points=[{'address':s.address,'kind':s.get_kind_display()} for s in stops] or [{'address':trip.origin,'kind':'Завантаження'},{'address':trip.destination,'kind':'Розвантаження'}]
    legs=[{'origin':trip.acceptance_location or 'Не вказано','destination':points[0]['address'],'label':'Без вантажу','distance':trip.empty_distance_km}]
    for index in range(len(points)-1):
        legs.append({'origin':points[index]['address'],'destination':points[index+1]['address'],'label':'З вантажем' if len(points)==2 else 'Ділянка маршруту','distance':trip.loaded_distance_km if len(points)==2 else None})
    mismatch=trip.empty_distance_km is not None and trip.loaded_distance_km is not None and trip.distance_km != trip.empty_distance_km+trip.loaded_distance_km
    end_odometer=(trip.acceptance_odometer_km+trip.empty_distance_km+trip.loaded_distance_km) if all(value is not None for value in (trip.acceptance_odometer_km,trip.empty_distance_km,trip.loaded_distance_km)) else None
    defaults={'number':str(trip.pk),'date':trip.planned_date.isoformat(),'organisation':'ТОВ «Макош Мінерал»','organisation_address':'Вінницька обл., Вінницький р-н, с. Зарванці, вул. Хмельницьке шосе, 20','brand':'SCANIA','plates':f'{trip.report_tractor_plate} / {trip.report_trailer_plate}' if trip.report_trailer_plate else trip.report_tractor_plate,'driver_name':trip.report_driver_name,'recipient':'ТОВ «Макош Мінерал»','dispatch_address':'','notes':'','driver_received':trip.report_driver_name,'driver_returned':trip.report_driver_name,'odometer_start':trip.acceptance_odometer_km,'odometer_end':end_odometer,'fuel_start':None,'fuel_issued':None,'fuel_ticket':'','fuel_remaining':None,'fuel_norm':None,'fuel_actual':trip.fuel_used_l,'fuel_economy':None,'fuel_overuse':None,'distance_total':trip.distance_km,'authorised_by':'','director':'','received_by':'','signature':''}
    saved=(trip.waybill_overrides or {}) if use_overrides else {}
    data={**defaults,**saved.get('fields',{})}
    data['date']=parse_date(data['date']) if data.get('date') else None
    legs=saved.get('legs',legs)
    return {'trip':trip,'data':data,'legs':legs,'empty_rows':range(max(0,16-len(legs))),'multi_stop':len(points)>2,'distance_mismatch':mismatch,'end_odometer_km':end_odometer}

@login_required
def waybill_detail(request,pk):
    if not request.user.manages_fleet: raise PermissionDenied
    trip=get_object_or_404(Trip.objects.select_related('vehicle','driver'),pk=pk,status='completed')
    query=clean_return_query(request.GET.get('return_query',''))
    return render(request,'fleet/waybill_detail.html',{**waybill_data(trip),'return_query':query})

class WaybillForm(forms.Form):
    revision=forms.DateTimeField(widget=forms.HiddenInput)
    number=forms.CharField(label='Номер шляхового листа',max_length=40,required=False)
    date=forms.DateField(label='Дата',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    organisation=forms.CharField(label='Організація',max_length=250,required=False)
    organisation_address=forms.CharField(label='Адреса організації',max_length=300,required=False)
    brand=forms.CharField(label='Марка автомобіля',max_length=80,required=False)
    plates=forms.CharField(label='Державний номер авто / причепа',max_length=80,required=False)
    driver_name=forms.CharField(label='Водій',max_length=150,required=False)
    recipient=forms.CharField(label='В розпорядження',max_length=250,required=False)
    dispatch_address=forms.CharField(label='Адреса подачі',max_length=300,required=False)
    notes=forms.CharField(label='Запізнення, простої та інші відмітки',max_length=1000,required=False,widget=forms.Textarea(attrs={'rows':2}))
    driver_received=forms.CharField(label='Прийняв водій',max_length=150,required=False)
    driver_returned=forms.CharField(label='Автомобіль здав водій',max_length=150,required=False)
    odometer_start=forms.IntegerField(label='Початковий показник спідометра, км',min_value=0,max_value=99999999,required=False)
    odometer_end=forms.IntegerField(label='Показник при поверненні, км',min_value=0,max_value=99999999,required=False)
    fuel_start=forms.DecimalField(label='Залишок пального до початку, л',min_value=0,max_digits=10,decimal_places=2,required=False)
    fuel_issued=forms.DecimalField(label='Видано пального, л',min_value=0,max_digits=10,decimal_places=2,required=False)
    fuel_ticket=forms.CharField(label='Номер заправного листа',max_length=100,required=False)
    fuel_remaining=forms.DecimalField(label='Залишок пального, л',min_value=0,max_digits=10,decimal_places=2,required=False)
    fuel_norm=forms.DecimalField(label='Витрата за нормою, л',min_value=0,max_digits=10,decimal_places=2,required=False)
    fuel_actual=forms.DecimalField(label='Фактична витрата пального, л',min_value=0,max_digits=10,decimal_places=2,required=False)
    fuel_economy=forms.DecimalField(label='Економія, л',min_value=0,max_digits=10,decimal_places=2,required=False)
    fuel_overuse=forms.DecimalField(label='Перевитрата, л',min_value=0,max_digits=10,decimal_places=2,required=False)
    distance_total=forms.IntegerField(label='Загальний пробіг, км',min_value=0,max_value=99999999,required=False)
    authorised_by=forms.CharField(label='Виїзд дозволив',max_length=150,required=False)
    director=forms.CharField(label='Директор',max_length=150,required=False)
    received_by=forms.CharField(label='Автомобіль прийняв',max_length=150,required=False)
    signature=forms.CharField(label='Підпис особи, яка користувалася авто',max_length=150,required=False)

class LegForm(forms.Form):
    origin=forms.CharField(label='Звідки',max_length=300,required=False)
    destination=forms.CharField(label='Куди',max_length=300,required=False)
    distance=forms.IntegerField(label='Пробіг, км',min_value=0,max_value=99999999,required=False)
    label=forms.CharField(label='Вид переїзду',max_length=100,required=False)

LegFormSet=forms.formset_factory(LegForm,extra=0,can_delete=True,max_num=30,validate_max=True,absolute_max=30)

@login_required
def waybill_edit(request,pk):
    if not request.user.manages_fleet: raise PermissionDenied
    query=clean_return_query(request.POST.get('return_query',request.GET.get('return_query','')))
    with transaction.atomic():
        trip=get_object_or_404(Trip.objects.select_for_update(),pk=pk,status='completed')
        current=waybill_data(trip)
        if request.method=='POST':
            form=WaybillForm(request.POST)
            legs=LegFormSet(request.POST,prefix='legs')
            if form.is_valid() and legs.is_valid():
                if form.cleaned_data['revision']!=trip.updated_at:
                    form.add_error(None,'Рейс змінився. Відкрийте актуальні дані перед збереженням.')
                else:
                    from django.core.serializers.json import DjangoJSONEncoder
                    import json
                    fields={k:v for k,v in form.cleaned_data.items() if k!='revision'}
                    rows=[{k:v for k,v in item.items() if k not in ('DELETE',)} for item in legs.cleaned_data if not item.get('DELETE') and any(item.get(k) not in (None,'') for k in ('origin','destination','distance','label'))]
                    previous=trip.waybill_overrides
                    base=waybill_data(trip,use_overrides=False)
                    def normalized(value): return json.loads(json.dumps(value,cls=DjangoJSONEncoder,ensure_ascii=False))
                    base_values=normalized(base['data']); submitted=normalized(fields)
                    changed_fields={key:value for key,value in submitted.items() if value!=base_values.get(key) and not (value is None and base_values.get(key)=='')}
                    result={'fields':changed_fields}
                    if normalized(rows)!=normalized(base['legs']): result['legs']=rows
                    trip.waybill_overrides=result
                    if previous!=trip.waybill_overrides:
                        trip.save(update_fields=['waybill_overrides','updated_at'])
                        from .views import event
                        prior=normalized(current['data'])
                        changes=[f'{form.fields[key].label}: {prior.get(key) if prior.get(key) not in (None, "") else "—"} → {value if value not in (None, "") else "—"}' for key,value in submitted.items() if value!=prior.get(key)]
                        if normalized(rows)!=normalized(current['legs']): changes.append('Змінено ділянки маршруту')
                        event(request.user,'Змінено шляховий лист',trip,('; '.join(changes) or 'Оновлено виправлення бланка')[:1500])
                    return redirect(detail_url(pk,query))
        else:
            form=WaybillForm(initial={**current['data'],'revision':trip.updated_at.isoformat()})
            legs=LegFormSet(prefix='legs',initial=current['legs'])
        return render(request,'fleet/waybill_edit.html',{'trip':trip,'form':form,'legs':legs,'return_query':query,'detail_url':detail_url(pk,query)})
