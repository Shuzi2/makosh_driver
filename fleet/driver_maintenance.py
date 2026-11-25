"""Driver-facing maintenance plans for the currently assigned vehicle."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from .driver_service import assigned_vehicle
from .maintenance import CompleteForm, add_months
from .models import AuditEvent, MaintenanceItem, Trip, Vehicle


class DriverPlanForm(forms.ModelForm):
    revision = forms.DateTimeField(required=False, widget=forms.HiddenInput)
    class Meta:
        model = MaintenanceItem
        fields = ['applies_to', 'title', 'due_date', 'due_km', 'interval_months', 'interval_km', 'notes']
        widgets = {'due_date': forms.DateInput(attrs={'type':'date'}, format='%Y-%m-%d'), 'notes': forms.Textarea(attrs={'rows':3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial['revision'] = self.instance.updated_at.isoformat()

    def clean(self):
        data = super().clean()
        if not data.get('due_date') and data.get('due_km') is None:
            raise forms.ValidationError('Вкажіть дату або показник одометра для ТО.')
        if self.instance.pk and data.get('revision') != self.instance.updated_at:
            raise forms.ValidationError('Запис уже змінився. Відкрийте його повторно.')
        return data


def driver_vehicle(user):
    if user.role != 'driver':
        raise PermissionDenied
    vehicle = assigned_vehicle(user)
    if not vehicle:
        raise PermissionDenied
    return vehicle


@login_required
def plan(request, pk=None):
    vehicle = driver_vehicle(request.user)
    with transaction.atomic():
        locked = Vehicle.objects.select_for_update().get(pk=vehicle.pk)
        current = assigned_vehicle(request.user)
        if not current or current.pk != locked.pk:
            raise PermissionDenied
        item = get_object_or_404(MaintenanceItem.objects.select_for_update(), pk=pk, vehicle=locked, kind='service', completed_at__isnull=True) if pk else MaintenanceItem(vehicle=locked,kind='service',reminder_days=7,reminder_km=1000)
        form = DriverPlanForm(request.POST or None, instance=item)
        if request.method == 'POST' and form.is_valid():
            if not item.pk: item.created_by=request.user
            item=form.save()
            AuditEvent.objects.create(actor=request.user,action='Водій оновив план ТО',detail=f'{locked.plate}: {item.title}'[:1500])
            from .maintenance_notifications import notify
            transaction.on_commit(lambda: notify(item,'change:'+item.updated_at.isoformat(),f'{request.user.full_name}: ТО {locked.plate} · {item.title}'),robust=True)
            messages.success(request,'План ТО збережено. Логіст бачить його в розділі ТО і ремонт.')
            return redirect('driver_service')
    return render(request,'fleet/driver_maintenance_form.html',{'service_screen':True,'form':form,'vehicle':vehicle,'title':'Планове ТО'})


class DriverOdometerForm(forms.Form):
    odometer = forms.IntegerField(label='Показник одометра, км', min_value=0, max_value=9999999, widget=forms.NumberInput(attrs={'inputmode':'numeric'}))
    revision = forms.CharField(required=False, widget=forms.HiddenInput)


@login_required
def odometer(request):
    vehicle=driver_vehicle(request.user)
    with transaction.atomic():
        vehicle=Vehicle.objects.select_for_update().get(pk=vehicle.pk)
        current=assigned_vehicle(request.user)
        if not current or current.pk!=vehicle.pk: raise PermissionDenied
        revision=vehicle.odometer_updated_at.isoformat() if vehicle.odometer_updated_at else ''
        busy=Trip.objects.filter(vehicle=vehicle,status__in=Trip.ACTIVE).exists()
        form=DriverOdometerForm(request.POST or None,initial={'odometer':vehicle.odometer_km,'revision':revision})
        if request.method=='POST' and form.is_valid():
            if busy: form.add_error(None,'Оновити одометр можна після завершення поточного рейсу.')
            elif form.cleaned_data['revision']!=revision: form.add_error(None,'Одометр уже змінився. Відкрийте сторінку повторно.')
            else:
                old=vehicle.odometer_km;vehicle.odometer_km=form.cleaned_data['odometer'];vehicle.odometer_updated_at=timezone.now();vehicle.save(update_fields=['odometer_km','odometer_updated_at'])
                AuditEvent.objects.create(actor=request.user,action='Водій уточнив одометр',detail=f'{vehicle.plate}: {old} → {vehicle.odometer_km} км')
                messages.success(request,'Показник одометра збережено.')
                return redirect('driver_service')
    return render(request,'fleet/driver_maintenance_form.html',{'service_screen':True,'form':form,'vehicle':vehicle,'title':'Показник одометра','busy':busy})


@login_required
def complete(request,pk):
    vehicle=driver_vehicle(request.user)
    with transaction.atomic():
        vehicle=Vehicle.objects.select_for_update().get(pk=vehicle.pk)
        current=assigned_vehicle(request.user)
        if not current or current.pk!=vehicle.pk: raise PermissionDenied
        item=get_object_or_404(MaintenanceItem.objects.select_for_update(),pk=pk,vehicle=vehicle,kind='service',completed_at__isnull=True)
        day=timezone.localdate()
        revision=vehicle.odometer_updated_at.isoformat() if vehicle.odometer_updated_at else ''
        initial={'revision':item.updated_at.isoformat(),'vehicle_revision':revision,'completed_on':day,'odometer':vehicle.odometer_km,
                 'next_due_date':add_months(day,item.interval_months) if item.interval_months else None,
                 'next_due_km':vehicle.odometer_km+item.interval_km if vehicle.odometer_km is not None and item.interval_km else None}
        form=CompleteForm(request.POST or None,initial=initial)
        if request.method=='POST' and form.is_valid():
            data=form.cleaned_data
            if data['revision']!=item.updated_at: form.add_error(None,'Запис уже змінився. Відкрийте його повторно.')
            elif data['vehicle_revision']!=revision: form.add_error(None,'Одометр уже змінився. Відкрийте форму повторно.')
            elif data['odometer'] is not None and data['odometer']!=vehicle.odometer_km and Trip.objects.filter(vehicle=vehicle,status__in=Trip.ACTIVE).exists(): form.add_error('odometer','Змінити одометр можна після завершення рейсу.')
            else:
                item.completed_at=timezone.now();item.completed_on=data['completed_on'];item.completed_odometer=data['odometer'];item.completed_by=request.user;item.result=data['result'];item.save()
                if data['odometer'] is not None and data['completed_on']==day and data['odometer']!=vehicle.odometer_km:
                    vehicle.odometer_km=data['odometer'];vehicle.odometer_updated_at=timezone.now();vehicle.save(update_fields=['odometer_km','odometer_updated_at'])
                if data['next_due_date'] or data['next_due_km'] is not None:
                    fields=['vehicle','kind','applies_to','title','reminder_days','reminder_km','interval_months','interval_km','notes']
                    MaintenanceItem.objects.create(**{f:getattr(item,f) for f in fields},due_date=data['next_due_date'],due_km=data['next_due_km'],previous=item,created_by=request.user)
                AuditEvent.objects.create(actor=request.user,action='Водій виконав ТО',detail=f'{vehicle.plate}: {item.title}; {item.completed_on}'[:1500])
                from .maintenance_notifications import notify
                transaction.on_commit(lambda: notify(item,'complete:'+item.updated_at.isoformat(),f'{request.user.full_name}: виконано ТО {vehicle.plate} · {item.title}'),robust=True)
                messages.success(request,'Виконання збережено в історії ТО.')
                return redirect('driver_service')
    return render(request,'fleet/driver_maintenance_form.html',{'service_screen':True,'form':form,'vehicle':vehicle,'title':'Виконання ТО'})
