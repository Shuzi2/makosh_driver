import re, uuid
from decimal import Decimal
from .operations_models import OperationsSettings, OperationJob, OperationsMetricSample
from .permissions_models import DriverPermissionSettings, ModeratorPermissionSettings
from .mail_models import MailSettings
from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models import Q, F
from django.utils import timezone

def normalize_phone(value):
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    if len(digits) == 10 and digits.startswith("0"):
        digits = "38" + digits
    if not re.fullmatch(r"380[0-9]{9}", digits):
        raise ValidationError("Введіть український номер: +380XXXXXXXXX або 0XXXXXXXXX.")
    return "+" + digits

class UserManager(BaseUserManager):
    use_in_migrations = True
    def create_user(self, phone, password=None, **extra):
        user = self.model(phone=normalize_phone(phone), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user
    def create_superuser(self, phone, password=None, **extra):
        extra.update(is_staff=True, is_superuser=True, role="admin", must_change_password=False)
        return self.create_user(phone, password, **extra)

class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Логіст / Адміністратор"
        MODERATOR = "moderator", "Модератор"
        DRIVER = "driver", "Водій"
    username = None
    phone = models.CharField("Телефон", max_length=13, unique=True)
    full_name = models.CharField("ПІБ", max_length=150)
    role = models.CharField("Роль", max_length=16, choices=Role.choices, default=Role.DRIVER)
    must_change_password = models.BooleanField(default=True)
    can_delete_test_trips = models.BooleanField('Може видаляти тестові рейси', default=False)
    rest_started_at = models.DateTimeField(null=True,blank=True)
    rest_changed_at = models.DateTimeField(default=timezone.now)
    objects = UserManager()
    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["full_name"]
    @property
    def manages_fleet(self):
        return self.role in (self.Role.ADMIN, self.Role.MODERATOR)
    @property
    def manages_users(self):
        return self.role == self.Role.ADMIN
    class Meta(AbstractUser.Meta):
        abstract = False
        constraints = [models.CheckConstraint(condition=Q(role__in=["admin", "moderator", "driver"]), name="user_role_valid")]
    def save(self, *args, **kwargs):
        self.phone = normalize_phone(self.phone)
        super().save(*args, **kwargs)
    def __str__(self):
        return self.full_name or self.phone

class Trailer(models.Model):
    plate = models.CharField('Номер причепа', max_length=20, unique=True)
    kind = models.CharField('Тип причепа',max_length=12,choices=[('tent','Тент'),('grain','Зерновоз')])
    class Meta:
        ordering = ['plate']
    def __str__(self): return self.plate


class Vehicle(models.Model):
    class Kind(models.TextChoices):
        TENT = "tent", "Тент"
        GRAIN = "grain", "Зерновоз"
    plate = models.CharField("Тягач", max_length=20, unique=True)
    trailer_plate = models.CharField("Причіп", max_length=20, unique=True, null=True, blank=True)
    trailer = models.OneToOneField(Trailer, null=True, blank=True, on_delete=models.PROTECT, related_name='vehicle')
    brand = models.CharField("Марка", max_length=40, default="SCANIA")
    kind = models.CharField("Тип", max_length=12, choices=Kind.choices)
    default_driver = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="default_vehicles")
    in_repair = models.BooleanField("На ремонті", default=False)
    notes = models.TextField("Примітка", blank=True, max_length=2000)
    odometer_km = models.PositiveIntegerField('Поточний пробіг одометра, км',null=True,blank=True,validators=[MaxValueValidator(9999999)])
    odometer_updated_at = models.DateTimeField(null=True,blank=True)
    class Meta:
        ordering = ["plate"]
        constraints = [models.UniqueConstraint(fields=['default_driver'], condition=Q(default_driver__isnull=False), name='one_default_vehicle_per_driver')]
    def save(self, *args, **kwargs):
        fields=kwargs.get('update_fields')
        if fields is None or 'trailer_plate' in fields:
            self.trailer_plate=self.trailer_plate or None
            self.trailer=Trailer.objects.get_or_create(plate=self.trailer_plate,defaults={'kind':self.kind})[0] if self.trailer_plate else None
            if fields is not None: kwargs['update_fields']=set(fields)|{'trailer'}
        super().save(*args, **kwargs)
    def __str__(self):
        return f"{self.plate} / {self.trailer_plate or 'Без причепа'}"


