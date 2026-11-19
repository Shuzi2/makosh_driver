from decimal import Decimal
from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseModelFormSet, modelformset_factory
from .models import Trip, TripStop
from .forms import TripForm


class StopForm(forms.ModelForm):
    point_confirmed=forms.BooleanField(required=False,widget=forms.HiddenInput)
    contact_phone=forms.CharField(label='Телефон',required=False,max_length=40,widget=forms.TextInput(attrs={'type':'tel','placeholder':'+380…'}))
    contact_phone_alt=forms.CharField(label='Додатковий телефон',required=False,max_length=40,widget=forms.TextInput(attrs={'type':'tel','placeholder':'+380…'}))
    class Meta:
        model=TripStop
        fields=['kind','address','settlement_code','settlement_name','region','district','community','lat','lon','contact_name','contact_phone','contact_phone_alt','cargo','planned_tons','actual_tons','ttn_number','notes']
        widgets={'settlement_code':forms.HiddenInput,'settlement_name':forms.HiddenInput,'address':forms.TextInput(attrs={'placeholder':'Назва села або міста…','autocomplete':'off'}),'notes':forms.Textarea(attrs={'rows':2}),'lat':forms.NumberInput(attrs={'step':'0.000001'}),'lon':forms.NumberInput(attrs={'step':'0.000001'})}
        help_texts={'ttn_number':'Кілька номерів — через крапку з комою. Фото прив’язуються до номера ТТН.','actual_tons':'Необов’язково. Вага саме цієї операції, якщо вона відома.'}
    def clean_contact_phone(self): return TripForm.normalize_contact_phone(self.cleaned_data.get('contact_phone',''))
    def clean_contact_phone_alt(self): return TripForm.normalize_contact_phone(self.cleaned_data.get('contact_phone_alt',''))
    def clean(self):
        data=super().clean()
        from .settlements import lookup,resolve_address,normalize
        source=lookup()[0].get(data.get('settlement_code',''))
        address=data.get('address','')
        if source and normalize(address.split(',')[0]) not in [normalize(source['name']),normalize(source['title'])]:
            self.add_error('address','Населений пункт змінено. Виберіть його з довідника або перейдіть до ручного введення.')
        elif not source and data.get('settlement_code'):
            self.add_error('address','Цього коду немає в довіднику. Виберіть населений пункт повторно.')
        else:
            source=source or (resolve_address(address) if not any(data.get(f) for f in ['region','district','community']) else None)
            if source:
                data.update(settlement_code=source['code'],settlement_name=source['title'],region=source['region'],district=source['district'],community=source['community'])
            elif address: data['settlement_name']=address.split(',')[0].strip()
        original=getattr(self,'original',{})
        needs_area=not self.instance.pk or address!=original.get('address',self.instance.address)
        special=data.get('settlement_code') in ['UA80000000000093317','UA85000000000065278']
        if needs_area:
            for name in (['region'] if special else ['region','district','community']):
                if not data.get(name): self.add_error(name,'Укажіть це поле або виберіть населений пункт із кодифікатора.')
        if data.get('contact_name') and not data.get('contact_phone'):
            self.add_error('contact_phone','Укажіть телефон контактної особи.')
        if data.get('contact_phone_alt') and not data.get('contact_phone'):
            self.add_error('contact_phone','Спочатку вкажіть основний телефон.')
        if data.get('contact_phone_alt') and data.get('contact_phone_alt')==data.get('contact_phone'):
            self.add_error('contact_phone_alt','Цей номер уже вказаний як основний.')
        if (data.get('lat') is None)!=(data.get('lon') is None): self.add_error('lat','Потрібні обидві координати.')
        original=getattr(self,'original',{})
        if original and data.get('address')!=original['address'] and data.get('lat') is not None and (data.get('lat'),data.get('lon'))==(original['lat'],original['lon']) and not data.get('point_confirmed'):
            self.add_error('address','Адресу змінено. Повторно підтвердьте точку на карті або приберіть координати.')
        numbers=[n.strip() for n in __import__('re').split(r'[,;\n]+',data.get('ttn_number','')) if n.strip()]
        if any(len(n)>100 for n in numbers): self.add_error('ttn_number','Один номер ТТН — до 100 символів.')
        return data


