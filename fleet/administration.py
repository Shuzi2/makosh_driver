"""Authenticated, audited administration of the local application."""
from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from . import operations
from .admin_forms import JournalFilterForm, OperationsSettingsForm, RoleForm, MailSettingsForm
from .models import AuditEvent, DriverPermissionSettings, ModeratorPermissionSettings, MailSettings, OperationJob, OperationsMetricSample, OperationsSettings, Trip, User
from .views import admin_required as staff_required
from django import forms


class FleetCopySettingsForm(forms.ModelForm):
    class Meta:
        from .models import FleetCopySettings
        model=FleetCopySettings
        fields=['carrier','storage_address']


class DriverCopyDocumentsForm(forms.ModelForm):
    class Meta:
        model=User
        fields=['driver_license_number','passport_number']

class CopySpecificationsForm(forms.ModelForm):
    class Meta:
        fields=['length_mm','width_mm','height_mm','empty_weight_kg','max_weight_kg']
    def clean(self):
        data=super().clean()
        if data.get('empty_weight_kg') and data.get('max_weight_kg') and data['empty_weight_kg']>data['max_weight_kg']:
            self.add_error('max_weight_kg','Максимальна маса не може бути меншою за масу без вантажу.')
        return data

class TractorCopySpecificationsForm(CopySpecificationsForm):
    class Meta(CopySpecificationsForm.Meta):
        from .models import Vehicle
        model=Vehicle

class TrailerCopySpecificationsForm(CopySpecificationsForm):
    class Meta(CopySpecificationsForm.Meta):
        from .models import Trailer
        model=Trailer


@staff_required
@require_http_methods(['GET','POST'])
def copy_data(request):
    from .models import FleetCopySettings, Vehicle, Trailer
    settings=FleetCopySettings.objects.filter(pk=1).first() or FleetCopySettings(pk=1)
    settings_form=FleetCopySettingsForm(instance=settings)
    selected=None;driver_form=None;selected_spec=None;spec_form=None;spec_action=None
    if request.method=='POST':
        if request.POST.get('action')=='settings':
            settings_form=FleetCopySettingsForm(request.POST,instance=settings)
            if settings_form.is_valid():
                settings_form.save()
                AuditEvent.objects.create(actor=request.user,action='Оновлено дані перевізника для копіювання')
                messages.success(request,'Дані перевізника збережено.')
                return redirect('admin_copy_data')
        elif request.POST.get('action')=='driver':
            driver_id=request.POST.get('driver_id','')
            if not driver_id.isascii() or not driver_id.isdigit() or len(driver_id)>18: raise Http404
            selected=get_object_or_404(User,pk=int(driver_id),role='driver')
            driver_form=DriverCopyDocumentsForm(request.POST,instance=selected)
            if driver_form.is_valid():
                driver_form.save()
                AuditEvent.objects.create(actor=request.user,action='Оновлено документи для копіювання',detail=selected.full_name)
                messages.success(request,'Документи водія збережено.')
                return redirect('admin_copy_data')
        elif request.POST.get('action') in ('tractor','trailer'):
            spec_action=request.POST['action']
            model,form_class,id_key=(Vehicle,TractorCopySpecificationsForm,'vehicle_id') if spec_action=='tractor' else (Trailer,TrailerCopySpecificationsForm,'trailer_id')
            object_id=request.POST.get(id_key,'')
            if not object_id.isascii() or not object_id.isdigit() or len(object_id)>18: raise Http404
            selected_spec=get_object_or_404(model,pk=int(object_id))
            spec_form=form_class(request.POST,instance=selected_spec,prefix=f'{spec_action}-{selected_spec.pk}')
            if spec_form.is_valid():
                spec_form.save()
                AuditEvent.objects.create(actor=request.user,action='Оновлено габарити для копіювання',detail=f'{spec_action}: {selected_spec.plate}')
                messages.success(request,'Дані авто збережено.')
                return redirect('admin_copy_data')
        else:
            raise Http404
    drivers=User.objects.filter(role='driver').order_by('full_name','pk')
    rows=[{'driver':driver,'form':driver_form if selected and selected.pk==driver.pk else DriverCopyDocumentsForm(instance=driver)} for driver in drivers]
    tractors=[{'item':item,'form':spec_form if spec_action=='tractor' and selected_spec and selected_spec.pk==item.pk else TractorCopySpecificationsForm(instance=item,prefix=f'tractor-{item.pk}')} for item in Vehicle.objects.order_by('plate')]
    trailers=[{'item':item,'form':spec_form if spec_action=='trailer' and selected_spec and selected_spec.pk==item.pk else TrailerCopySpecificationsForm(instance=item,prefix=f'trailer-{item.pk}')} for item in Trailer.objects.order_by('plate')]
    return render(request,'fleet/admin_copy_data.html',{'settings_form':settings_form,'rows':rows,'tractors':tractors,'trailers':trailers})