class SavedPlace(models.Model):
    name=models.CharField('Назва місця',max_length=150)
    operation=models.CharField('Використання',max_length=8,choices=[('both','Завантаження й розвантаження'),('load','Завантаження'),('unload','Розвантаження')],default='both')
    address=models.CharField('Адреса',max_length=250)
    settlement_code=models.CharField(max_length=19,blank=True)
    settlement_name=models.CharField(max_length=180,blank=True)
    region=models.CharField('Область',max_length=150,blank=True)
    district=models.CharField('Район',max_length=150,blank=True)
    community=models.CharField('Громада',max_length=150,blank=True)
    lat=models.DecimalField('Широта',max_digits=9,decimal_places=6,null=True,blank=True,validators=[MinValueValidator(-90),MaxValueValidator(90)])
    lon=models.DecimalField('Довгота',max_digits=9,decimal_places=6,null=True,blank=True,validators=[MinValueValidator(-180),MaxValueValidator(180)])
    contact_name=models.CharField('Контактна особа',max_length=150,blank=True)
    contact_phone=models.CharField('Телефон',max_length=16,blank=True)
    contact_phone_alt=models.CharField('Додатковий телефон',max_length=16,blank=True)
    notes=models.TextField('Вказівки для водія',max_length=2000,blank=True)
    is_active=models.BooleanField(default=True)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.PROTECT,related_name='saved_places')
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta:
        ordering=['name','pk']
    def __str__(self): return self.name


class MaintenanceItem(models.Model):
    vehicle=models.ForeignKey(Vehicle,on_delete=models.PROTECT,related_name='maintenance_items')
    tractor_plate=models.CharField(max_length=20,blank=True,editable=False)
    trailer_plate=models.CharField(max_length=20,blank=True,editable=False)
    kind=models.CharField('Тип',max_length=10,choices=[('service','Планове ТО'),('document','Документ авто')])
    applies_to=models.CharField('Стосується',max_length=8,choices=[('tractor','Тягач'),('trailer','Причіп')],default='tractor')
    title=models.CharField('Назва',max_length=150)
    document_number=models.CharField('Номер документа',max_length=100,blank=True)
    due_date=models.DateField('Виконати / поновити до',null=True,blank=True)
    due_km=models.PositiveIntegerField('Пробіг для ТО, км',null=True,blank=True,validators=[MaxValueValidator(9999999)])
    reminder_days=models.PositiveSmallIntegerField('Нагадати за днів',default=30,validators=[MaxValueValidator(365)])
    reminder_km=models.PositiveIntegerField('Нагадати за км',default=1000,validators=[MaxValueValidator(100000)])
    interval_months=models.PositiveSmallIntegerField('Повторювати кожні місяців',null=True,blank=True,validators=[MinValueValidator(1),MaxValueValidator(120)])
    interval_km=models.PositiveIntegerField('Повторювати кожні км',null=True,blank=True,validators=[MinValueValidator(1),MaxValueValidator(1000000)])
    notes=models.TextField('Примітка',max_length=2000,blank=True)
    completed_at=models.DateTimeField(null=True,blank=True)
    completed_on=models.DateField(null=True,blank=True)
    completed_odometer=models.PositiveIntegerField(null=True,blank=True)
    result=models.TextField(max_length=2000,blank=True)
    completed_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.PROTECT,related_name='completed_maintenance')
    previous=models.OneToOneField('self',null=True,blank=True,on_delete=models.PROTECT,related_name='next_item')
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.PROTECT,related_name='created_maintenance')
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta:
        ordering=['completed_at','due_date','due_km','pk']
        constraints=[models.CheckConstraint(condition=Q(due_date__isnull=False)|Q(due_km__isnull=False),name='maintenance_has_due')]

    def save(self,*args,**kwargs):
        if not self.pk:
            self.tractor_plate=self.vehicle.plate
            self.trailer_plate=self.vehicle.trailer_plate or ''
        super().save(*args,**kwargs)

    def status_info(self,today=None):
        today=today or timezone.localdate()
        if self.completed_at: return {'key':'completed','label':'Виконано'}
        days=(self.due_date-today).days if self.due_date else None
        km=self.due_km-self.vehicle.odometer_km if self.due_km is not None and self.vehicle.odometer_km is not None else None
        if (days is not None and days<0) or (km is not None and km<0): key,label='overdue','Прострочено'
        elif (days is not None and days<=self.reminder_days) or (km is not None and km<=self.reminder_km): key,label='soon','Потребує уваги'
        elif self.due_km is not None and km is None: key,label='unknown','Потрібен пробіг авто'
        else: key,label='ok','Заплановано'
        return {'key':key,'label':label,'days':days,'km':km}

