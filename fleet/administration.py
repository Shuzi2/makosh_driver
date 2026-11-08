from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET

from .forms import SavedPlaceForm
from .models import AuditEvent, SavedPlace, User


def admin_required(view):
    @wraps(view)
    @login_required
    def inner(request, *args, **kwargs):
        if not request.user.manages_users:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return inner


@admin_required
@require_GET
def overview(request):
    return render(request, "fleet/administration.html", {
        "user_count": User.objects.count(),
        "place_count": SavedPlace.objects.filter(is_active=True).count(),
    })


@admin_required
@require_GET
def places(request):
    return render(request, "fleet/saved_places.html", {"places": SavedPlace.objects.all()})


@admin_required
def place_edit(request, pk=None):
    if request.method not in ("GET", "POST"):
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(["GET", "POST"])
    place = get_object_or_404(SavedPlace, pk=pk) if pk is not None else None
    form = SavedPlaceForm(request.POST if request.method == "POST" else None, instance=place)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            saved = form.save()
            AuditEvent.objects.create(
                actor=request.user,
                action="Оновлено збережене місце" if place else "Додано збережене місце",
                detail=saved.name,
            )
        messages.success(request, "Місце збережено.")
        return redirect("saved_places")
    return render(request, "fleet/place_form.html", {"form": form, "place": place})


@admin_required
@require_GET
def activity(request):
    return render(request, "fleet/admin_activity.html", {
        "events": AuditEvent.objects.select_related("actor", "trip").order_by("-created_at", "-id")[:100],
    })
