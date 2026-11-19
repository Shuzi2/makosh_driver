from django.db import models


class DriverPermissionSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    view_trips = models.BooleanField(default=True)
    change_stages = models.BooleanField(default=True)
    upload_documents = models.BooleanField(default=True)
    report_delays = models.BooleanField(default=True)
    manage_expenses = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='driver_permissions_singleton')]

class ModeratorPermissionSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    view_fleet = models.BooleanField(default=True)
    edit_fleet = models.BooleanField(default=True)
    view_trips = models.BooleanField(default=True)
    edit_trips = models.BooleanField(default=True)
    view_documents = models.BooleanField(default=True)
    edit_documents = models.BooleanField(default=True)
    view_reports = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