class DriverServiceRequest(models.Model):
    vehicle = models.ForeignKey(Vehicle,on_delete=models.PROTECT,related_name='driver_service_requests')
    driver = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='service_requests')
    tractor_plate = models.CharField(max_length=20)
    trailer_plate = models.CharField(max_length=20,blank=True)
    request_key = models.UUIDField(default=uuid.uuid4,unique=True,editable=False)
    category = models.CharField('Тип',max_length=12,choices=[('problem','Несправність'),('service','Планове ТО'),('supply','Запчастини / інше')])
    title = models.CharField('Що потрібно зробити',max_length=150)
    description = models.TextField('Опис',max_length=2000,blank=True)
    parts = models.CharField('Що потрібно купити',max_length=300,blank=True)
    urgent = models.BooleanField('Терміново',default=False)
    due_date = models.DateField('Бажана дата',null=True,blank=True)
    due_km = models.PositiveIntegerField('Термін за пробігом, км',null=True,blank=True,validators=[MaxValueValidator(9999999)])
    status = models.CharField('Стан',max_length=16,choices=[('new','Нове'),('in_progress','У роботі'),('done','Виконано')],default='new')
    manager_note = models.TextField('Відповідь логіста',max_length=1000,blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.PROTECT,related_name='updated_service_requests')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering=['-created_at','-pk']

def repair_document_path(instance, filename):
    from pathlib import Path
    return f'repairs/{instance.vehicle_id}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}'

class UserSessionActivity(models.Model):
    session_hash = models.CharField(max_length=64, primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='session_activity')
    browser = models.CharField(max_length=40, blank=True)
    platform = models.CharField(max_length=40, blank=True)
    login_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(db_index=True)

class VehicleRepair(models.Model):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name='repairs')
    reason = models.TextField(max_length=1000)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    result = models.TextField(max_length=1000, blank=True)
    amount = models.DecimalField('Витрати, грн', max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    invoice = models.FileField('Рахунок', upload_to=repair_document_path, blank=True)
    act = models.FileField('Акт', upload_to=repair_document_path, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='started_repairs')
    finished_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='finished_repairs')
    recorded_at = models.DateTimeField(auto_now_add=True)
    finished_recorded_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        ordering = ['-id']
        constraints = [models.UniqueConstraint(fields=['vehicle'], condition=Q(finished_at__isnull=True), name='one_open_repair_per_vehicle'), models.CheckConstraint(condition=Q(started_at__isnull=True) | Q(finished_at__isnull=True) | Q(finished_at__gte=models.F('started_at')), name='repair_dates_ordered')]

class VehicleAssignment(models.Model):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name='assignments')
    driver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='vehicle_assignments')
    driver_name = models.CharField(max_length=150)
    tractor_plate = models.CharField(max_length=20)
    trailer_plate = models.CharField(max_length=20)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=500, blank=True)
    ended_note = models.CharField(max_length=500, blank=True)
    ended_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='ended_assignments')
    ended_recorded_at = models.DateTimeField(null=True, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='recorded_assignments')
    recorded_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['vehicle'], condition=Q(ended_at__isnull=True), name='one_open_assignment_per_vehicle'),
            models.UniqueConstraint(fields=['driver'], condition=Q(ended_at__isnull=True), name='one_open_assignment_per_driver'),
            models.CheckConstraint(condition=Q(started_at__isnull=True) | Q(ended_at__isnull=True) | Q(ended_at__gte=models.F('started_at')), name='assignment_dates_ordered'),
        ]


