"""Driver's final trip totals, saved atomically with completion."""
from django import forms
from decimal import Decimal, ROUND_HALF_UP
from django.db.models import F
from django.shortcuts import render
from django.utils import timezone
from .models import Vehicle
from .routes import route_context, audit


class DriverFinishForm(forms.Form):
    empty_distance_km=forms.IntegerField(label='Переїзд до завантаження, км',min_value=0,max_value=20000,
        widget=forms.NumberInput(attrs={'inputmode':'numeric','placeholder':'Наприклад, 71'}),help_text='Від місця стоянки до першого завантаження.')
    loaded_distance_km=forms.IntegerField(label='Пробіг з вантажем, км',min_value=0,max_value=20000,
        widget=forms.NumberInput(attrs={'inputmode':'numeric','placeholder':'Наприклад, 1286'}),help_text='Пробіг після завантаження до завершення рейсу.')
    fuel_rate=forms.DecimalField(label='Витрата пального, л/100 км',min_value=0,max_value=2000,max_digits=6,decimal_places=2,
        widget=forms.NumberInput(attrs={'inputmode':'decimal','step':'0.01','placeholder':'Наприклад, 32.5'}),
        help_text='Вкажіть середню витрату на 100 км. Витрачені літри розрахуються за загальним пробігом рейсу.')

    def __init__(self,data=None,*args,trip=None,**kwargs):
        if data is not None:
            data=data.copy()
            for key in data:
                if key in ('fuel_rate','fuel_used_l','actual_tons') or key.startswith('unload_weight_'):
                    data[key]=data[key].replace(',','.')
        super().__init__(data,*args,**kwargs)
        self.legacy_fuel=data.get('fuel_used_l') if data is not None and 'fuel_rate' not in data else None
        if self.legacy_fuel is not None: self.fields['fuel_rate'].required=False
        self.saved_empty_distance=trip.empty_distance_km if trip else None
        if self.saved_empty_distance is not None:
            self.fields.pop('empty_distance_km')
        self.weight_points=[]
        points=list(trip.stops.filter(kind='unload').exclude(state='cancelled').order_by('position','pk')) if trip and trip.route_enabled else []
        if points:
            for point in points:
                name='actual_tons' if len(points)==1 else f'unload_weight_{point.pk}'
                self.fields[name]=forms.DecimalField(label='Фактична вага розвантаження, т' if len(points)==1 else f'Вага розвантаження · {point.address}, т',min_value=0.01,max_value=60,max_digits=5,decimal_places=3,initial=point.actual_tons,widget=forms.NumberInput(attrs={'inputmode':'decimal','step':'0.001','placeholder':'Наприклад, 24.255'}))
                self.weight_points.append((name,point))
        else:
            self.fields['actual_tons']=forms.DecimalField(label='Фактична вага розвантаження, т',min_value=0.01,max_value=60,max_digits=5,decimal_places=3,initial=trip.actual_tons if trip else None,widget=forms.NumberInput(attrs={'inputmode':'decimal','step':'0.001','placeholder':'Наприклад, 24.255'}))

    def clean(self):
        data=super().clean()
        empty=self.saved_empty_distance if self.saved_empty_distance is not None else data.get('empty_distance_km');loaded=data.get('loaded_distance_km')
        if self.saved_empty_distance is not None: data['empty_distance_km']=empty
        if empty is not None and loaded is not None:
            total=empty+loaded
            if not 1<=total<=20000: raise forms.ValidationError('Загальний пробіг має бути від 1 до 20 000 км.')
            data['distance_km']=total
            if data.get('fuel_rate') is not None:
                litres=(Decimal(total)*data['fuel_rate']/100).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
                if litres>20000: self.add_error('fuel_rate','За цим пробігом витрата перевищує 20 000 л. Перевірте дані.')
                else: data['fuel_used_l']=litres
            elif self.legacy_fuel is not None:
                try: data['fuel_used_l']=forms.DecimalField(min_value=0,max_value=20000,max_digits=7,decimal_places=2).clean(self.legacy_fuel)
                except forms.ValidationError: self.add_error('fuel_rate','Перевірте витрату пального.')
        return data


def render_finish(request,trip,data=None,form=None):
    context=route_context(trip)
    context.update(trip=trip,documents_screen=False,
        finish_form=form or DriverFinishForm(data,trip=trip,initial={'empty_distance_km':trip.empty_distance_km,'loaded_distance_km':trip.loaded_distance_km,'fuel_rate':(trip.fuel_used_l*100/trip.distance_km).quantize(Decimal('0.01')) if trip.fuel_used_l is not None and trip.distance_km else None}),
        pending_tons=(data or {}).get('actual_tons',''),
        missing_signed=not trip.has_signed_ttn if not trip.route_enabled else bool(context.get('missing_ttn_stops')))
    return render(request,'fleet/driver_finish_details.html',context)


def save_finish_details(trip,form,actor):
    changes=[]
    for name in ['empty_distance_km','loaded_distance_km','distance_km','fuel_used_l']:
        old=getattr(trip,name);value=form.cleaned_data[name]
        if old!=value:
            setattr(trip,name,value)
            changes.append(f'{form.fields[name].label if name in form.fields else "Загальний пробіг, км"}: {old if old is not None else "—"} → {value}')
    if changes:
        if form.cleaned_data.get('fuel_rate') is not None: changes.append(f'Витрата пального: {form.cleaned_data["fuel_rate"]} л/100 км')
        trip.save(update_fields=['empty_distance_km','loaded_distance_km','distance_km','fuel_used_l','updated_at'])
        audit(actor,trip,'Водій вніс підсумки рейсу','; '.join(changes))
    # The caller holds the trip lock and commits this together with completion.
    # A protected audit entry is the per-trip receipt, so a retried request
    # cannot count the same completed journey twice.
    action='Пробіг рейсу додано до одометра'
    if not trip.events.filter(action__in=[action,'Пробіг рейсу не додано до одометра']).exists():
        distance=form.cleaned_data['distance_km']
        updated=Vehicle.objects.filter(pk=trip.vehicle_id,odometer_km__isnull=False,odometer_km__lte=9999999-distance).update(
            odometer_km=F('odometer_km')+distance,odometer_updated_at=timezone.now())
        if updated:
            vehicle=Vehicle.objects.get(pk=trip.vehicle_id)
            audit(actor,trip,action,f'{vehicle.plate}: +{distance} км; одометр {vehicle.odometer_km} км')
        else:
            audit(actor,trip,'Пробіг рейсу не додано до одометра',f'{distance} км; одометр не внесено або перевищено допустимий показник. Оновіть одометр вручну.')
