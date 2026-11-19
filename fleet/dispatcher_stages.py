"""Explicit dispatcher corrections of route state, with full point history."""
from django import forms
from django.db import transaction, IntegrityError
from django.db.models import Q
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import Trip, Vehicle
from .routes import audit, sync_trip, notify_route


class DispatcherStageForm(forms.Form):
    revision=forms.DateTimeField(widget=forms.HiddenInput)
    stage=forms.ChoiceField(label='Перевести на етап',choices=[('','Оберіть етап'),('planned','Призначено — очікує прийняття'),
        ('accepted','Прийнято'),('loading','Завантаження'),('road','У дорозі'),('unloading','Розвантаження'),('completed','Завершено')])
    stop=forms.ChoiceField(label='Точка маршруту',required=False)

    def __init__(self,*args,trip,**kwargs):
        super().__init__(*args,**kwargs);self.trip=trip
        self.points=list(trip.stops.exclude(state='cancelled')) if trip.route_enabled else []
        self.initial['revision']=trip.updated_at.isoformat()
        if self.points:
            self.fields['stage'].choices=list(self.fields['stage'].choices)+[('point_done','Точку виконано — далі')]
            self.fields['stop'].choices=[('','Автоматично для простого маршруту')]+[(str(p.pk),f'{"" if trip.is_simple_route else str(p.position)+". "}{p.get_kind_display()} · {p.driver_place["name"]}') for p in self.points]
            self.fields['stop'].help_text='Для кількох точок оберіть, де має бути водій. Для «Призначено», «Прийнято» й «Завершено» вибір точки не потрібен.'
        else: del self.fields['stop']
        for name,field in self.fields.items(): field.widget.attrs['id']=f'dispatcher-stage-{name}'

    def clean(self):
        data=super().clean();stage=data.get('stage')
        if data.get('revision') and data['revision']!=self.trip.updated_at: self.add_error(None,'Рейс уже змінився. Оновіть сторінку перед зміною етапу.')
        if self.trip.status=='cancelled': self.add_error(None,'Скасований рейс не можна змінити цією дією.')
        if not self.trip.route_enabled or stage in ['planned','accepted','completed'] or not stage: return data
        if not self.points:
            self.add_error(None,'У маршруті немає активних точок.');return data
        point=next((p for p in self.points if str(p.pk)==data.get('stop')),None)
        if not point and len(self.points)>2:
            self.add_error('stop','Оберіть точку для цього етапу.');return data
        if not point:
            kind='load' if stage=='loading' else 'unload'
            point=next((p for p in self.points if p.kind==kind),self.points[0])
            if stage=='point_done': point=self.trip.current_stop or self.points[-1]
        expected={'loading':'load','unloading':'unload'}.get(stage)
        if expected and point.kind!=expected: self.add_error('stop','Оберіть точку відповідної операції: завантаження або розвантаження.')
        data['point']=point
        return data


def apply_stage(trip,form,actor):
    now=timezone.now();stage=form.cleaned_data['stage'];point=form.cleaned_data.get('point')
    old_label=trip.operational_status_display
    if stage!='completed' and Trip.objects.filter(status__in=Trip.ACTIVE).exclude(pk=trip.pk).filter(
            Q(vehicle=trip.vehicle)|Q(driver=trip.driver)).exists():
        raise forms.ValidationError('Водій або авто вже виконує інший рейс. Спочатку завершіть або передайте його.')
    points=form.points
    if points:
        cursor=points.index(point) if point else 0
        if stage=='point_done': cursor+=1
        for index,p in enumerate(points):
            desired='completed' if stage=='completed' or stage not in ['planned','accepted'] and index<cursor else 'arrived' if stage in ['loading','unloading'] and index==cursor else 'pending'
            if p.state==desired: continue
            arrived=timezone.localtime(p.arrived_at).strftime('%d.%m.%Y %H:%M') if p.arrived_at else '—'
            completed=timezone.localtime(p.completed_at).strftime('%d.%m.%Y %H:%M') if p.completed_at else '—'
            before=f'{p.get_state_display()} (прибув: {arrived}, виконав: {completed})'
            p.state=desired
            p.arrived_at=now if desired=='arrived' else p.arrived_at if desired=='completed' else None
            p.completed_at=now if desired=='completed' else None
            p.completed_by=actor if desired=='completed' else None
            p.save(update_fields=['state','arrived_at','completed_at','completed_by'])
            audit(actor,trip,'Логіст змінив стан точки',f'Точка {p.position}, {p.address}: {before} → {p.get_state_display()}.')
        if stage=='point_done': stage='completed' if cursor==len(points) else 'road'
    if stage=='planned': trip.accepted_at=None;trip.accepted_by=None
    elif stage=='accepted' or not trip.is_accepted:
        trip.accepted_at=now;trip.accepted_by=actor
    trip.status='planned' if stage=='accepted' else stage
    trip.completed_at=now if stage=='completed' else None
    trip.queue_priority_at=None;trip.status_changed_at=now
    trip.save(update_fields=['status','accepted_at','accepted_by','completed_at','queue_priority_at','status_changed_at','updated_at'])
    if stage=='completed':
        from .driver_reports import snapshot_completion_rates
        snapshot_completion_rates(trip)
    if trip.is_accepted or stage=='completed':
        from .driver_notifications import resolve_assignment
        resolve_assignment(trip)
    if points: sync_trip(trip)
    summary=f'{old_label} → {trip.operational_status_display}'+(f' · Точка {point.position}: {point.address}' if point else '')
    audit(actor,trip,'Логіст змінив етап рейсу',summary)
    if trip.is_active: notify_route(trip,'Логіст змінив етап рейсу: '+summary)


@require_POST
def change_stage(request,pk):
    try:
        with transaction.atomic():
            initial=get_object_or_404(Trip,pk=pk)
            Vehicle.objects.select_for_update().get(pk=initial.vehicle_id)
            trip=get_object_or_404(Trip.objects.select_for_update(),pk=pk)
            form=DispatcherStageForm(request.POST,trip=trip)
            if not form.is_valid():
                messages.error(request,' '.join(str(e) for errors in form.errors.values() for e in errors))
            else:
                apply_stage(trip,form,request.user)
                messages.success(request,'Етап змінено логістом. Водій побачить оновлення, зміна збережена в історії.')
    except (forms.ValidationError,IntegrityError) as exc:
        messages.error(request,exc.messages[0] if isinstance(exc,forms.ValidationError) else 'Водій або авто вже отримали інший рейс. Оновіть сторінку.')
    return redirect('trip_detail',pk=pk)
