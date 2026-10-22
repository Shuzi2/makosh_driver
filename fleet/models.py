import re, uuid
from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models import Q

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
        ADMIN = "admin", "Адміністратор"
        LOGISTIC = "logistic", "Логіст"
        MANAGER = "manager", "Керівник"
        DRIVER = "driver", "Водій"
    username = None
    phone = models.CharField("Телефон", max_length=13, unique=True)
    full_name = models.CharField("ПІБ", max_length=150)
    role = models.CharField("Роль", max_length=16, choices=Role.choices, default=Role.DRIVER)
    must_change_password = models.BooleanField(default=True)
    objects = UserManager()
    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["full_name"]
    @property
    def manages_fleet(self):
        return self.is_superuser or self.role in (self.Role.ADMIN, self.Role.LOGISTIC)
    @property
    def manages_users(self):
        return self.is_superuser or self.role == self.Role.ADMIN
    def save(self, *args, **kwargs):
        self.phone = normalize_phone(self.phone)
        super().save(*args, **kwargs)
    def __str__(self):
        return self.full_name or self.phone

class Vehicle(models.Model):
    class Kind(models.TextChoices):
        TENT = "tent", "Тент"
        GRAIN = "grain", "Зерновоз"
    plate = models.CharField("Тягач", max_length=20, unique=True)
    trailer_plate = models.CharField("Причіп", max_length=20, unique=True)
    brand = models.CharField("Марка", max_length=40, default="SCANIA")
    kind = models.CharField("Тип", max_length=12, choices=Kind.choices)
    default_driver = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="default_vehicles")
    in_repair = models.BooleanField("На ремонті", default=False)
    notes = models.TextField("Примітка", blank=True, max_length=2000)
    class Meta:
        ordering = ["plate"]
    def __str__(self):
        return f"{self.plate} / {self.trailer_plate}"

class Trip(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", "Заплановано"
        LOADING = "loading", "Завантаження"
        ROAD = "road", "У дорозі"
        UNLOADING = "unloading", "Розвантаження"
        COMPLETED = "completed", "Завершено"
        CANCELLED = "cancelled", "Скасовано"
    ACTIVE = ["planned", "loading", "road", "unloading"]
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="trips")
    driver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="trips")
    origin = models.CharField("Звідки", max_length=180)
    destination = models.CharField("Куди / адреса", max_length=250)
    cargo = models.CharField("Вантаж", max_length=120)
    planned_tons = models.DecimalField("Планова вага, т", max_digits=7, decimal_places=2, validators=[MinValueValidator(0.01), MaxValueValidator(60)])
    actual_tons = models.DecimalField("Фактична вага, т", max_digits=7, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0.01), MaxValueValidator(60)])
    planned_date = models.DateField("Планова дата")
    distance_km = models.PositiveIntegerField("Пробіг рейсу, км", null=True, blank=True, validators=[MaxValueValidator(20000)])
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLANNED)
    ttn_number = models.CharField("Номер ТТН", max_length=100, blank=True)
    notes = models.TextField("Інструкція водію", blank=True, max_length=3000)
    original_received = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_trips")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        ordering = ["-planned_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["vehicle"], condition=Q(status__in=["planned","loading","road","unloading"]), name="one_active_trip_per_vehicle"), models.UniqueConstraint(fields=["driver"], condition=Q(status__in=["planned","loading","road","unloading"]), name="one_active_trip_per_driver")]
    def __str__(self):
        return f"Рейс №{self.pk}: {self.origin} → {self.destination}"
    @property
    def is_active(self):
        return self.status in self.ACTIVE
    @property
    def has_signed_ttn(self):
        return self.documents.filter(kind="signed_ttn").exists()

def document_path(instance, filename):
    return f"trips/{instance.trip_id}/{uuid.uuid4().hex}.{filename.rsplit('.',1)[-1].lower()}"

class Document(models.Model):
    class Kind(models.TextChoices):
        TTN = "ttn", "ТТН із завантаження"
        SIGNED_TTN = "signed_ttn", "Підписана ТТН"
        WEIGHT = "weight", "Ваговий талон"
        OTHER = "other", "Інший документ"
    trip = models.ForeignKey(Trip, on_delete=models.PROTECT, related_name="documents")
    kind = models.CharField("Тип документа", max_length=16, choices=Kind.choices)
    file = models.FileField(upload_to=document_path, max_length=250)
    original_name = models.CharField(max_length=250)
    content_type = models.CharField(max_length=80)
    size = models.PositiveIntegerField()
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-uploaded_at"]

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