class TrailerTransfer(models.Model):
    trailer = models.ForeignKey(Trailer,on_delete=models.PROTECT,related_name='transfers')
    from_vehicle = models.ForeignKey(Vehicle,null=True,blank=True,on_delete=models.PROTECT,related_name='trailer_transfers_out')
    to_vehicle = models.ForeignKey(Vehicle,null=True,blank=True,on_delete=models.PROTECT,related_name='trailer_transfers_in')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.PROTECT)
    note = models.CharField(max_length=500,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering=['-created_at','-pk']

class Trip(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", "Заплановано"
        LOADING = "loading", "Завантаження"
        ROAD = "road", "У дорозі"
        UNLOADING = "unloading", "Розвантаження"
        COMPLETED = "completed", "Завершено"
        CANCELLED = "cancelled", "Скасовано"
        QUEUED = "queued", "У черзі"
    ACTIVE = ["planned", "loading", "road", "unloading"]
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="trips")
    driver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="trips")
    completed_tractor_plate = models.CharField(max_length=20, null=True, blank=True, editable=False)
    completed_trailer_plate = models.CharField(max_length=20, null=True, blank=True, editable=False)
    completed_vehicle_kind = models.CharField(max_length=12, null=True, blank=True, editable=False)
    completed_driver_name = models.CharField(max_length=150, null=True, blank=True, editable=False)
    origin = models.CharField("Звідки", max_length=180)
    destination = models.CharField("Куди / адреса", max_length=250)
    origin_lat = models.DecimalField('Широта завантаження',max_digits=9,decimal_places=6,null=True,blank=True,validators=[MinValueValidator(-90),MaxValueValidator(90)])
    origin_lon = models.DecimalField('Довгота завантаження',max_digits=9,decimal_places=6,null=True,blank=True,validators=[MinValueValidator(-180),MaxValueValidator(180)])
    destination_lat = models.DecimalField('Широта розвантаження',max_digits=9,decimal_places=6,null=True,blank=True,validators=[MinValueValidator(-90),MaxValueValidator(90)])
    destination_lon = models.DecimalField('Довгота розвантаження',max_digits=9,decimal_places=6,null=True,blank=True,validators=[MinValueValidator(-180),MaxValueValidator(180)])
    contact_name = models.CharField('Контакт на розвантаження', max_length=150, blank=True)
    contact_phone = models.CharField('Телефон на розвантаженні', max_length=16, blank=True)
    loading_contact_name = models.CharField('Контакт на завантаження', max_length=150, blank=True)
    loading_contact_phone = models.CharField('Телефон на завантаженні', max_length=16, blank=True)
    cargo = models.CharField("Вантаж", max_length=120)
    planned_tons = models.DecimalField("Планова вага, т", max_digits=7, decimal_places=2, validators=[MinValueValidator(0.01), MaxValueValidator(9999)])
    actual_tons = models.DecimalField("Фактична вага, т", max_digits=7, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0.01), MaxValueValidator(9999)])
    route_enabled = models.BooleanField(default=False)
    route_notice = models.TextField(max_length=2000, blank=True)
    route_notice_acknowledged_at = models.DateTimeField(null=True, blank=True)
    planned_date = models.DateField("Планова дата")
    distance_km = models.PositiveIntegerField("Пробіг рейсу, км", null=True, blank=True, validators=[MaxValueValidator(20000)])
    empty_distance_km = models.PositiveIntegerField('Переїзд до завантаження, км',null=True,blank=True,validators=[MaxValueValidator(20000)])
    loaded_distance_km = models.PositiveIntegerField('Пробіг з вантажем, км',null=True,blank=True,validators=[MaxValueValidator(20000)])
    fuel_used_l = models.DecimalField('Витрачено пального, л',max_digits=7,decimal_places=2,null=True,blank=True,validators=[MinValueValidator(0),MaxValueValidator(20000)])
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLANNED)
    ttn_number = models.CharField("Номер ТТН", max_length=100, blank=True)
    notes = models.TextField("Інструкція водію", blank=True, max_length=3000)
    original_received = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_trips")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status_changed_at = models.DateTimeField(default=timezone.now, null=True, blank=True)
    queue_priority_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='accepted_trips')
    acceptance_location = models.CharField('Місце водія під час прийняття рейсу', max_length=250, blank=True)
    acceptance_odometer_km = models.PositiveIntegerField('Показник спідометра при прийнятті, км',null=True,blank=True,validators=[MaxValueValidator(9999999)])
    waybill_overrides = models.JSONField(default=dict,blank=True,editable=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True,blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.PROTECT,related_name='cancelled_trips')
    cancellation_reason = models.CharField(max_length=500,blank=True)
    cancellation_notice_required = models.BooleanField(default=False)
    cancellation_acknowledged_at = models.DateTimeField(null=True,blank=True)
    class Meta:
        ordering = ["-planned_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["vehicle"], condition=Q(status__in=["planned","loading","road","unloading"]), name="one_active_trip_per_vehicle"), models.UniqueConstraint(fields=["driver"], condition=Q(status__in=["planned","loading","road","unloading"]), name="one_active_trip_per_driver")]
    def __str__(self):
        return f"Рейс №{self.pk}: {self.origin} → {self.destination}"
    def save(self, *args, **kwargs):
        if self.status == self.Status.COMPLETED and self.completed_tractor_plate is None and self.vehicle_id and self.driver_id:
            self.completed_tractor_plate = self.vehicle.plate
            self.completed_trailer_plate = self.vehicle.trailer_plate or ''
            self.completed_vehicle_kind = self.vehicle.kind
            self.completed_driver_name = self.driver.full_name
            if kwargs.get('update_fields') is not None:
                kwargs['update_fields'] = set(kwargs['update_fields']) | {'completed_tractor_plate','completed_trailer_plate','completed_vehicle_kind','completed_driver_name'}
        super().save(*args, **kwargs)
    @property
    def report_tractor_plate(self): return self.completed_tractor_plate if self.completed_tractor_plate is not None else self.vehicle.plate
    @property
    def report_trailer_plate(self): return self.completed_trailer_plate if self.completed_trailer_plate is not None else self.vehicle.trailer_plate
    @property
    def report_driver_name(self): return self.completed_driver_name if self.completed_driver_name is not None else self.driver.full_name
    @property
    def report_vehicle_kind(self):
        return dict(Vehicle.Kind.choices).get(self.completed_vehicle_kind if self.completed_vehicle_kind is not None else self.vehicle.kind, '')
    @classmethod
    def ordered_queue(cls, queryset):
        return queryset.order_by(F('queue_priority_at').desc(nulls_last=True),'planned_date','id')
    @property
    def is_active(self):
        return self.status in self.ACTIVE
    @property
    def is_accepted(self):
        return bool(self.accepted_at and self.accepted_by_id and (self.accepted_by_id == self.driver_id or self.accepted_by.manages_fleet))
    @property
    def requires_acceptance(self):
        return self.status in self.ACTIVE+['queued'] and not self.is_accepted and (self.status in ['planned','queued'] or self.transfers.exists())
    @property
    def driver_can_upload_documents(self):
        return self.status != 'cancelled' and not self.requires_acceptance
    @property
    def operational_status_display(self):
        from .trip_stages import status_label
        return status_label(self)
    @property
    def stage_elapsed_display(self):
        from .trip_stages import elapsed_label
        return elapsed_label(self.status_changed_at) if self.is_active else ''
    @property
    def has_signed_ttn(self):
        if self.route_enabled:
            points=[s for s in self.stops.all() if s.kind=='unload' and s.state!='cancelled']
            return bool(points) and all(s.signed_complete for s in points)
        return self.documents.filter(kind="signed_ttn").exists()
    @property
    def current_stop(self):
        if not self.route_enabled or self.status in ['completed','cancelled']: return None
        return next((s for s in self.stops.all() if s.state in ['pending','arrived']),None)
    @property
    def is_simple_route(self):
        points=[s for s in self.stops.all() if s.state!='cancelled']
        return len(points)==2 and points[0].kind=='load' and points[1].kind=='unload'
    @property
    def route_progress(self):
        points=[s for s in self.stops.all() if s.state!='cancelled']
        if self.is_simple_route: return ''
        done=sum(s.state=='completed' for s in points)
        return f'Точка {min(done+1,len(points))} із {len(points)}' if points else ''
    @property
    def route_balance(self):
        points=[s for s in self.stops.all() if s.state=='completed']
        from decimal import Decimal
        balance=sum((s.actual_tons if s.actual_tons is not None else s.planned_tons)*(1 if s.kind=='load' else -1) for s in points) if points else Decimal('0')
        return {'tons':balance,'estimated':any(s.actual_tons is None for s in points)}
    @property
    def loading_directions_url(self):
        from .locations import directions_url
        return directions_url(self.origin,self.origin_lat,self.origin_lon)
    @property
    def unloading_directions_url(self):
        from .locations import directions_url
        return directions_url(self.destination,self.destination_lat,self.destination_lon)

class DriverReportHistory(models.Model):
    source_sheet=models.CharField(max_length=150)
    source_row=models.PositiveIntegerField()
    driver=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.PROTECT,related_name='historical_driver_reports')
    driver_label=models.CharField(max_length=150)
    vehicle_kind=models.CharField(max_length=12,blank=True)
    trip_date=models.DateField(null=True,blank=True)
    source_values=models.JSONField(default=dict)
    report_corrections=models.JSONField(default=dict, blank=True)
    display_values=models.JSONField(default=dict)
    source_raw=models.JSONField(default=list)
    source_formulas=models.JSONField(default=list)
    anomalies=models.JSONField(default=list)
    class Meta:
        ordering=['-trip_date','source_sheet','source_row']
        constraints=[models.UniqueConstraint(fields=['source_sheet','source_row'],name='driver_report_source_row_unique')]