def _settings():
    return OperationsSettings.objects.get_or_create(pk=1)[0]


def _worker_state(settings):
    if not settings.worker_seen_at:
        return 'Немає сигналу', False
    fresh = settings.worker_seen_at >= timezone.now() - timedelta(minutes=5)
    return ('Працює' if fresh else 'Сигнал застарів'), fresh


def _preview_rows(days):
    counts = operations.cleanup_preview(days)
    labels = {'sessions': 'Прострочені сесії', 'session_activity': 'Застарілі дані активності сесій', 'login_throttle': 'Застарілі обмеження входу', 'map_cache': 'Кеш карт', 'operation_logs': 'Технічна діагностика операцій', 'metric_samples': 'Історія технічних вимірювань'}
    return {label: counts.get(key, 0) for key, label in labels.items()}


@staff_required
@require_GET
def overview(request):
    settings = _settings()
    worker_label, worker_healthy = _worker_state(settings)
    return render(request, 'fleet/admin_overview.html', {
        'settings': settings, 'worker_label': worker_label, 'worker_healthy': worker_healthy,
        'metrics': operations.resource_metrics(),
        'metric_samples': OperationsMetricSample.objects.order_by('-sampled_at')[:12],
        'last_backup': OperationJob.objects.filter(kind='backup', status='succeeded').order_by('-created_at').first(),
        'last_restore_test': OperationJob.objects.filter(kind='restore_test').order_by('-created_at').first(),
        'jobs': OperationJob.objects.select_related('requested_by').order_by('-created_at')[:5],
    })


@staff_required
@require_GET
def metrics_data(request):
    metrics = operations.resource_metrics()
    metrics['sampled_at'] = timezone.now().isoformat()
    response = JsonResponse(metrics)
    response['Cache-Control'] = 'private, no-store'
    return response

@staff_required
@require_http_methods(['GET', 'POST'])
def mail_settings(request):
    config, _ = MailSettings.objects.get_or_create(pk=1)
    form = MailSettingsForm(request.POST or None, instance=config)
    if request.method == 'POST' and form.is_valid():
        from .mail_service import cipher
        config = form.save(commit=False)
        if form.cleaned_data['password']:
            config.password_cipher = cipher().encrypt(form.cleaned_data['password'].encode()).decode()
        config.save()
        AuditEvent.objects.create(actor=request.user, action='Змінено налаштування SMTP', detail='Пароль змінено' if form.cleaned_data['password'] else '')
        messages.success(request, 'Налаштування пошти збережено.')
        return redirect('admin_mail')
    return render(request, 'fleet/admin_mail.html', {'form': form, 'config': config})

@staff_required
@require_POST
def mail_test(request):
    from django.core.validators import validate_email
    from django.core.exceptions import ValidationError
    from .mail_service import send_mail
    recipient = request.POST.get('recipient', '').strip()
    try:
        validate_email(recipient)
        send_mail(MailSettings.objects.get(pk=1), recipient, 'Перевірка пошти M TRANS', 'Поштові налаштування працюють.')
        messages.success(request, 'Тестовий лист передано поштовому серверу.')
    except (ValidationError, MailSettings.DoesNotExist):
        messages.error(request, 'Перевірте адресу та налаштування пошти.')
    except Exception:
        messages.error(request, 'Лист не надіслано. Перевірте SMTP налаштування та мережу.')
    return redirect('admin_mail')