class RouteFormSet(BaseModelFormSet):
    def add_fields(self,form,index):
        super().add_fields(form,index)
        form.fields['ORDER'].widget=forms.HiddenInput()
    def __init__(self,*args,trip=None,**kwargs):
        self.trip=trip
        queryset=TripStop.objects.filter(trip=trip).order_by('position','pk') if trip else TripStop.objects.none()
        self.original={s.pk:{f:getattr(s,f) for f in StopForm.Meta.fields+['position','state']} for s in queryset}
        super().__init__(*args,queryset=queryset,**kwargs)
        for form in self.forms:
            form.fields['id'].queryset=queryset
            form.fields['ORDER'].widget=forms.HiddenInput()
            form.original=self.original.get(form.instance.pk,{})
            if form.instance.pk and (form.original['state']=='cancelled' or trip and trip.status=='cancelled'):
                for field in form.fields.values(): field.disabled=True
    def clean(self):
        super().clean()
        if any(self.errors): return
        if self.initial_form_count()!=len(self.original): raise ValidationError('Кількість наявних точок змінилася. Відкрийте актуальний маршрут.')
        ids=[f.cleaned_data['id'].pk for f in self.forms if f.cleaned_data.get('id')]
        if len(ids)!=len(set(ids)) or set(ids)!=set(self.original): raise ValidationError('Склад точок змінився. Відкрийте актуальний маршрут.')
        if self.trip and self.trip.status=='cancelled':
            if any(not f.instance.pk for f in self.forms): raise ValidationError('До скасованого рейсу не можна додавати точки.')
            return
        ordered=self.ordered_forms
        live=[f for f in ordered if not f.instance.pk or self.original[f.instance.pk]['state']!='cancelled']
        if not 2<=len(live)<=30: raise ValidationError('У маршруті має бути від 2 до 30 точок.')
        locked=[pk for pk,old in self.original.items() if old['state'] in ['completed','arrived']]
        if [f.instance.pk for f in live[:len(locked)]]!=locked: raise ValidationError('Виконані точки й поточна операція мають залишатися на початку в тому самому порядку.')
        for form in self.forms:
            old=self.original.get(form.instance.pk)
            if old and old['state'] in ['completed','arrived']:
                if form.cleaned_data.get('DELETE') or form.cleaned_data.get('kind')!=old['kind']: raise ValidationError('Виконану або розпочату точку не можна видалити чи змінити тип операції.')
        if self.trip and self.trip.status in ['completed','cancelled'] and any(not f.instance.pk for f in live): raise ValidationError('До завершеного чи скасованого рейсу не можна додавати нові точки.')
        if live[0].cleaned_data['kind']!='load' or live[-1].cleaned_data['kind']!='unload': raise ValidationError('Маршрут має починатися завантаженням і закінчуватися розвантаженням.')
        balances={};total=Decimal('0')
        for form in live:
            data=form.cleaned_data;key=data['cargo'].strip().casefold()
            amount=data['planned_tons']*(1 if data['kind']=='load' else -1)
            balances[key]=balances.get(key,Decimal('0'))+amount;total+=amount
            if balances[key]<0: raise ValidationError(f'Розвантаження «{data["cargo"]}» перевищує попередні завантаження. Перевірте вагу й порядок точок.')
            if total>60: raise ValidationError('За планом в авто одночасно буде понад 60 т. Перевірте вагу та порядок точок.')
        if any(value!=0 for value in balances.values()): raise ValidationError('Планова вага всіх завантажень і розвантажень має збігатися для кожного вантажу.')

    def save_route(self,trip,actor,copying=False):
        from .routes import audit,refresh_signed,sync_trip,notify_route
        if trip.status=='cancelled': return []
        changes=[]
        for form in self.deleted_forms:
            if form.instance.pk:
                stop=form.instance;stop.state='cancelled';stop.save(update_fields=['state'])
                text=f'Точка {self.original[stop.pk]["position"]}: {self.original[stop.pk]["address"]}. Зупинку скасовано.'
                audit(actor,trip,'Скасовано точку маршруту',text);changes.append(text)
        position=0
        for form in self.ordered_forms:
            stop=form.save(commit=False)
            if stop.pk and self.original[stop.pk]['state']=='cancelled': continue
            position+=1;old=self.original.get(stop.pk)
            stop.trip=trip;stop.position=position
            if not old or any(getattr(stop,f)!=old[f] for f in StopForm.Meta.fields+['position']):
                if old and stop.actual_tons!=old['actual_tons']: stop.weight_revision+=1
                if old and stop.ttn_number!=old['ttn_number']: stop.documents_revision+=1
                stop.save();refresh_signed(stop)
                if old:
                    details=[]
                    for field in StopForm.Meta.fields+['position']:
                        if getattr(stop,field)!=old[field]:
                            label='Порядок' if field=='position' else stop._meta.get_field(field).verbose_name
                            def display(value): return ('так' if value else 'ні') if isinstance(value,bool) else str(value) if value is not None else '—'
                            details.append(f'{label}: {display(old[field])} → {display(getattr(stop,field))}')
                    text=f'Точка {position}, {stop.address}. '+'; '.join(details)
                    audit(actor,trip,'Оновлено точку маршруту',text)
                else:
                    text=f'Точка {position}: {stop.get_kind_display()}, {stop.address}, {stop.cargo}, {stop.planned_tons} т.'
                    audit(actor,trip,'Додано точку маршруту',text)
                changes.append(text)
        sync_trip(trip)
        if trip.is_active and not trip.stops.filter(state__in=['pending','arrived']).exists():
            from django.utils import timezone
            trip.status='completed';trip.completed_at=timezone.now();trip.status_changed_at=trip.completed_at
            trip.save(update_fields=['status','completed_at','status_changed_at','updated_at'])
            from .driver_reports import snapshot_completion_rates
            snapshot_completion_rates(trip)
            audit(actor,trip,'Завершено рейс','Після коригування маршруту всі чинні точки вже виконано.')
        if self.trip and changes: notify_route(trip,'Маршрут оновлено: '+ '; '.join(changes)[:1800])
        return changes