class DriverEconomicsDefaults(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    daily_rate = models.DecimalField('Ставка водія, грн/день', max_digits=10, decimal_places=2, null=True, blank=True, default=1000, validators=[MinValueValidator(0)])
    bonus_per_km = models.DecimalField('Бонус водія, грн/км', max_digits=8, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    days = models.DecimalField('Кількість днів', max_digits=6, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    fuel_price = models.DecimalField('Ціна пального, грн/л', max_digits=8, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    adblue_price = models.DecimalField('Ціна AdBlue, грн/л', max_digits=8, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    adblue_rate = models.DecimalField('Витрата AdBlue, л/100 км', max_digits=7, decimal_places=2, null=True, blank=True, default=1, validators=[MinValueValidator(0)])
    depreciation_tent = models.DecimalField('Амортизація тента, грн/км', max_digits=8, decimal_places=2, null=True, blank=True, default=Decimal('11.70'), validators=[MinValueValidator(0)])
    depreciation_grain = models.DecimalField('Амортизація зерновоза, грн/км', max_digits=8, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    credit_per_km = models.DecimalField('Кредит, грн/км', max_digits=8, decimal_places=2, null=True, blank=True, default=6, validators=[MinValueValidator(0)])
    price = models.DecimalField('Ціна рейсу, грн', max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    extra_cost = models.DecimalField('Додаткові витрати, грн', max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(id=1), name='driver_economics_defaults_singleton')]

class TripEconomics(models.Model):
    trip=models.OneToOneField(Trip,on_delete=models.PROTECT,related_name='economics')
    report_overrides=models.JSONField(default=dict, blank=True)
    price=models.DecimalField('Ціна рейсу, грн',max_digits=12,decimal_places=2,null=True,blank=True,validators=[MinValueValidator(0)])
    daily_rate=models.DecimalField('Ставка водія за день, грн',max_digits=10,decimal_places=2,null=True,blank=True,validators=[MinValueValidator(0)])
    days=models.DecimalField('Кількість днів',max_digits=6,decimal_places=2,null=True,blank=True,validators=[MinValueValidator(0)])
    bonus_per_km=models.DecimalField('Бонус водія, грн/км',max_digits=8,decimal_places=2,null=True,blank=True,validators=[MinValueValidator(0)])
    fuel_price=models.DecimalField('Ціна пального, грн/л',max_digits=8,decimal_places=2,null=True,blank=True,validators=[MinValueValidator(0)])
    adblue_price=models.DecimalField('Ціна AdBlue, грн/л',max_digits=8,decimal_places=2,null=True,blank=True,validators=[MinValueValidator(0)])
    adblue_rate=models.DecimalField('Витрата AdBlue, л/100 км',max_digits=7,decimal_places=2,null=True,blank=True,validators=[MinValueValidator(0)])
    depreciation_per_km=models.DecimalField('Амортизація, грн/км',max_digits=8,decimal_places=2,null=True,blank=True,validators=[MinValueValidator(0)])
    credit_per_km=models.DecimalField('Кредит, грн/км',max_digits=8,decimal_places=2,null=True,blank=True,validators=[MinValueValidator(0)])
    extra_cost=models.DecimalField('Додаткові витрати, грн',max_digits=12,decimal_places=2,null=True,blank=True,validators=[MinValueValidator(0)])
    updated_at=models.DateTimeField(auto_now=True)

class TripStop(models.Model):
    trip = models.ForeignKey(Trip, on_delete=models.PROTECT, related_name='stops')
    position = models.PositiveSmallIntegerField()
    kind = models.CharField('Операція',max_length=8,choices=[('load','Завантаження'),('unload','Розвантаження')])
    address = models.CharField('Адреса',max_length=250)
    settlement_code = models.CharField('Код населеного пункту',max_length=19,blank=True)
    settlement_name = models.CharField('Населений пункт',max_length=180,blank=True)
    region = models.CharField('Область',max_length=150,blank=True)
    district = models.CharField('Район',max_length=150,blank=True)
    community = models.CharField('Громада (ОТГ)',max_length=150,blank=True)
    lat = models.DecimalField('Широта',max_digits=9,decimal_places=6,null=True,blank=True,validators=[MinValueValidator(-90),MaxValueValidator(90)])
    lon = models.DecimalField('Довгота',max_digits=9,decimal_places=6,null=True,blank=True,validators=[MinValueValidator(-180),MaxValueValidator(180)])
    contact_name = models.CharField('Контактна особа',max_length=150,blank=True)
    contact_phone = models.CharField('Телефон',max_length=16,blank=True)
    contact_phone_alt = models.CharField('Додатковий телефон',max_length=16,blank=True)
    cargo = models.CharField('Вантаж',max_length=120)
    planned_tons = models.DecimalField('Планова вага, т',max_digits=7,decimal_places=2,validators=[MinValueValidator(0.01),MaxValueValidator(60)])
    actual_tons = models.DecimalField('Фактична вага, т',max_digits=7,decimal_places=2,null=True,blank=True,validators=[MinValueValidator(0.01),MaxValueValidator(60)])
    ttn_number = models.CharField('Номери ТТН',max_length=500,blank=True)
    notes = models.TextField('Вказівки для цієї точки',max_length=1500,blank=True)
    state = models.CharField(max_length=12,choices=[('pending','Очікує'),('arrived','На місці'),('completed','Виконано'),('cancelled','Скасовано')],default='pending')
    arrived_at = models.DateTimeField(null=True,blank=True)
    completed_at = models.DateTimeField(null=True,blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.PROTECT,related_name='+')
    signed_complete = models.BooleanField(default=False)
    weight_revision = models.PositiveIntegerField(default=0)
    documents_revision = models.PositiveIntegerField(default=0)
    original_received = models.BooleanField('Паперові оригінали отримано',default=False)
    class Meta:
        ordering=['position','pk']
        constraints=[models.CheckConstraint(condition=Q(kind__in=['load','unload']),name='stop_valid_kind'),models.CheckConstraint(condition=Q(state__in=['pending','arrived','completed','cancelled']),name='stop_valid_state')]
    @property
    def driver_place(self):
        from .settlements import resolve_address,normalize
        source=resolve_address(self.address) if not self.region else None
        name=self.settlement_name or (source['title'] if source else self.address.split(',')[0].strip())
        region=self.region or (source['region'] if source else '')
        district=self.district or (source['district'] if source else '')
        community=self.community or (source['community'] if source else '')
        excluded={normalize(v) for v in [name,region,district,community] if v}
        detail=', '.join(part.strip() for part in self.address.split(',')[1:] if normalize(part.strip()) not in excluded)
        return {'name':name,'region':region,'district':district,'community':community,'detail':detail,'has_area':bool(region),'special':(self.settlement_code or (source['code'] if source else '')) in ['UA80000000000093317','UA85000000000065278']}
    @property
    def directions_url(self):
        from .locations import directions_url
        return directions_url(self.address,self.lat,self.lon)
    @property
    def ttn_numbers(self):
        return list(dict.fromkeys(n.strip() for n in re.split(r'[,;\n]+',self.ttn_number) if n.strip()))
    def __str__(self): return f'{self.position}. {self.get_kind_display()}: {self.address}'

class MapSearchState(models.Model):
    last_requested_at=models.DateTimeField(null=True)

class AttentionSkip(models.Model):
    trip = models.ForeignKey(Trip, on_delete=models.PROTECT, related_name='attention_skips')
    category = models.CharField(max_length=16, choices=[('acceptance','Прийняття'),('cancellation','Скасування'),('documents','Підписана ТТН'),('weight','Фактична вага'),('report','Звіт рейсу')])
    stop = models.ForeignKey(TripStop,null=True,blank=True,on_delete=models.PROTECT,related_name='+')
    episode = models.CharField(max_length=64)
    skipped_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    skipped_at = models.DateTimeField(default=timezone.now)
    note = models.CharField(max_length=500, blank=True)
    restored_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['trip','category','episode'], name='unique_attention_skip_episode')]

class MapSearchCache(models.Model):
    key=models.CharField(max_length=64,unique=True)
    results=models.JSONField(default=list)
    cached_at=models.DateTimeField(default=timezone.now)

def document_path(instance, filename):
    return f"trips/{instance.trip_id}/{uuid.uuid4().hex}.{filename.rsplit('.',1)[-1].lower()}"

class ActiveDocumentManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class Document(models.Model):
    class Kind(models.TextChoices):
        TTN = "ttn", "ТТН із завантаження"
        SIGNED_TTN = "signed_ttn", "Підписана ТТН"
        WEIGHT = "weight", "Ваговий талон"
        OTHER = "other", "Інший документ"
    trip = models.ForeignKey(Trip, on_delete=models.PROTECT, related_name="documents")
    stop = models.ForeignKey(TripStop,null=True,blank=True,on_delete=models.PROTECT,related_name='documents')
    ttn_number = models.CharField('Номер ТТН',max_length=100,blank=True)
    kind = models.CharField("Тип документа", max_length=16, choices=Kind.choices)
    file = models.FileField(upload_to=document_path, max_length=250)
    original_name = models.CharField(max_length=250)
    content_type = models.CharField(max_length=80)
    size = models.PositiveIntegerField()
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='removed_documents')
    objects = ActiveDocumentManager()
    all_objects = models.Manager()
    class Meta:
        ordering = ["-uploaded_at"]
        base_manager_name = 'all_objects'

def expense_receipt_path(instance, filename):
    return f'expense-receipts/{instance.trip_id}/{uuid.uuid4().hex}.{filename.rsplit(".",1)[-1].lower()}'


class TripDelay(models.Model):
    class Reason(models.TextChoices):
        QUEUE='queue','Черга на завантаженні / розвантаженні'
        TRAFFIC='traffic','Затор або дорога'
        BREAKDOWN='breakdown','Поломка'
        OTHER='other','Інша причина'
    trip=models.ForeignKey(Trip,on_delete=models.PROTECT,related_name='delays')
    driver=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='reported_delays')
    reason=models.CharField(max_length=16,choices=Reason.choices)
    comment=models.CharField(max_length=500,blank=True)
    minutes=models.PositiveIntegerField(null=True,blank=True)
    created_at=models.DateTimeField(default=timezone.now)


class DelayNotification(models.Model):
    recipient=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='delay_notifications')
    delay=models.ForeignKey(TripDelay,on_delete=models.CASCADE,related_name='notifications')
    read_at=models.DateTimeField(null=True,blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['recipient','delay'],name='delay_notice_recipient_unique')]