@staff_required
@require_GET
def journal(request):
    form = JournalFilterForm(request.GET)
    events = AuditEvent.objects.select_related('actor', 'trip').order_by('-created_at', '-pk')
    if form.is_valid():
        data = form.cleaned_data
        if data['user']:
            events = events.filter(actor=data['user'])
        if data['category']:
            events = events.filter(action=data['category'])
        if data['q']:
            events = events.filter(Q(action__icontains=data['q']) | Q(detail__icontains=data['q']) | Q(actor__full_name__icontains=data['q']) | Q(actor__phone__icontains=data['q']))
        if data['date_from']:
            events = events.filter(created_at__date__gte=data['date_from'])
        if data['date_to']:
            events = events.filter(created_at__date__lte=data['date_to'])
    else:
        events = events.none()
    query = request.GET.copy()
    query.pop('page', None)
    return render(request, 'fleet/admin_journal.html', {'form': form, 'page_obj': Paginator(events, 40).get_page(request.GET.get('page')), 'filter_query': query.urlencode()})


@staff_required
@require_GET
def roles(request):
    permissions, _ = DriverPermissionSettings.objects.get_or_create(pk=1)
    moderator, _ = ModeratorPermissionSettings.objects.get_or_create(pk=1)
    permission_rows = [
        ('view_trips', 'Перегляд власних рейсів', permissions.view_trips),
        ('change_stages', 'Зміна етапів і стану рейсу', permissions.change_stages),
        ('upload_documents', 'Перегляд і завантаження документів', permissions.upload_documents),
        ('report_delays', 'Повідомлення про затримку', permissions.report_delays),
        ('manage_expenses', 'Внесення витрат і чеків', permissions.manage_expenses),
    ]
    moderator_labels = [('view_fleet','Перегляд автопарку та ТО'), ('edit_fleet','Зміни автопарку та ТО'), ('view_trips','Перегляд рейсів'), ('edit_trips','Створення та зміна рейсів'), ('view_documents','Перегляд документів'), ('edit_documents','Завантаження та зміна документів'), ('view_reports','Перегляд звітів')]
    moderator_rows = [(key, label, getattr(moderator, key)) for key, label in moderator_labels]
    return render(request, 'fleet/admin_roles.html', {'permission_rows': permission_rows, 'moderator_rows': moderator_rows})

@staff_required
@require_POST
def moderator_permissions_update(request):
    fields = ('view_fleet', 'edit_fleet', 'view_trips', 'edit_trips', 'view_documents', 'edit_documents', 'view_reports')
    with transaction.atomic():
        ModeratorPermissionSettings.objects.get_or_create(pk=1)
        settings = ModeratorPermissionSettings.objects.select_for_update().get(pk=1)
        changes = []
        for field in fields:
            value = field in request.POST
            if getattr(settings, field) != value:
                changes.append(f'{field}: {getattr(settings, field)} → {value}')
            setattr(settings, field, value)
        settings.save(update_fields=[*fields, 'updated_at'])
        if changes:
            AuditEvent.objects.create(actor=request.user, action='Змінено права модератора', detail='; '.join(changes))
    messages.success(request, 'Права модераторів збережено.')
    return redirect('admin_roles')


@staff_required
@require_POST
def permissions_update(request):
    fields = ('view_trips', 'change_stages', 'upload_documents', 'report_delays', 'manage_expenses')
    with transaction.atomic():
        DriverPermissionSettings.objects.get_or_create(pk=1)
        permissions = DriverPermissionSettings.objects.select_for_update().get(pk=1)
        actor = User.objects.filter(pk=request.user.pk, is_active=True).first()
        if not actor or not actor.manages_users:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        changes = []
        for field in fields:
            new_value = field in request.POST
            if getattr(permissions, field) != new_value:
                changes.append(f'{field}: {getattr(permissions, field)} → {new_value}')
            setattr(permissions, field, new_value)
        permissions.save(update_fields=[*fields, 'updated_at'])
        if changes:
            AuditEvent.objects.create(actor=request.user, action='Змінено права ролі водія', detail='; '.join(changes))
    messages.success(request, 'Права водіїв збережено. Адміністратор зберігає всі права.')
    return redirect('admin_roles')


