"""Small, revision-checked editors for dispatcher trip details."""
from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, render, redirect
from .forms import TripEditForm, TripFactsForm
from .models import Trip
from django import forms
from decimal import Decimal


class RouteWeightsForm(forms.Form):
    revision=forms.DateTimeField(widget=forms.HiddenInput)

    def __init__(self,data=None,*,trip):
        super().__init__(data)
        self.trip=trip
        self.points=list(trip.stops.filter(kind='unload').exclude(state='cancelled').order_by('position','pk'))
        self.initial['revision']=trip.updated_at.isoformat()
        for point in self.points:
            name='actual_tons' if len(self.points)==1 else f'weight_{point.pk}'
            self.fields[name]=forms.DecimalField(label=f'Розвантаження · {point.driver_place["name"]}, т',
                required=False,min_value=Decimal('0.01'),max_value=60,max_digits=5,decimal_places=2,
                widget=forms.NumberInput(attrs={'step':'0.01','placeholder':'Не вказана'}),
                help_text='Можна залишити порожнім, якщо вага ще невідома.')
            self.initial[name]=point.actual_tons

    def clean(self):
        data=super().clean()
        if data.get('revision') and data['revision']!=self.trip.updated_at:
            self.add_error(None,'Рейс уже змінився. Відкрийте актуальні дані та повторіть редагування.')
        return data

    def save_weights(self,actor):
        from .routes import sync_trip,audit
        changed=False
        for point in self.points:
            name='actual_tons' if len(self.points)==1 else f'weight_{point.pk}'
            value=self.cleaned_data[name]
            if point.actual_tons==value: continue
            old=point.actual_tons;point.actual_tons=value;point.weight_revision+=1
            point.save(update_fields=['actual_tons','weight_revision'])
            audit(actor,self.trip,'Фактична вага точки',f'Точка {point.position}, {point.address}: {old if old is not None else "—"} → {value if value is not None else "—"} т')
            changed=True
        if changed: sync_trip(self.trip)
        return changed

GROUPS={
    'origin':('Місце завантаження',['origin','origin_lat','origin_lon']),
    'destination':('Місце розвантаження',['destination','destination_lat','destination_lon']),
    'loading_contact':('Контакт на завантаження',['loading_contact_name','loading_contact_phone']),
    'contact':('Контакт на розвантаження',['contact_name','contact_phone']),
    'cargo':('Вантаж',['cargo']),
    'planned_tons':('Планова вага',['planned_tons']),
    'planned_date':('Планова дата',['planned_date']),
    'actual_tons':('Фактична вага',['actual_tons']),
    'distance_km':('Пробіг рейсу',['distance_km']),
    'fuel_used_l':('Витрачено пального',['fuel_used_l']),
    'ttn_number':('Номер ТТН',['ttn_number']),
    'notes':('Інструкція водію',['notes']),
}

def edit_form(trip,key,data=None):
    if trip.route_enabled and key=='actual_tons':
        return RouteWeightsForm(data,trip=trip)
    fields=GROUPS[key][1]
    form=(TripFactsForm if key in ['actual_tons','distance_km','fuel_used_l','ttn_number'] else TripEditForm)(data,instance=trip)
    for name in list(form.fields):
        if name not in fields+['revision']+([key+'_point_confirmed'] if key in ['origin','destination'] else []): del form.fields[name]
    for field in form:
        field.field.widget.attrs['id']=f'quick-{key}-{field.name}'
    return form

def editors(trip):
    return [{'key':key,'title':title,'form':edit_form(trip,key)} for key,(title,fields) in GROUPS.items() if not trip.route_enabled or key in ['planned_date','distance_km','fuel_used_l','notes','actual_tons']]

def quick_edit(request,pk,key):
    from .views import event
    from django.http import Http404
    if key not in GROUPS: raise Http404
    if get_object_or_404(Trip,pk=pk).route_enabled and key not in ['planned_date','distance_km','fuel_used_l','notes','actual_tons']: return redirect('trip_edit',pk=pk)
    if request.method=='POST':
        with transaction.atomic():
            trip=get_object_or_404(Trip.objects.select_for_update(),pk=pk)
            fields=GROUPS[key][1]
            before={field:getattr(trip,field) for field in fields}
            form=edit_form(trip,key,request.POST)
            if form.is_valid():
                if trip.route_enabled and key=='actual_tons':
                    changed=form.save_weights(request.user)
                    messages.success(request,'Фактичну вагу збережено. Підсумок рейсу та звіт оновлено.') if changed else messages.info(request,'Дані не змінилися.')
                    return redirect('trip_detail',pk=pk)
                changed=[field for field in fields if before[field]!=form.cleaned_data[field]]
                if changed:
                    edited=form.save(commit=False)
                    edited.save(update_fields=changed+['updated_at'])
                    from .push import queue_trip_change
                    queue_trip_change(edited)
                    for field in changed:
                        old=str(before[field] if before[field] is not None else '') or 'не вказано'
                        new=str(form.cleaned_data[field] if form.cleaned_data[field] is not None else '') or 'не вказано'
                        event(request.user,'Оновлено інформацію рейсу',trip,f'{form.fields[field].label}: {old[:650]} → {new[:650]}')
                    messages.success(request,'Зміни збережено в рейсі та його історії.')
                else: messages.info(request,'Дані не змінилися.')
                return redirect('trip_detail',pk=pk)
    else:
        trip=get_object_or_404(Trip,pk=pk)
        form=edit_form(trip,key)
    return render(request,'fleet/trip_quick_edit.html',{'trip':trip,'form':form,'key':key,'edit_title':GROUPS[key][0]})