class TripExpense(models.Model):
    class Category(models.TextChoices):
        FUEL='fuel','Пальне'
        ROAD='road','Платна дорога'
        PARKING='parking','Стоянка'
        OTHER='other','Інші витрати'
    trip=models.ForeignKey(Trip,on_delete=models.PROTECT,related_name='expenses')
    driver=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='trip_expenses')
    category=models.CharField(max_length=16,choices=Category.choices)
    amount=models.DecimalField(max_digits=12,decimal_places=2)
    currency=models.CharField(max_length=3,choices=[('UAH','Гривня'),('EUR','Євро'),('USD','Долар')])
    litres=models.DecimalField(max_digits=10,decimal_places=2,null=True,blank=True)
    comment=models.CharField(max_length=500,blank=True)
    receipt=models.FileField(upload_to=expense_receipt_path,max_length=250,blank=True)
    created_at=models.DateTimeField(default=timezone.now)
    class Meta:
        ordering=['-created_at','-pk']


class DriverAssignmentNotification(models.Model):
    recipient=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='assignment_notifications')
    trip=models.ForeignKey(Trip,on_delete=models.CASCADE,related_name='assignment_notifications')
    created_at=models.DateTimeField(default=timezone.now)
    read_at=models.DateTimeField(null=True,blank=True)
    resolved_at=models.DateTimeField(null=True,blank=True)
    class Meta:
        ordering=['-created_at','-pk']


