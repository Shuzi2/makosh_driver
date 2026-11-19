"""Filtered completed-trip reports and audited corrections."""
import csv
from datetime import datetime
from urllib.parse import urlsplit
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Sum, Q, Exists, OuterRef
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.utils import timezone
from .forms import ReportFilterForm, ReportTripForm
from .models import Trip, Document


@login_required
def report(request):
    from .views import trips_for
    today=timezone.localdate()
    data=request.GET.copy()
    data.pop('original',None)
    if 'start' not in data: data['start']=today.replace(day=1).isoformat()
    if 'end' not in data: data['end']=today.isoformat()
    form=ReportFilterForm(data,user=request.user)
    from .routes import signed_expression, fleet_ttn_summary
    qs=trips_for(request.user).filter(status='completed').select_related('vehicle','driver').annotate(signed=signed_expression())
    valid=form.is_valid()
    if valid:
        filters=form.cleaned_data
        qs=qs.filter(completed_at__date__range=(filters['start'],filters['end']))
        for field in ['driver','vehicle']:
            if filters[field]: qs=qs.filter(**{field:filters[field]})
        if filters['kind']: qs=qs.filter(Q(completed_vehicle_kind=filters['kind'])|Q(completed_vehicle_kind__isnull=True,vehicle__kind=filters['kind']))
        if filters['q']:
            q=filters['q']
            qs=qs.filter(Q(origin__icontains=q)|Q(destination__icontains=q)|Q(cargo__icontains=q)|Q(ttn_number__icontains=q)|Q(stops__address__icontains=q)|Q(stops__cargo__icontains=q)|Q(stops__ttn_number__icontains=q)).distinct()
        if filters['signed']: qs=qs.filter(signed=filters['signed']=='yes')
        if filters['weight']: qs=qs.filter(actual_tons__isnull=filters['weight']=='no')
    else: qs=qs.none()
    qs=qs.order_by('-completed_at','-pk')
    totals=qs.aggregate(tons=Sum('actual_tons'),distance=Sum('distance_km'),fuel=Sum('fuel_used_l'),missing_distance=Count('pk',filter=Q(distance_km__isnull=True)),missing_fuel=Count('pk',filter=Q(fuel_used_l__isnull=True)),missing_weight=Count('pk',filter=Q(actual_tons__isnull=True)),missing_signed=Count('pk',filter=Q(signed=False)))
    if request.GET.get('format')=='csv' and valid:
        response=HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition']='attachment; filename="makosh-report.csv"'
        response.write('\ufeff')
        writer=csv.writer(response,delimiter=';')
        if request.GET.get('detail')=='stops':
            writer.writerow(['Рейс','Точка','Операція','Стан','Адреса','Вантаж','Планова вага, т','Фактична вага, т','Номери ТТН','Виконано'])
            def safe_point(value):
                value=str(value if value is not None else '')
                return "'"+value if value.lstrip().startswith(('=','+','-','@','\t','\r')) else value
            for trip in qs:
                for stop in trip.stops.all():
                    writer.writerow([safe_point(v) for v in [trip.pk,stop.position,stop.get_kind_display(),stop.get_state_display(),stop.address,stop.cargo,stop.planned_tons,stop.actual_tons,stop.ttn_number,timezone.localtime(stop.completed_at).strftime('%d.%m.%Y %H:%M') if stop.completed_at else '']])
            return response
        writer.writerow(['Рейс','Дата завершення','Тягач','Причіп','Тип авто','Водій','Звідки','Куди','Вантаж','Планова вага, т','Фактична вага, т','Номер ТТН','ТТН завантаження','Підписана ТТН розвантаження','Пробіг рейсу, км','Витрачено пального, л'])
        def safe(value):
            text=str(value if value is not None else '')
            return "'"+text if text.lstrip().startswith(('=','+','-','@','\t','\r')) else text
        def tons(value): return str(value).replace('.',',') if value is not None else ''
        for trip in qs.prefetch_related('stops','documents').iterator(chunk_size=100):
            ttn_status=fleet_ttn_summary(trip)
            writer.writerow([safe(value) for value in [trip.pk,timezone.localtime(trip.completed_at).strftime('%d.%m.%Y %H:%M'),trip.report_tractor_plate,trip.report_trailer_plate,trip.report_vehicle_kind,trip.report_driver_name,trip.origin,trip.destination,trip.cargo,tons(trip.planned_tons),tons(trip.actual_tons),trip.ttn_number,ttn_status[0]['text'],ttn_status[1]['text'],trip.distance_km,tons(trip.fuel_used_l)]])
        return response
    query=data.copy()
    for key in ['page','format']: query.pop(key,None)
    page=Paginator(qs.prefetch_related('stops','documents'),50).get_page(request.GET.get('page'))
    for trip in page.object_list:
        trip.report_documents=fleet_ttn_summary(trip)
    return_url=reverse('report')+'?'+query.urlencode()
    return render(request,'fleet/report.html',{'form':form,'trips':page.object_list,'page':page,'count':page.paginator.count,'totals':totals,'filter_query':query.urlencode(),'return_url':return_url+'&page='+str(page.number),'error':not valid})


def safe_return(value):
    try:
        parts=urlsplit(value)
        if not parts.scheme and not parts.netloc and parts.path==reverse('report') and not any(ord(c)<32 for c in value): return value
    except ValueError: pass
    return reverse('report')


@login_required
def report_edit(request,pk):
    from .views import event
    if not request.user.manages_fleet: raise PermissionDenied
    return_url=safe_return(request.POST.get('return_url',request.GET.get('return_url','')))
    if request.method=='POST':
        with transaction.atomic():
            trip=get_object_or_404(Trip.objects.select_for_update(),pk=pk,status='completed')
            before={field:getattr(trip,field) for field in ReportTripForm.Meta.fields}
            form=ReportTripForm(request.POST,instance=trip)
            if form.is_valid():
                changed=[field for field in ReportTripForm.Meta.fields if before[field]!=form.cleaned_data[field]]
                if changed:
                    edited=form.save(commit=False)
                    cleared=[]
                    for stop in ['origin','destination']:
                        if stop in changed and (getattr(edited,stop+'_lat') is not None or getattr(edited,stop+'_lon') is not None):
                            setattr(edited,stop+'_lat',None);setattr(edited,stop+'_lon',None)
                            cleared.extend([stop+'_lat',stop+'_lon'])
                    edited.save(update_fields=list(dict.fromkeys(changed+cleared+['updated_at'])))
                    def display(value):
                        if value is None or value=='': return 'не вказано'
                        if isinstance(value,bool): return 'так' if value else 'ні'
                        if isinstance(value,datetime): return timezone.localtime(value).strftime('%d.%m.%Y %H:%M:%S')
                        return str(value)
                    for field in changed:
                        event(request.user,'Уточнено дані звіту',trip,f'{form.fields[field].label}: {display(before[field])} → {display(form.cleaned_data[field])}')
                    if cleared: event(request.user,'Знято точку на карті',trip,'Адресу змінено у звіті. Для нової адреси потрібно повторно вибрати точку.')
                    messages.success(request,'Дані рейсу оновлено. Звіт перераховано за вибраними фільтрами.')
                else: messages.info(request,'Дані не змінилися.')
                return redirect(return_url)
    else:
        trip=get_object_or_404(Trip,pk=pk,status='completed')
        form=ReportTripForm(instance=trip)
    return render(request,'fleet/report_edit.html',{'trip':trip,'form':form,'return_url':return_url})
