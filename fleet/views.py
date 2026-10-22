import csv, secrets
from datetime import date
from functools import wraps
from pathlib import Path
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction, IntegrityError, connection
from django.db.models import Sum, Q
from django.db.models.functions import Coalesce
from django.http import FileResponse, HttpResponse, JsonResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_GET
from .models import User, Vehicle, Trip, Document, AuditEvent
from .forms import TripForm, DocumentForm, AssignmentForm, AccountForm

def staff_required(view):
    @wraps(view)
    @login_required
    def inner(request,*args,**kwargs):
        if not request.user.manages_fleet: raise PermissionDenied
        return view(request,*args,**kwargs)
    return inner

def trips_for(user):
    qs=Trip.objects.select_related("vehicle","driver").prefetch_related("documents")
    if user.role=="driver" and not user.is_superuser: return qs.filter(driver=user)
    return qs

def can_write(user,trip):
    return user.manages_fleet or (user.role=="driver" and trip.driver_id==user.id)

def event(user,action,trip=None,detail=""):
    AuditEvent.objects.create(actor=user,trip=trip,action=action,detail=detail)

@login_required
def dashboard(request):
    qs=trips_for(request.user)
    today=timezone.localdate()
    active=qs.filter(status__in=Trip.ACTIVE)
    done=qs.filter(status="completed",completed_at__date=today)
    missing=qs.filter(status="completed").exclude(documents__kind="signed_ttn").distinct()
    cards=[]
    if request.user.role!="driver" or request.user.is_superuser:
        by_vehicle={t.vehicle_id:t for t in active}
        for vehicle in Vehicle.objects.select_related("default_driver"):
            cards.append({"vehicle":vehicle,"trip":by_vehicle.get(vehicle.id)})
    return render(request,"fleet/dashboard.html",{"cards":cards,"trips":active,"done_count":done.count(),"tons":done.aggregate(total=Sum(Coalesce("actual_tons","planned_tons")))["total"] or 0,"active_count":active.count(),"missing_count":missing.count(),"missing":missing[:5],"today":today,"vehicle_count":Vehicle.objects.count()})

@login_required
def trip_list(request):
    qs=trips_for(request.user)
    query=request.GET.get("q","")[:100]
    if query: qs=qs.filter(Q(vehicle__plate__icontains=query)|Q(origin__icontains=query)|Q(destination__icontains=query)|Q(driver__full_name__icontains=query)|Q(ttn_number__icontains=query))
    status=request.GET.get("status","")
    if status=="missing": qs=qs.filter(status="completed").exclude(documents__kind="signed_ttn").distinct()
    elif status in Trip.Status.values: qs=qs.filter(status=status)
    # Bounded first release: most recent 200; reports support full selected period.
    return render(request,"fleet/trip_list.html",{"trips":qs[:200],"statuses":Trip.Status.choices,"q":query,"selected":status})

@staff_required
def trip_create(request):
    form=TripForm(request.POST or None,initial={"planned_date":timezone.localdate()})
    if request.method=="POST" and form.is_valid():
        try:
            with transaction.atomic():
                vehicle=Vehicle.objects.select_for_update().get(pk=form.cleaned_data["vehicle"].pk)
                driver=User.objects.select_for_update().get(pk=form.cleaned_data["driver"].pk)
                if vehicle.in_repair or not driver.is_active or driver.role!="driver":
                    raise ValueError("Авто або водій зараз недоступні.")
                trip=form.save(commit=False); trip.created_by=request.user; trip.save()
                event(request.user,"Створено рейс",trip)
            return redirect("trip_detail",pk=trip.pk)
        except (IntegrityError,ValueError) as exc:
            form.add_error(None,str(exc) if isinstance(exc,ValueError) else "Авто або водій уже мають активний рейс. Оновіть сторінку.")
    return render(request,"fleet/form.html",{"form":form,"title":"Новий рейс","submit":"Створити рейс"})

@login_required
def trip_detail(request,pk):
    trip=get_object_or_404(trips_for(request.user),pk=pk)
    chain=list(Trip.Status.values[:5])
    next_status=chain[chain.index(trip.status)+1] if trip.status in chain[:-1] else None
    return render(request,"fleet/trip_detail.html",{"trip":trip,"upload_form":DocumentForm(),"assignment_form":AssignmentForm(initial={"driver":trip.driver}),"can_write":can_write(request.user,trip),"next_status":next_status,"next_label":dict(Trip.Status.choices).get(next_status),"events":trip.events.select_related("actor")[:30]})

