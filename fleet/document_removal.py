from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import AuditEvent, Document, Trip, TripStop


@login_required
@require_POST
def remove_driver_photo(request,pk):
    if request.user.role != 'driver': raise PermissionDenied
    initial=get_object_or_404(Document.objects.select_related('trip'),pk=pk,uploaded_by=request.user,trip__driver=request.user,content_type__in=['image/jpeg','image/png','image/webp','application/pdf'])
    with transaction.atomic():
        trip=get_object_or_404(Trip.objects.select_for_update(),pk=initial.trip_id,driver=request.user)
        doc=get_object_or_404(Document.objects.select_for_update(),pk=pk,trip=trip,uploaded_by=request.user,content_type__in=['image/jpeg','image/png','image/webp','application/pdf'])
        doc.deleted_at=timezone.now();doc.deleted_by=request.user;doc.save(update_fields=['deleted_at','deleted_by'])
        if doc.stop_id:
            stop=TripStop.objects.select_for_update().get(pk=doc.stop_id,trip=trip)
            stop.documents_revision+=1;stop.save(update_fields=['documents_revision'])
            from .routes import refresh_signed
            refresh_signed(stop)
        trip.save(update_fields=['updated_at'])
        AuditEvent.objects.create(actor=request.user,trip=trip,action='Водій видалив PDF' if doc.content_type=='application/pdf' else 'Водій видалив фото документа',detail=f'{doc.get_kind_display()}: {doc.original_name}'+(f' · Точка {doc.stop.position}, {doc.stop.address}' if doc.stop_id else ''))
    messages.success(request,'Документ видалено зі списку.')
    target=reverse('driver_documents')+f'?trip={trip.pk}'
    if doc.stop_id: target+=f'&stop={doc.stop_id}'
    return redirect(target)
