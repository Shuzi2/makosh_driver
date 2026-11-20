"""Service needs shared by drivers and fleet managers."""
import uuid
import re
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_datetime
from django.db.models import Q
from django.views.decorators.http import require_POST
from .models import AuditEvent, DriverServiceRequest, DriverServiceNotification, MaintenanceItem, Trip, Vehicle


def assigned_vehicle(user):
    active=Trip.objects.filter(driver=user,status__in=Trip.ACTIVE).select_related('vehicle').first()
    return active.vehicle if active else Vehicle.objects.filter(default_driver=user).first()


class ServiceForm(forms.ModelForm):
    request_key=forms.UUIDField(widget=forms.HiddenInput,required=False)
    revision=forms.CharField(widget=forms.HiddenInput,required=False)
    class Meta:
        model=DriverServiceRequest
        fields=['category','title','description','parts','urgent','due_date','due_km']
        widgets={'description':forms.Textarea(attrs={'rows':3,'placeholder':'Наприклад: пічка не гріє, потрібна діагностика'}),'parts':forms.TextInput(attrs={'placeholder':'Наприклад: гальмівні колодки'}),'due_date':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'),'due_km':forms.NumberInput(attrs={'inputmode':'numeric'})}

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        if self.instance.pk: self.initial['revision']=self.instance.updated_at.isoformat()
        else: self.initial.setdefault('request_key',uuid.uuid4())

    def clean(self):
        data=super().clean()
        if not self.instance.pk and not data.get('request_key'): self.add_error(None,'Оновіть сторінку та повторіть збереження.')
        return data


@login_required
def driver_service(request):
    if request.user.role!='driver': raise PermissionDenied
    vehicle=assigned_vehicle(request.user)
    requests=DriverServiceRequest.objects.filter(driver=request.user).select_related('vehicle','updated_by')
    upcoming=MaintenanceItem.objects.filter(vehicle=vehicle,kind='service',completed_at__isnull=True).select_related('vehicle') if vehicle else MaintenanceItem.objects.none()
    return render(request,'fleet/driver_service.html',{'service_screen':True,'vehicle':vehicle,'service_requests':requests,'upcoming_service':[{'item':item,'state':item.status_info()} for item in upcoming]})


@login_required
def driver_service_edit(request,pk=None):
    if request.user.role!='driver': raise PermissionDenied
    with transaction.atomic():
        record=get_object_or_404(DriverServiceRequest.objects.select_for_update(),pk=pk,driver=request.user) if pk else None
        vehicle=record.vehicle if record else assigned_vehicle(request.user)
        if record and record.status=='done':
            messages.error(request,'Виконаний запис змінити не можна. Створіть новий запис за потреби.')
            return redirect('driver_service')
        if vehicle is None: return redirect('driver_service')
        before={name:getattr(record,name) for name in ServiceForm.Meta.fields} if record else {}
        form=ServiceForm(request.POST or None,instance=record)
        if request.method=='POST' and form.is_valid():
            if record:
                try: revision=parse_datetime(form.cleaned_data['revision'] or '')
                except ValueError: revision=None
                if revision!=record.updated_at:
                    form.add_error(None,'Запис уже змінився. Відкрийте його повторно перед збереженням.')
                else:
                    item=form.save(commit=False);item.updated_by=request.user;item.save()
                    changes=[f'{form.fields[name].label}: {before[name] if before[name] not in (None, "") else "—"} → {form.cleaned_data[name] if form.cleaned_data[name] not in (None, "") else "—"}' for name in ServiceForm.Meta.fields if before[name]!=form.cleaned_data[name]]
                    if changes:
                        AuditEvent.objects.create(actor=request.user,action='Водій змінив сервісний запис',detail=(f'Запис №{item.pk}, {item.tractor_plate}: '+'; '.join(changes))[:1500])
                        from .push import enqueue_staff
                        enqueue_staff('staff_service')
                    return redirect('driver_service')
            else:
                # Recheck assignment at submission, so a stale form cannot select another vehicle.
                locked=Vehicle.objects.select_for_update().get(pk=vehicle.pk)
                current_vehicle=assigned_vehicle(request.user)
                if not current_vehicle or current_vehicle.pk!=locked.pk:
                    form.add_error(None,'Закріплене авто змінилося. Оновіть сторінку та перевірте автомобіль.')
                else:
                    key=form.cleaned_data['request_key']
                    item,created=DriverServiceRequest.objects.get_or_create(request_key=key,defaults={**{name:form.cleaned_data[name] for name in ServiceForm.Meta.fields},'vehicle':locked,'driver':request.user,'tractor_plate':locked.plate,'trailer_plate':locked.trailer_plate or ''})
                    if item.driver_id!=request.user.pk: raise PermissionDenied
                    if created:
                        AuditEvent.objects.create(actor=request.user,action='Водій додав сервісний запис',detail=f'Запис №{item.pk}, {item.tractor_plate}: {item.title}'[:1500])
                        from .push import enqueue_staff
                        enqueue_staff('staff_service')
                    return redirect('driver_service')
    return render(request,'fleet/driver_service_form.html',{'service_screen':True,'form':form,'vehicle':vehicle,'record':record})


@login_required
def manager_messages(request):
    if not request.user.manages_fleet: raise PermissionDenied
    base=DriverServiceRequest.objects.select_related('vehicle','driver','updated_by')
    status=request.GET.get('status','open')
    if status not in ('open','new','in_progress','done','all'): status='open'
    query=request.GET.get('q','').strip()[:150]
    items=base
    if status=='open': items=items.exclude(status='done')
    elif status!='all': items=items.filter(status=status)
    if query:
        items=items.filter(Q(tractor_plate__icontains=query)|Q(trailer_plate__icontains=query)|Q(driver__full_name__icontains=query)|Q(title__icontains=query)|Q(description__icontains=query)|Q(parts__icontains=query))
    counts={'new':base.filter(status='new').count(),'in_progress':base.filter(status='in_progress').count(),'urgent':base.filter(urgent=True).exclude(status='done').count(),'done':base.filter(status='done').count()}
    items=items.order_by('-urgent','created_at','pk') if status!='done' else items.order_by('-updated_at','-pk')
    items=list(items)
    by_id={item.pk:item for item in items}
    for item in items:
        item.message_history=[{'when':item.created_at,'actor':item.driver.full_name,'text':'Водій надіслав звернення'}]
    if by_id:
        events=AuditEvent.objects.filter(action='Оновлено сервісний запис водія').select_related('actor').order_by('created_at','pk')
        for event in events:
            match=re.match(r'^Запис №(\d+), .*?: (.*?) → (.*?); відповідь: (.*?) → (.*)$',event.detail,flags=re.DOTALL)
            if not match: continue
            item=by_id.get(int(match.group(1)))
            if not item: continue
            actor=event.actor.full_name if event.actor else 'Логіст'
            if match.group(2)!=match.group(3):
                item.message_history.append({'when':event.created_at,'actor':actor,'text':'Стан змінено: '+match.group(3)})
            if match.group(4)!=match.group(5):
                item.message_history.append({'when':event.created_at,'actor':actor,'text':'Логіст залишив або змінив відповідь'})
    return render(request,'fleet/driver_messages.html',{'items':items,'counts':counts,'status':status,'query':query})


@login_required
@require_POST
def manager_service_update(request,pk):
    if not request.user.manages_fleet: raise PermissionDenied
    with transaction.atomic():
        item=get_object_or_404(DriverServiceRequest.objects.select_for_update(),pk=pk)
        status=request.POST.get('status','');note=request.POST.get('manager_note','').strip()
        try: revision=parse_datetime(request.POST.get('revision',''))
        except ValueError: revision=None
        if revision!=item.updated_at: messages.error(request,'Запис уже змінився. Перевірте актуальну інформацію.')
        elif status not in dict(DriverServiceRequest._meta.get_field('status').choices) or len(note)>1000:
            messages.error(request,'Перевірте стан та відповідь (до 1000 символів).')
        else:
            previous=item.get_status_display();old_note=item.manager_note
            item.status=status;item.manager_note=note;item.updated_by=request.user;item.save(update_fields=['status','manager_note','updated_by','updated_at'])
            notice_parts=[]
            if previous!=item.get_status_display():
                notice_parts.append('Стан: '+item.get_status_display())
            if note and note!=old_note:
                notice_parts.append('Відповідь: '+note)
            if notice_parts:
                DriverServiceNotification.objects.create(recipient=item.driver,service_request=item,message=' · '.join(notice_parts)[:1000])
                from .push import queue_service_reply
                queue_service_reply(item.driver_id)
            if previous!=item.get_status_display() or old_note!=note:
                AuditEvent.objects.create(actor=request.user,action='Оновлено сервісний запис водія',detail=f'Запис №{item.pk}, {item.tractor_plate}: {previous} → {item.get_status_display()}; відповідь: {old_note or "—"} → {note or "—"}'[:1500])
            messages.success(request,'Сервісний запис оновлено. Водій побачить стан і відповідь.')
    return redirect('manager_messages')
