import warnings
from io import BytesIO
from pathlib import Path
from PIL import Image, UnidentifiedImageError
from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from .models import User, Trip, Document, normalize_phone

class PhoneLoginForm(AuthenticationForm):
    username = forms.CharField(label="Телефон", widget=forms.TextInput(attrs={"autocomplete":"username", "inputmode":"tel", "placeholder":"+380…"}))
    password = forms.CharField(label="Пароль", strip=False, widget=forms.PasswordInput(attrs={"autocomplete":"current-password"}))
    def clean_username(self): return normalize_phone(self.cleaned_data["username"])

class TripForm(forms.ModelForm):
    class Meta:
        model = Trip
        fields = ["vehicle","driver","origin","destination","cargo","planned_tons","planned_date","distance_km","ttn_number","notes"]
        labels = {"vehicle":"Авто", "driver":"Водій"}
        widgets = {"planned_date":forms.DateInput(attrs={"type":"date"}, format="%Y-%m-%d"), "notes":forms.Textarea(attrs={"rows":3})}
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["driver"].queryset = User.objects.filter(role="driver", is_active=True)
    def clean(self):
        data = super().clean()
        vehicle, driver = data.get("vehicle"), data.get("driver")
        if vehicle and vehicle.in_repair: self.add_error("vehicle", "Авто на ремонті.")
        if vehicle and Trip.objects.filter(vehicle=vehicle,status__in=Trip.ACTIVE).exists(): self.add_error("vehicle", "Авто вже має активний рейс.")
        if driver and Trip.objects.filter(driver=driver,status__in=Trip.ACTIVE).exists(): self.add_error("driver", "Водій уже має активний рейс.")
        return data

class AssignmentForm(forms.Form):
    driver = forms.ModelChoiceField(label="Новий водій", queryset=User.objects.none())
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["driver"].queryset = User.objects.filter(role="driver",is_active=True)

class DocumentForm(forms.ModelForm):
    class Meta:
        model=Document
        fields=["kind","file"]
        labels={"file":"Фото або PDF (до 20 МБ)"}
        widgets={"file":forms.ClearableFileInput(attrs={"accept":"image/jpeg,image/png,image/webp,application/pdf"})}
    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > settings.MAX_DOCUMENT_BYTES:
            raise ValidationError("Максимальний розмір — 20 МБ.")
        data = f.read()
        ext = Path(f.name).suffix.lower()
        if ext == ".pdf" and data.startswith(b"%PDF-"):
            self.detected_type = "application/pdf"
        else:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    image = Image.open(BytesIO(data))
                    formats = {"JPEG":(".jpg",".jpeg"), "PNG":(".png",), "WEBP":(".webp",)}
                    if image.format not in formats or ext not in formats[image.format]:
                        raise ValueError("Unsupported image")
                    self.detected_type = Image.MIME[image.format]
                    image.verify()
            except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
                raise ValidationError("Підтримуються справжні JPG, PNG, WebP та PDF. HEIC потрібно експортувати як JPG.")
        f.seek(0)
        return f

class AccountForm(forms.ModelForm):
    class Meta:
        model=User
        fields=["full_name","phone","role"]
    def clean_phone(self): return normalize_phone(self.cleaned_data["phone"])