@login_required
@require_POST
def trip_status(request,pk):
    with transaction.atomic():
        trip=get_object_or_404(trips_for(request.user).select_for_update(),pk=pk)
        if not can_write(request.user,trip): raise PermissionDenied
        target=request.POST.get("status")
        chain=list(Trip.Status.values[:5])
        allowed=[]
        if trip.status in chain[:-1]: allowed=[chain[chain.index(trip.status)+1]]
        if request.user.manages_fleet and trip.is_active: allowed=chain[1:]+["cancelled"]
        if target not in allowed:
            messages.error(request,"Цей перехід статусу недоступний."); return redirect("trip_detail",pk=pk)
        tons=request.POST.get("actual_tons","").strip()
        if tons:
            try:
                value=Decimal(tons.replace(",","."))
                if not value.is_finite() or not Decimal("0.01")<=value<=60 or value.as_tuple().exponent < -2: raise ValueError()
                trip.actual_tons=value
            except Exception:
                messages.error(request,"Вага має бути від 0,01 до 60 т, до двох знаків після коми."); return redirect("trip_detail",pk=pk)
        previous=trip.status
        trip.status=target
        if target=="completed": trip.completed_at=timezone.now()
        trip.save()
        event(request.user,"Змінено статус",trip,f"{dict(Trip.Status.choices)[previous]} → {trip.get_status_display()}")
    messages.success(request,"Статус оновлено. Документи можна додати після завершення рейсу.")
    return redirect("trip_detail",pk=pk)

@staff_required
@require_POST
def assign_driver(request,pk):
    form=AssignmentForm(request.POST)
    if form.is_valid():
        try:
            with transaction.atomic():
                trip=get_object_or_404(Trip.objects.select_for_update(),pk=pk)
                new=User.objects.select_for_update().get(pk=form.cleaned_data["driver"].pk)
                if not trip.is_active:
                    raise ValueError("Водія можна змінити лише в активному рейсі.")
                if not new.is_active or new.role!="driver": raise ValueError("Водій недоступний.")
                old=str(trip.driver);trip.driver=new;trip.save()
                event(request.user,"Змінено водія",trip,f"{old} → {new}")
            messages.success(request,"Рейс передано новому водію.")
        except (IntegrityError,ValueError) as exc:
            messages.error(request,str(exc) if isinstance(exc,ValueError) else "Цей водій уже має активний рейс.")
    else: messages.error(request,"Оберіть доступного водія.")
    return redirect("trip_detail",pk=pk)

@login_required
@require_POST
def document_upload(request,pk):
    trip=get_object_or_404(trips_for(request.user),pk=pk)
    if not can_write(request.user,trip): raise PermissionDenied
    if trip.status=="cancelled": raise PermissionDenied
    form=DocumentForm(request.POST,request.FILES)
    if form.is_valid():
        # Recheck assignment while holding the trip lock: a substitute may have taken over.
        with transaction.atomic():
            trip=get_object_or_404(trips_for(request.user).select_for_update(),pk=pk)
            if not can_write(request.user,trip): raise PermissionDenied
            doc=form.save(commit=False);doc.trip=trip;doc.uploaded_by=request.user
            doc.original_name=Path(form.cleaned_data["file"].name).name[:250]
            doc.content_type=form.detected_type;doc.size=form.cleaned_data["file"].size
            try:
                doc.save();event(request.user,"Додано документ",trip,doc.get_kind_display())
            except Exception:
                if doc.file and doc.file.name: doc.file.delete(save=False)
                raise
        messages.success(request,"Документ збережено.")
    else:
        for errors in form.errors.values():
            for error in errors: messages.error(request,error)
    return redirect("trip_detail",pk=pk)

@login_required
@require_GET
def document_download(request,pk):
    doc=get_object_or_404(Document.objects.select_related("trip"),pk=pk,trip__in=trips_for(request.user))
    try: handle=doc.file.open("rb")
    except FileNotFoundError: raise Http404("Файл не знайдено. Зверніться до адміністратора.")
    response=FileResponse(handle,as_attachment=True,filename=doc.original_name,content_type=doc.content_type)
    response["Cache-Control"]="private, no-store"
    response["X-Content-Type-Options"]="nosniff"
    return response

@staff_required
@require_POST
def original_received(request,pk):
    trip=get_object_or_404(Trip,pk=pk)
    trip.original_received=not trip.original_received;trip.save(update_fields=["original_received","updated_at"])
    event(request.user,"Облік оригіналу ТТН",trip,"Отримано" if trip.original_received else "Не отримано")
    return redirect("trip_detail",pk=pk)

