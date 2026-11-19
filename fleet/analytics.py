"""Mileage/fuel analytics from the same completed-trip filters as reports."""
import csv
from decimal import Decimal
from django.db.models import Sum,Count,Q
from django.db.models.functions import TruncDate,Coalesce
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from .forms import ReportFilterForm
from .models import Trip


def filtered_trips(form):
    from .routes import signed_expression
    if not form.is_valid(): return Trip.objects.none()
    f=form.cleaned_data
    qs=Trip.objects.filter(status='completed',completed_at__date__range=(f['start'],f['end'])).annotate(signed=signed_expression())
    for name in ['driver','vehicle']:
        if f[name]: qs=qs.filter(**{name:f[name]})
    if f['kind']: qs=qs.filter(Q(completed_vehicle_kind=f['kind'])|Q(completed_vehicle_kind__isnull=True,vehicle__kind=f['kind']))
    if f['q']:
        q=f['q'];qs=qs.filter(Q(origin__icontains=q)|Q(destination__icontains=q)|Q(cargo__icontains=q)|Q(ttn_number__icontains=q)|Q(stops__address__icontains=q)|Q(stops__cargo__icontains=q)|Q(stops__ttn_number__icontains=q)).distinct()
    if f['signed']: qs=qs.filter(signed=f['signed']=='yes')
    if f['weight']: qs=qs.filter(actual_tons__isnull=f['weight']=='no')
    return Trip.objects.filter(pk__in=qs.values('pk'))


PAIRED=Q(distance_km__gt=0,fuel_used_l__isnull=False)


def sums():
    return dict(trips=Count('pk'),km=Sum('distance_km'),fuel=Sum('fuel_used_l'),
        paired=Count('pk',filter=PAIRED),paired_km=Sum('distance_km',filter=PAIRED),paired_fuel=Sum('fuel_used_l',filter=PAIRED),
        missing_km=Count('pk',filter=Q(distance_km__isnull=True)|Q(distance_km=0)),missing_fuel=Count('pk',filter=Q(fuel_used_l__isnull=True)))


def with_rate(row):
    row['rate']=(row['paired_fuel']*100/Decimal(row['paired_km'])).quantize(Decimal('.01')) if row.get('paired_km') else None
    return row


def analytics(request):
    today=timezone.localdate();data=request.GET.copy();data.pop('original',None)
    data.setdefault('start',today.replace(day=1).isoformat());data.setdefault('end',today.isoformat())
    form=ReportFilterForm(data,user=request.user);qs=filtered_trips(form)
    total=with_rate(qs.aggregate(**sums()))
    qs=qs.annotate(report_plate=Coalesce('completed_tractor_plate','vehicle__plate'),report_trailer=Coalesce('completed_trailer_plate','vehicle__trailer_plate'),report_kind=Coalesce('completed_vehicle_kind','vehicle__kind'),report_driver=Coalesce('completed_driver_name','driver__full_name'))
    vehicles=[with_rate(row) for row in qs.order_by().values('vehicle_id','report_plate','report_trailer','report_kind').annotate(**sums()).order_by('-km','report_plate')]
    drivers=[with_rate(row) for row in qs.order_by().values('driver_id','report_driver').annotate(**sums()).order_by('-km','report_driver')]
    daily=[with_rate(row) for row in qs.order_by().annotate(day=TruncDate('completed_at')).values('day').annotate(**sums()).order_by('day')]
    peak=max((row['km'] or 0 for row in daily),default=0)
    for row in daily: row['bar']=max(1,round((row['km'] or 0)*100/peak)) if peak else 0
    if data.get('format')=='csv' and form.is_valid():
        response=HttpResponse(content_type='text/csv; charset=utf-8');response['Content-Disposition']='attachment; filename="makosh-fuel-analytics.csv"';response.write('\ufeff')
        writer=csv.writer(response,delimiter=';');writer.writerow(['Авто','Причіп','Рейсів','Км','Пальне, л','Середня витрата, л/100 км','Рейсів із повними даними','Без пробігу','Без пального'])
        for row in vehicles:
            values=[row['report_plate'],row['report_trailer'],row['trips'],row['km'],row['fuel'],row['rate'],row['paired'],row['missing_km'],row['missing_fuel']]
            writer.writerow(["'"+str(v) if str(v or '').lstrip().startswith(('=','+','-','@','\t','\r')) else str(v).replace('.',',') if isinstance(v,Decimal) else v for v in values])
        return response
    query=data.copy();query.pop('format',None)
    return render(request,'fleet/analytics.html',dict(form=form,total=total,vehicles=vehicles,drivers=drivers,daily=daily,query=query.urlencode(),error=not form.is_valid()))