class DriverServiceNotification(models.Model):
    recipient=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='service_notifications')
    service_request=models.ForeignKey(DriverServiceRequest,on_delete=models.CASCADE,related_name='notifications')
    message=models.TextField(max_length=1000)
    created_at=models.DateTimeField(default=timezone.now)
    read_at=models.DateTimeField(null=True,blank=True)
    class Meta:
        ordering=['-created_at','-pk']


class PushSubscription(models.Model):
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='push_subscriptions')
    endpoint=models.CharField(max_length=2048,unique=True)
    p256dh=models.CharField(max_length=128)
    auth=models.CharField(max_length=64)
    created_at=models.DateTimeField(default=timezone.now)
    updated_at=models.DateTimeField(auto_now=True)


class PushDelivery(models.Model):
    subscription=models.ForeignKey(PushSubscription,on_delete=models.CASCADE,related_name='deliveries')
    recipient=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='+')
    trip=models.ForeignKey(Trip,null=True,blank=True,on_delete=models.CASCADE,related_name='+')
    kind=models.CharField(max_length=20)
    status=models.CharField(max_length=12,default='pending',db_index=True)
    attempts=models.PositiveSmallIntegerField(default=0)
    created_at=models.DateTimeField(default=timezone.now)
    next_attempt_at=models.DateTimeField(default=timezone.now)
    claimed_at=models.DateTimeField(null=True,blank=True)
    class Meta:
        ordering=['created_at','pk']