@login_required
def report(request):
    today=timezone.localdate()
    error=""
    try:
        start=date.fromisoformat(request.GET.get("start",today.replace(day=1).isoformat()))
        end=date.fromisoformat(request.GET.get("end",today.isoformat()))
        if start>end: raise ValueError()
    except ValueError:
        start=end=today;error="Перевірте період: дата початку не може бути пізніше дати завершення."
    qs=trips_for(request.user).filter(status="completed",completed_at__date__range=(start,end))
    totals=qs.aggregate(tons=Sum(Coalesce("actual_tons","planned_tons")),km=Sum("distance_km"))
    if request.GET.get("format")=="csv" and not error:
        response=HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"]='attachment; filename="makosh-report.csv"'
        response.write("\ufeff")
        writer=csv.writer(response,delimiter=";")
        writer.writerow(["Рейс","Дата завершення","Тягач","Причіп","Водій","Звідки","Куди","Вантаж","Вага, т","Пробіг, км","Підписана ТТН","Оригінал"])
        def safe(value):
            text=str(value if value is not None else "")
            return "'"+text if text.lstrip().startswith(("=","+","-","@","\t","\r")) else text
        for t in qs.iterator(chunk_size=100):
            writer.writerow([safe(x) for x in [t.pk,timezone.localtime(t.completed_at).strftime("%d.%m.%Y"),t.vehicle.plate,t.vehicle.trailer_plate,t.driver.full_name,t.origin,t.destination,t.cargo,str(t.actual_tons or t.planned_tons).replace(".",","),t.distance_km,"Так" if t.has_signed_ttn else "Ні","Так" if t.original_received else "Ні"]])
        return response
    return render(request,"fleet/report.html",{"trips":qs[:500],"count":qs.count(),"totals":totals,"start":start.isoformat(),"end":end.isoformat(),"error":error})

@login_required
def accounts(request):
    if not request.user.manages_users: raise PermissionDenied
    form=AccountForm(request.POST or None)
    temporary=None
    if request.method=="POST" and form.is_valid():
        user=form.save(commit=False);temporary=secrets.token_urlsafe(15);user.set_password(temporary);user.must_change_password=True;user.save()
        event(request.user,"Створено обліковий запис",detail=user.full_name)
        form=AccountForm()
    response=render(request,"fleet/accounts.html",{"users":User.objects.order_by("role","full_name"),"form":form,"temporary":temporary})
    response["Cache-Control"]="no-store"
    return response

@login_required
@require_POST
def account_action(request,pk):
    if not request.user.manages_users: raise PermissionDenied
    user=get_object_or_404(User,pk=pk)
    if user.pk==request.user.pk or user.is_superuser:
        raise PermissionDenied
    action=request.POST.get("action")
    temporary=None
    with transaction.atomic():
        user=User.objects.select_for_update().get(pk=pk)
        if action=="reset":
            temporary=secrets.token_urlsafe(15);user.set_password(temporary);user.must_change_password=True;user.save()
            # Existing sessions are invalidated by Django's password hash check.
            event(request.user,"Скинуто пароль",detail=user.full_name)
        elif action=="toggle":
            if user.is_active and user.trips.filter(status__in=Trip.ACTIVE).exists():
                messages.error(request,"Спочатку передайте активний рейс іншому водію.")
            else:
                user.is_active=not user.is_active;user.save(update_fields=["is_active"])
                event(request.user,"Змінено активність користувача",detail=user.full_name)
        else: raise PermissionDenied
    if temporary:
        response=render(request,"fleet/temporary_password.html",{"target":user,"temporary":temporary});response["Cache-Control"]="no-store";return response
    return redirect("accounts")

@staff_required
@require_POST
def vehicle_repair(request,pk):
    with transaction.atomic():
        vehicle=get_object_or_404(Vehicle.objects.select_for_update(),pk=pk)
        if not vehicle.in_repair and vehicle.trips.filter(status__in=Trip.ACTIVE).exists():
            messages.error(request,"Спочатку завершіть або скасуйте активний рейс.")
        else:
            vehicle.in_repair=not vehicle.in_repair;vehicle.save(update_fields=["in_repair"])
            event(request.user,"Стан авто",detail=f"{vehicle.plate}: {'ремонт' if vehicle.in_repair else 'готове'}")
    return redirect("dashboard")

@require_GET
def health(request):
    try:
        with connection.cursor() as cursor: cursor.execute("SELECT 1"); cursor.fetchone()
    except Exception: return JsonResponse({"status":"unavailable"},status=503)
    return JsonResponse({"status":"ok"})
