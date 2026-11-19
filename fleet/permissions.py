"""Role restrictions are checked at the route boundary, including direct requests."""
from django.core.exceptions import PermissionDenied
from .permissions_models import DriverPermissionSettings, ModeratorPermissionSettings

# Unknown routes are denied to moderators until explicitly reviewed.
MODERATOR_ROUTES = {
    'dashboard': ('view_fleet', 'edit_fleet'), 'vehicle_history': ('view_fleet', 'edit_fleet'),
    'vehicle_create': ('view_fleet', 'edit_fleet'), 'vehicle_edit': ('view_fleet', 'edit_fleet'),
    'vehicle_repair': ('view_fleet', 'edit_fleet'), 'vehicle_odometer': ('view_fleet', 'edit_fleet'),
    'repair_document_download': ('view_fleet', 'edit_fleet'), 'repair_costs_edit': ('view_fleet', 'edit_fleet'), 'repair_report': ('view_fleet', 'edit_fleet'),
    'maintenance': ('view_fleet', 'edit_fleet'), 'maintenance_create': ('view_fleet', 'edit_fleet'),
    'maintenance_edit': ('view_fleet', 'edit_fleet'), 'maintenance_complete': ('view_fleet', 'edit_fleet'),
    'manager_service_update': ('view_fleet', 'edit_fleet'),
    'manager_messages': ('view_fleet', 'edit_fleet'),
    'trip_list': ('view_trips', 'edit_trips'), 'trip_detail': ('view_trips', 'edit_trips'),
    'trip_create': ('view_trips', 'edit_trips'), 'trip_edit': ('view_trips', 'edit_trips'),
    'trip_quick_edit': ('view_trips', 'edit_trips'), 'trip_facts': ('view_trips', 'edit_trips'),
    'driver_substitution': ('view_trips', 'edit_trips'), 'dispatcher_stage': ('view_trips', 'edit_trips'),
    'trip_start': ('view_trips', 'edit_trips'), 'trip_status': ('view_trips', 'edit_trips'),
    'trip_accept': ('view_trips', 'edit_trips'), 'trip_ack_cancel': ('view_trips', 'edit_trips'),
    'trip_assign': ('view_trips', 'edit_trips'), 'assign_driver': ('view_trips', 'edit_trips'),
    'stop_action': ('view_trips', 'edit_trips'), 'acknowledge_route': ('view_trips', 'edit_trips'),
    'attention': ('view_trips', 'edit_trips'), 'attention_count': ('view_trips', 'edit_trips'),
    'attention_action': ('view_trips', 'edit_trips'),
    'map_search': ('view_trips', 'edit_trips'), 'settlement_search': ('view_trips', 'edit_trips'),
    'saved_places': ('view_trips', 'edit_trips'), 'saved_place_search': ('view_trips', 'edit_trips'),
    'saved_place_create': ('view_trips', 'edit_trips'), 'saved_place_edit': ('view_trips', 'edit_trips'),
    'saved_place_toggle': ('view_trips', 'edit_trips'),
    'document_download': ('view_documents', 'edit_documents'), 'document_preview': ('view_documents', 'edit_documents'),
    'document_inline': ('view_documents', 'edit_documents'), 'document_upload': ('view_documents', 'edit_documents'),
    'ttn_notifications': ('view_documents', 'view_documents'), 'ttn_notification_read': ('view_documents', 'view_documents'),
    'expense_receipt': ('view_documents', 'view_documents'),
    'original_received': ('view_documents', 'edit_documents'),
    'report': ('view_reports', 'edit_trips'), 'analytics': ('view_reports', 'edit_trips'),
    'driver_reports': ('view_reports','edit_trips'), 'driver_report_export': ('view_reports','edit_trips'), 'driver_report_archive_export': ('view_reports','edit_trips'), 'driver_report_defaults': ('view_reports','edit_trips'), 'driver_report_detail': ('view_reports','edit_trips'), 'driver_report_unmatched': ('view_reports','edit_trips'), 'driver_economics_edit': ('view_reports','edit_trips'), 'driver_economics_preview': ('view_reports','edit_trips'),
    'driver_archive_prices': ('view_reports','edit_trips'),
    'waybill_list': ('view_reports', 'view_reports'), 'waybill_detail': ('view_reports', 'view_reports'), 'waybill_edit': ('view_reports', 'edit_trips'),
    'report_edit': ('view_reports', 'edit_trips'),
}

DRIVER_ROUTE_PERMISSIONS = {
    'dashboard': 'view_trips', 'trip_list': 'view_trips', 'trip_detail': 'view_trips',
    'driver_updates': 'view_trips', 'driver_finish': 'view_trips', 'driver_settlement_search': 'change_stages',
    'driver_delay': 'report_delays', 'driver_expense': 'manage_expenses', 'expense_receipt': 'manage_expenses',
    'driver_documents': 'upload_documents', 'document_download': 'upload_documents',
    'driver_service': 'view_trips', 'driver_service_create': 'view_trips', 'driver_service_edit': 'view_trips',
    'document_preview': 'upload_documents', 'document_inline': 'upload_documents',
    'document_upload': 'upload_documents',
    'driver_remove_photo': 'upload_documents',
    'driver_action': 'change_stages', 'driver_rest': 'change_stages',
    'trip_accept': 'change_stages', 'trip_start': 'change_stages',
    'trip_status': 'change_stages', 'trip_ack_cancel': 'change_stages',
    'acknowledge_route': 'change_stages', 'stop_action': 'change_stages',
}


class DriverPermissionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not request.user.is_authenticated:
            return None
        if request.user.role == 'moderator':
            name = request.resolver_match.url_name
            if name in ('logout', 'password_change'):
                return None
            pair = MODERATOR_ROUTES.get(name)
            if not pair:
                raise PermissionDenied
            settings, _ = ModeratorPermissionSettings.objects.get_or_create(pk=1)
            read, write = pair
            # These screens include embedded trips or document data.
            prerequisites = {
                'dashboard': ('view_fleet', 'view_trips', 'view_documents'),
                'trip_detail': ('view_trips', 'view_documents'),
                'trip_list': ('view_trips', 'view_documents'),
                'ttn_notifications': ('view_trips', 'view_documents'),
                'ttn_notification_read': ('view_trips', 'view_documents'),
                'expense_receipt': ('view_trips', 'view_documents'),
            }
            if any(not getattr(settings, field) for field in prerequisites.get(name, ())):
                raise PermissionDenied
            if not getattr(settings, read) or (request.method not in ('GET', 'HEAD', 'OPTIONS') and not getattr(settings, write)):
                raise PermissionDenied
            return None
        if request.user.role != 'driver':
            return None
        permission = DRIVER_ROUTE_PERMISSIONS.get(request.resolver_match.url_name)
        if permission:
            settings, _ = DriverPermissionSettings.objects.get_or_create(pk=1)
            # Document and action endpoints also expose trip data. View access
            # is therefore a prerequisite for every trip-related capability.
            if not settings.view_trips or not getattr(settings, permission):
                raise PermissionDenied
        return None