StopFormSet=modelformset_factory(TripStop,form=StopForm,formset=RouteFormSet,extra=0,can_order=True,can_delete=True,max_num=30,validate_max=True,absolute_max=60)


class RouteTripEditForm(forms.ModelForm):
    revision=forms.DateTimeField(widget=forms.HiddenInput)
    class Meta:
        model=Trip
        fields=['planned_date','distance_km','notes']
        widgets={'planned_date':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'),'notes':forms.Textarea(attrs={'rows':3})}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.initial['revision']=self.instance.updated_at.isoformat()
    def clean(self):
        data=super().clean()
        if data.get('revision')!=self.instance.updated_at: raise ValidationError('Рейс уже змінився. Відкрийте актуальний маршрут перед збереженням.')
        return data


def initial_stops(source=None):
    if source and source.route_enabled:
        return [{field:getattr(stop,field) for field in StopForm.Meta.fields if field not in ['actual_tons','ttn_number','original_received']} for stop in source.stops.exclude(state='cancelled')]
    return [{'kind':kind,'address':getattr(source,side,'') if source else '', 'lat':getattr(source,side+'_lat',None) if source else None,'lon':getattr(source,side+'_lon',None) if source else None,'contact_name':getattr(source,'loading_contact_name' if kind=='load' else 'contact_name','') if source else '', 'contact_phone':getattr(source,'loading_contact_phone' if kind=='load' else 'contact_phone','') if source else '', 'cargo':source.cargo if source else '', 'planned_tons':source.planned_tons if source else None} for kind,side in [('load','origin'),('unload','destination')]]


def create_formset(data=None,source=None):
    if data is not None:
        result=StopFormSet(data,prefix='stops')
        if source:
            for form,reference in zip(result.forms,initial_stops(source)): form.original=reference
        return result
    initial=initial_stops(source)
    # Initial extra forms are required because the queryset is deliberately empty.
    factory=modelformset_factory(TripStop,form=StopForm,formset=RouteFormSet,extra=len(initial),can_order=True,can_delete=True,max_num=30,validate_max=True,absolute_max=60)
    return factory(prefix='stops',initial=initial)