@staff_required
@require_POST
def role_update(request, pk):
    form = RoleForm(request.POST)
    destination = ('account_detail', pk) if request.POST.get('return_to') == 'account' else ('admin_roles',)
    if not form.is_valid():
        messages.error(request, 'Оберіть одну з доступних ролей.')
        return redirect(*destination)
    with transaction.atomic():
        DriverPermissionSettings.objects.get_or_create(pk=1)
        DriverPermissionSettings.objects.select_for_update().get(pk=1)
        actor = User.objects.filter(pk=request.user.pk, is_active=True).first()
        if not actor or not actor.manages_users:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        account = get_object_or_404(User.objects.select_for_update(), pk=pk)
        if account.pk == request.user.pk or account.is_superuser:
            messages.error(request, 'Власну роль і роль суперкористувача змінювати не можна.')
        elif account.role == form.cleaned_data['role']:
            messages.info(request, 'Роль уже встановлено.')
        elif account.role == User.Role.ADMIN and account.is_active and User.objects.filter(role=User.Role.ADMIN, is_active=True).count() <= 1:
            messages.error(request, 'Потрібно зберегти хоча б одного активного адміністратора.')
        elif account.role == User.Role.DRIVER and (
            account.default_vehicles.exists() or account.vehicle_assignments.filter(ended_at__isnull=True).exists()
            or account.trips.filter(status__in=Trip.ACTIVE + ['queued']).exists()
        ):
            messages.error(request, 'Спочатку завершіть активні рейси, приберіть рейси з черги та звільніть закріплене авто.')
        else:
            old_role = account.get_role_display()
            account.role = form.cleaned_data['role']
            account.save(update_fields=['role'])
            AuditEvent.objects.create(actor=request.user, action='Змінено роль користувача', detail=f'{account.full_name} ({account.phone}): {old_role} → {account.get_role_display()}')
            messages.success(request, 'Роль користувача змінено.')
    return redirect(*destination)


@staff_required
@require_http_methods(['GET', 'POST'])
def maintenance(request):
    settings = _settings()
    form = OperationsSettingsForm(request.POST if request.method == 'POST' else None, instance=settings)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            updated_settings = form.save(commit=False)
            updated_settings.save(update_fields=[*form.Meta.fields, 'updated_at'])
            AuditEvent.objects.create(actor=request.user, action='Змінено налаштування обслуговування', detail='; '.join(f'{key}={value}' for key, value in form.cleaned_data.items()))
        messages.success(request, 'Налаштування збережено. Час резервування — за Києвом.')
        return redirect('admin_maintenance')
    # Invalid POST must not change the preview's saved retention horizon.
    saved_settings = _settings()
    worker_label, worker_healthy = _worker_state(saved_settings)
    return render(request, 'fleet/admin_maintenance.html', {
        'form': form, 'settings': saved_settings, 'worker_label': worker_label, 'worker_healthy': worker_healthy,
        'last_restore_test': OperationJob.objects.filter(kind='restore_test').order_by('-created_at').first(),
        'preview': _preview_rows(saved_settings.cleanup_days),
        'page_obj': Paginator(OperationJob.objects.select_related('requested_by').order_by('-created_at'), 20).get_page(request.GET.get('page')),
    })


@staff_required
@require_POST
def backup_now(request):
    operations.queue_job('backup', user=request.user)
    messages.success(request, 'Резервну копію поставлено в чергу. Результат з’явиться в історії обслуговування.')
    return redirect('admin_maintenance')


@staff_required
@require_POST
def cleanup_now(request):
    if request.POST.get('confirm') != 'yes':
        messages.error(request, 'Підтвердьте очищення лише технічних записів.')
    else:
        operations.queue_job('cleanup', user=request.user)
        messages.success(request, 'Очищення технічних записів поставлено в чергу.')
    return redirect('admin_maintenance')


@staff_required
@require_GET
def download(request, pk):
    job = get_object_or_404(OperationJob, pk=pk, kind='backup', status='succeeded')
    try:
        path = operations.backup_path(job)
        handle = path.open('rb')
    except (ValueError, OSError):
        raise Http404('Архів недоступний або вже вилучений за строком зберігання.')
    response = FileResponse(handle, as_attachment=True, filename=path.name)
    response['Cache-Control'] = 'private, no-store'
    return response