class TtnNotification(models.Model):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='ttn_notifications')
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='notifications')
    created_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['recipient', 'document'], name='ttn_notice_recipient_document_unique')]


class AuditEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    trip = models.ForeignKey(Trip, null=True, blank=True, on_delete=models.PROTECT, related_name="events")
    action = models.CharField(max_length=100)
    detail = models.TextField(max_length=1500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-created_at"]

class LoginThrottle(models.Model):
    key = models.CharField(max_length=64, unique=True)
    failures = models.PositiveIntegerField(default=0)
    window_start = models.DateTimeField()

class DriverRequest(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    trip = models.ForeignKey(Trip, null=True, blank=True, on_delete=models.PROTECT)
    kind = models.CharField(max_length=16)
    fingerprint = models.CharField(max_length=64)
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

class TripTransfer(models.Model):
    kind = models.CharField(max_length=16,choices=[('transfer','Передача рейсу'),('substitution','Підміна водія')],default='transfer')
    trip = models.ForeignKey(Trip,on_delete=models.PROTECT,related_name='transfers')
    previous_driver = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='transferred_trips_from')
    next_driver = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='transferred_trips_to')
    from_vehicle = models.ForeignKey(Vehicle,on_delete=models.PROTECT,related_name='outgoing_trip_transfers')
    to_vehicle = models.ForeignKey(Vehicle,on_delete=models.PROTECT,related_name='incoming_trip_transfers')
    previous_status = models.CharField(max_length=16)
    next_status = models.CharField(max_length=16)
    moved_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='trip_transfers')
    moved_at = models.DateTimeField(default=timezone.now)
    effective_at = models.DateTimeField(null=True,blank=True)
    location = models.CharField(max_length=180,blank=True)
    note = models.TextField(max_length=1000,blank=True)
    @property
    def previous_status_label(self):
        return dict(Trip.Status.choices).get(self.previous_status,self.previous_status)
    @property
    def effective_time(self):
        return self.effective_at or self.moved_at
    class Meta:
        ordering=['-moved_at','-pk']
