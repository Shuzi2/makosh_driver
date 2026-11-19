import calendar
from datetime import timedelta
from django import forms
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Prefetch
from django.shortcuts import get_object_or_404,render,redirect
from django.utils import timezone
from .models import MaintenanceItem,Vehicle,VehicleRepair,AuditEvent


class MaintenanceForm(forms.ModelForm):
    revision=forms.DateTimeField(required=False,widget=forms.HiddenInput)
    class Meta:
        model=MaintenanceItem
        fields=['vehicle','kind','applies_to','title','document_number','due_date','due_km','reminder_days','reminder_km','interval_months','interval_km','notes']
        widgets={'due_date':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'),'notes':forms.Textarea(attrs={'rows':3})}
        labels={'vehicle':'Автомобіль'}
        help_texts={'due_km':'Для ТО за пробігом. Використовується внесений показник одометра авто.','interval_months':'Необов’язково: період для наступного нагадування.','interval_km':'Необов’язково: інтервал між обслуговуваннями.'}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.initial['revision']=self.instance.updated_at.isoformat() if self.instance.pk else ''
    def clean(self):
        data=super().clean()
        if not data.get('due_date') and data.get('due_km') is None: self.add_error(None,'Укажіть дату, пробіг або обидва терміни.')
        if data.get('kind')=='document' and data.get('due_km') is not None: self.add_error('due_km','Для документа використовуйте дату закінчення.')
        if data.get('kind')=='document' and data.get('interval_km') is not None: self.add_error('interval_km','Документ повторюється за місяцями, а не кілометрами.')
        if self.instance.pk and data.get('revision')!=self.instance.updated_at: self.add_error(None,'Запис уже змінився. Відкрийте актуальні дані.')
        return data


class OdometerForm(forms.Form):
    odometer_km=forms.IntegerField(label='Показник одометра, км',min_value=0,max_value=9999999)
    revision=forms.CharField(widget=forms.HiddenInput,required=False)


class CompleteForm(forms.Form):
    revision=forms.DateTimeField(widget=forms.HiddenInput)
    vehicle_revision=forms.CharField(widget=forms.HiddenInput,required=False)
    completed_on=forms.DateField(label='Фактична дата',widget=forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'))
    odometer=forms.IntegerField(label='Одометр на момент виконання, км',min_value=0,max_value=9999999,required=False)
    result=forms.CharField(label='Що виконано / поновлено',required=False,max_length=2000,widget=forms.Textarea(attrs={'rows':3}))
    next_due_date=forms.DateField(label='Наступна дата',required=False,widget=forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'))
    next_due_km=forms.IntegerField(label='Наступний пробіг для ТО, км',required=False,min_value=0,max_value=9999999)
    def clean(self):
        data=super().clean();day=data.get('completed_on')
        if day and day>timezone.localdate(): self.add_error('completed_on','Дата виконання не може бути в майбутньому.')
        if day and data.get('next_due_date') and data['next_due_date']<=day: self.add_error('next_due_date','Наступний термін має бути після виконання.')
        if data.get('odometer') is not None and data.get('next_due_km') is not None and data['next_due_km']<=data['odometer']: self.add_error('next_due_km','Наступний пробіг має бути більшим за поточний.')
        return data


def snapshot():
    rows=[]
    for item in MaintenanceItem.objects.filter(completed_at__isnull=True).select_related('vehicle'):
        state=item.status_info();rows.append({'item':item,'state':state})
    order={'overdue':0,'soon':1,'unknown':2,'ok':3}
    return sorted(rows,key=lambda row:(order[row['state']['key']],row['item'].due_date or timezone.localdate()+timedelta(days=36500),row['item'].vehicle.plate,row['item'].pk))


def maintenance(request):
    rows=snapshot();counts={key:sum(r['state']['key']==key for r in rows) for key in ['overdue','soon','unknown','ok']}
    vehicle=request.GET.get('vehicle','');kind=request.GET.get('kind','');status=request.GET.get('status','')
    vehicles=list(Vehicle.objects.all())
    service_by_vehicle={v.pk:[] for v in vehicles}
    for row in rows:
        if row['item'].kind=='service': service_by_vehicle[row['item'].vehicle_id].append(row)
    service_overview=[]
    for v in vehicles:
        if vehicle and str(v.pk)!=vehicle: continue
        plans=service_by_vehicle[v.pk]
        by_date=min((r for r in plans if r['item'].due_date),key=lambda r:(r['item'].due_date,r['item'].pk),default=None)
        by_km=min((r for r in plans if r['item'].due_km is not None),key=lambda r:(r['item'].due_km,r['item'].pk),default=None)
        service_overview.append({'vehicle':v,'count':len(plans),'by_date':by_date,'by_km':by_km})
    if vehicle: rows=[r for r in rows if str(r['item'].vehicle_id)==vehicle]
    if kind in ['service','document']: rows=[r for r in rows if r['item'].kind==kind]
    if status: rows=[r for r in rows if r['state']['key']==status]
    history=MaintenanceItem.objects.filter(completed_at__isnull=False).select_related('vehicle','completed_by').order_by('-completed_at')
    if vehicle: history=history.filter(vehicle_id=vehicle) if vehicle.isdigit() else history.none()
    if kind in ['service','document']: history=history.filter(kind=kind)
    repair_vehicles=Vehicle.objects.filter(in_repair=True).prefetch_related(Prefetch('repairs',queryset=VehicleRepair.objects.filter(finished_at__isnull=True).select_related('created_by'),to_attr='active_repairs'))
    repair_history=VehicleRepair.objects.filter(finished_at__isnull=False).select_related('vehicle','finished_by').order_by('-finished_at','-pk')
    if vehicle:
        repair_vehicles=repair_vehicles.filter(pk=vehicle) if vehicle.isdigit() else repair_vehicles.none()
        repair_history=repair_history.filter(vehicle_id=vehicle) if vehicle.isdigit() else repair_history.none()
    return render(request,'fleet/maintenance.html',{'rows':rows,'counts':counts,'vehicles':vehicles,'service_overview':service_overview,'selected_vehicle':vehicle,'selected_kind':kind,'selected_status':status,'history':history[:100],'repair_vehicles':list(repair_vehicles),'repair_history':repair_history[:100]})


def edit(request,pk=None):
    with transaction.atomic():
        item=get_object_or_404(MaintenanceItem.objects.select_for_update(),pk=pk,completed_at__isnull=True) if pk else MaintenanceItem()
        initial={'vehicle':request.GET.get('vehicle')} if request.GET.get('vehicle','').isdigit() else {}
        form=MaintenanceForm(request.POST if request.method=='POST' else None,instance=item,initial=initial)
        if request.method=='POST' and form.is_valid():
            saved=form.save(commit=False)
            if not saved.pk: saved.created_by=request.user
            elif 'vehicle' in form.changed_data:
                saved.tractor_plate=saved.vehicle.plate;saved.trailer_plate=saved.vehicle.trailer_plate or ''
            saved.save();AuditEvent.objects.create(actor=request.user,action='Оновлено план ТО / документа',detail=f'{saved.vehicle.plate}: {saved.title}; дата {saved.due_date or "—"}, пробіг {saved.due_km if saved.due_km is not None else "—"}')
            messages.success(request,'Термін збережено. Нагадування з’явиться у розділі ТО й документів.')
            return redirect('maintenance')
    return render(request,'fleet/maintenance_edit.html',{'form':form,'item':item})


def add_months(day,months):
    year,month=divmod(day.year*12+day.month-1+months,12);month+=1
    return day.replace(year=year,month=month,day=min(day.day,calendar.monthrange(year,month)[1]))


def complete(request,pk):
    with transaction.atomic():
        initial=get_object_or_404(MaintenanceItem,pk=pk)
        vehicle=Vehicle.objects.select_for_update().get(pk=initial.vehicle_id)
        item=MaintenanceItem.objects.select_for_update().get(pk=pk)
        if item.completed_at:
            messages.info(request,'Цей запис уже виконано.');return redirect('maintenance')
        day=timezone.localdate()
        vehicle_revision=vehicle.odometer_updated_at.isoformat() if vehicle.odometer_updated_at else ''
        values={'revision':item.updated_at.isoformat(),'vehicle_revision':vehicle_revision,'completed_on':day,'odometer':vehicle.odometer_km if item.kind=='service' else None,
                'next_due_date':add_months(day,item.interval_months) if item.interval_months else None,
                'next_due_km':vehicle.odometer_km+item.interval_km if item.kind=='service' and vehicle.odometer_km is not None and item.interval_km else None}
        form=CompleteForm(request.POST if request.method=='POST' else None,initial=values)
        if item.kind=='document':
            form.fields['odometer'].widget=forms.HiddenInput();form.fields['next_due_km'].widget=forms.HiddenInput()
        if request.method=='POST' and form.is_valid():
            data=form.cleaned_data
            if data['revision']!=item.updated_at: form.add_error(None,'Запис уже змінився. Відкрийте актуальні дані.')
            elif data['odometer'] is not None and data['vehicle_revision']!=vehicle_revision: form.add_error('odometer','Одометр уже змінився. Відкрийте актуальні дані.')
            elif item.kind=='document' and data.get('next_due_km') is not None: form.add_error('next_due_km','Для документа використовуйте дату.')
            else:
                item.completed_at=timezone.now();item.completed_on=data['completed_on'];item.completed_odometer=data['odometer'];item.completed_by=request.user;item.result=data['result'];item.save()
                if item.kind=='service' and data['odometer'] is not None and data['completed_on']==day and (vehicle.odometer_km is None or data['odometer']>=vehicle.odometer_km):
                    vehicle.odometer_km=data['odometer'];vehicle.odometer_updated_at=timezone.now();vehicle.save(update_fields=['odometer_km','odometer_updated_at'])
                if data['next_due_date'] or data['next_due_km'] is not None:
                    fields=['vehicle','kind','applies_to','title','reminder_days','reminder_km','interval_months','interval_km','notes']
                    MaintenanceItem.objects.create(**{f:getattr(item,f) for f in fields},due_date=data['next_due_date'],due_km=data['next_due_km'],previous=item,created_by=request.user)
                AuditEvent.objects.create(actor=request.user,action='Виконано ТО / поновлено документ',detail=f'{vehicle.plate}: {item.title}; {item.completed_on}; одометр {item.completed_odometer if item.completed_odometer is not None else "—"}; {item.result}')
                messages.success(request,'Виконання збережено в історії.'+(' Наступне нагадування створено.' if data['next_due_date'] or data['next_due_km'] is not None else ''))
                return redirect('maintenance')
    return render(request,'fleet/maintenance_complete.html',{'form':form,'item':item,'vehicle':vehicle})


def odometer(request,pk):
    with transaction.atomic():
        vehicle=get_object_or_404(Vehicle.objects.select_for_update(),pk=pk)
        revision=vehicle.odometer_updated_at.isoformat() if vehicle.odometer_updated_at else ''
        form=OdometerForm(request.POST if request.method=='POST' else None,initial={'odometer_km':vehicle.odometer_km,'revision':revision})
        if request.method=='POST' and form.is_valid():
            if form.cleaned_data['revision']!=revision: form.add_error(None,'Показник уже змінився. Оновіть дані.')
            else:
                old=vehicle.odometer_km;vehicle.odometer_km=form.cleaned_data['odometer_km'];vehicle.odometer_updated_at=timezone.now();vehicle.save(update_fields=['odometer_km','odometer_updated_at'])
                AuditEvent.objects.create(actor=request.user,action='Уточнено одометр',detail=f'{vehicle.plate}: {old if old is not None else "—"} → {vehicle.odometer_km} км')
                messages.success(request,'Одометр оновлено. Терміни ТО перераховані.');return redirect('maintenance')
    return render(request,'fleet/odometer_edit.html',{'form':form,'vehicle':vehicle})
