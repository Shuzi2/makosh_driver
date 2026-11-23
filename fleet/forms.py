import warnings
import re
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener
from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from .models import User, Vehicle, Trailer, Trip, Document, normalize_phone

register_heif_opener()

class VehicleSelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value and hasattr(value, 'instance'):
            option['attrs']['data-driver'] = value.instance.default_driver_id or ''
        return option

class PhoneLoginForm(AuthenticationForm):
    username = forms.CharField(label="Телефон", widget=forms.TextInput(attrs={"autocomplete":"username", "inputmode":"tel", "placeholder":"+380…"}))
    password = forms.CharField(label="Пароль", strip=False, widget=forms.PasswordInput(attrs={"autocomplete":"current-password"}))
    def clean_username(self): return normalize_phone(self.cleaned_data["username"])

class TripForm(forms.ModelForm):
    origin_point_confirmed=forms.BooleanField(required=False,widget=forms.HiddenInput)
    destination_point_confirmed=forms.BooleanField(required=False,widget=forms.HiddenInput)
    contact_phone = forms.CharField(label='Телефон на розвантаженні', required=False, max_length=40,
        widget=forms.TextInput(attrs={'type':'tel','inputmode':'tel','placeholder':'+380…'}))
    loading_contact_phone = forms.CharField(label='Телефон на завантаженні', required=False, max_length=40,
        widget=forms.TextInput(attrs={'type':'tel','inputmode':'tel','placeholder':'+380…'}))
    scheduling = forms.ChoiceField(label='Коли виконувати', required=False,
        choices=[('current','Поточний рейс'),('queued','У чергу — після поточного рейсу')],
        help_text='Рейс у черзі водій побачить одразу. Почати його можна, коли авто й водій вільні.')
    class Meta:
        model = Trip
        fields = ["vehicle","driver","origin","origin_lat","origin_lon","loading_contact_name","loading_contact_phone","destination","destination_lat","destination_lon","contact_name","contact_phone","cargo","planned_tons","planned_date","ttn_number","notes"]
        labels = {"vehicle":"Авто", "driver":"Водій"}
        widgets = {"vehicle":VehicleSelect(), "contact_name":forms.TextInput(attrs={'placeholder':'Ім’я та прізвище'}), "contact_phone":forms.TextInput(attrs={'type':'tel','inputmode':'tel','placeholder':'+380…'}), "planned_date":forms.DateInput(attrs={"type":"date"}, format="%Y-%m-%d"), "notes":forms.Textarea(attrs={"rows":3})}
    def __init__(self, *args, **kwargs):
        self.location_reference=kwargs.pop('location_reference',None)
        super().__init__(*args, **kwargs)
        self.fields["driver"].queryset = User.objects.filter(role="driver", is_active=True)
        self.fields["vehicle"].queryset = Vehicle.objects.select_related('default_driver')
        self.order_fields(['vehicle','driver','scheduling','origin','loading_contact_name','loading_contact_phone','destination','destination_lat','destination_lon','contact_name','contact_phone','cargo','planned_tons','planned_date','ttn_number','notes'])
        self.fields['contact_name'].help_text='Водій побачить контакт після прийняття рейсу.'
        self.fields['loading_contact_name'].help_text='Водій побачить контакт після прийняття рейсу.'
        self.configure_points()
    def configure_points(self):
        for stop in ['origin','destination']:
            for axis in ['lat','lon']:
                name=f'{stop}_{axis}'
                if name in self.fields: self.fields[name].widget.attrs.update({'step':'0.000001','inputmode':'decimal'})
    def clean_contact_phone(self):
        return self.normalize_contact_phone(self.cleaned_data.get('contact_phone',''))
    def clean_loading_contact_phone(self):
        return self.normalize_contact_phone(self.cleaned_data.get('loading_contact_phone',''))
    def clean_planned_tons(self):
        value=self.cleaned_data.get('planned_tons')
        if 'stops-TOTAL_FORMS' not in self.data and value is not None and value>60: raise ValidationError('Вага має бути не більше 60 т.')
        return value
    @staticmethod
    def normalize_contact_phone(value):
        value=re.sub(r'[\s().-]', '', value)
        if not value: return ''
        if re.fullmatch(r'(?:0\d{9}|380\d{9})',value): return normalize_phone(value)
        if not re.fullmatch(r'\+[1-9]\d{6,14}',value):
            raise ValidationError('Введіть номер у форматі +380… або міжнародний номер із +.')
        return value
    def clean(self):
        data = super().clean()
        self.instance.status = 'queued' if data.get('scheduling') == 'queued' else 'planned'
        vehicle, driver = data.get("vehicle"), data.get("driver")
        self.validate_contacts(data)
        from .locations import validate_points
        validate_points(self,data)
        if data.get('scheduling') != 'queued':
            if vehicle and not vehicle.trailer_id: self.add_error('vehicle','Спочатку закріпіть причіп або заплануйте рейс у чергу.')
            if vehicle and vehicle.in_repair: self.add_error("vehicle", "Авто на ремонті. Можна запланувати рейс у чергу.")
            if vehicle and Trip.objects.filter(vehicle=vehicle,status__in=Trip.ACTIVE).exists(): self.add_error("vehicle", "Авто вже має поточний рейс. Оберіть «У чергу».")
            if driver and Trip.objects.filter(driver=driver,status__in=Trip.ACTIVE).exists(): self.add_error("driver", "Водій уже має поточний рейс. Оберіть «У чергу».")
        return data
    def validate_contacts(self,data):
        for name,phone in [('contact_name','contact_phone'),('loading_contact_name','loading_contact_phone')]:
            if data.get(name) and not data.get(phone) and phone not in self.errors:
                self.add_error(phone,'Додайте телефон контактної особи.')
            if data.get(phone) and not data.get(name):
                self.add_error(name,'Укажіть, кому телефонуватиме водій.')


class TripEditForm(TripForm):
    scheduling = None
    revision = forms.DateTimeField(widget=forms.HiddenInput)
    class Meta(TripForm.Meta):
        fields=['origin','origin_lat','origin_lon','loading_contact_name','loading_contact_phone','destination','destination_lat','destination_lon','contact_name','contact_phone','cargo','planned_tons','planned_date','distance_km','ttn_number','notes']
    def __init__(self,*args,**kwargs):
        self.location_reference=kwargs.pop('location_reference',None)
        forms.ModelForm.__init__(self,*args,**kwargs)
        self.initial['revision']=self.instance.updated_at.isoformat()
        self.configure_points()
    def clean(self):
        data=forms.ModelForm.clean(self)
        self.validate_contacts(data)
        from .locations import validate_points
        validate_points(self,data)
        if data.get('revision') and data['revision']!=self.instance.updated_at:
            self.add_error(None,'Рейс уже змінився після відкриття форми. Відкрийте редагування знову, щоб побачити актуальні дані.')
        return data

class TripFactsForm(forms.ModelForm):
    revision=forms.DateTimeField(widget=forms.HiddenInput)
    class Meta:
        model=Trip
        fields=['actual_tons','distance_km','fuel_used_l','ttn_number']
        help_texts={'actual_tons':'Фактична вага вантажу. Якщо ще невідома — залиште порожнім.','distance_km':'Пробіг цього рейсу, внесений вручну.'}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.initial['revision']=self.instance.updated_at.isoformat()
    def clean(self):
        data=super().clean()
        if data.get('revision') and data['revision']!=self.instance.updated_at:
            self.add_error(None,'Рейс уже змінився. Відкрийте актуальні дані та повторіть редагування.')
        return data
    def clean_actual_tons(self):
        value=self.cleaned_data.get('actual_tons')
        if value is not None and value>60: raise ValidationError('Вага має бути не більше 60 т.')
        return value

class ReportFilterForm(forms.Form):
    start=forms.DateField(label='З',widget=forms.DateInput(attrs={'type':'date'}))
    end=forms.DateField(label='По',widget=forms.DateInput(attrs={'type':'date'}))
    driver=forms.ModelChoiceField(label='Водій',queryset=User.objects.none(),required=False,empty_label='Усі водії')
    vehicle=forms.ModelChoiceField(label='Авто',queryset=Vehicle.objects.none(),required=False,empty_label='Усі авто')
    kind=forms.ChoiceField(label='Тип авто',required=False,choices=[('','Усі типи'),('tent','Тенти'),('grain','Зерновози')])
    q=forms.CharField(label='Маршрут, вантаж або ТТН',required=False,max_length=100,widget=forms.TextInput(attrs={'placeholder':'Місто, вантаж, номер ТТН…'}))
    signed=forms.ChoiceField(label='Підписана ТТН',required=False,choices=[('','Усі'),('yes','Є підписана ТТН'),('no','Немає підписаної ТТН')])
    weight=forms.ChoiceField(label='Фактична вага',required=False,choices=[('','Усі'),('yes','Внесена'),('no','Не внесена')])
    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        own=Trip.objects.all() if user.manages_fleet else Trip.objects.filter(driver=user)
        self.fields['driver'].queryset=User.objects.filter(pk__in=own.values('driver_id')).order_by('full_name')
        self.fields['vehicle'].queryset=Vehicle.objects.filter(pk__in=own.values('vehicle_id')).order_by('plate')
    def clean(self):
        data=super().clean()
        if data.get('start') and data.get('end') and data['start']>data['end']:
            raise ValidationError('Дата початку не може бути пізніше дати завершення.')
        return data

class ReportTripForm(forms.ModelForm):
    revision=forms.DateTimeField(widget=forms.HiddenInput)
    class Meta:
        model=Trip
        fields=['origin','destination','cargo','planned_tons','actual_tons','distance_km','fuel_used_l','completed_at','ttn_number']
        labels={'completed_at':'Дата й час завершення'}
        widgets={'completed_at':forms.DateTimeInput(attrs={'type':'datetime-local','step':'1'},format='%Y-%m-%dT%H:%M:%S')}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.initial['revision']=self.instance.updated_at.isoformat()
        self.fields['completed_at'].required=True
        self.fields['completed_at'].help_text='Час за Києвом.'
        self.fields['actual_tons'].help_text='Залиште порожнім, якщо фактична вага ще невідома.'
        if self.instance.route_enabled:
            for name in ['origin','destination','cargo','planned_tons','actual_tons','ttn_number']: self.fields[name].disabled=True
    def clean_completed_at(self):
        value=self.cleaned_data.get('completed_at')
        current=self.instance.completed_at
        return current if value and current and value==current.replace(microsecond=0) else value
    def clean_actual_tons(self):
        value=self.cleaned_data.get('actual_tons')
        if not self.instance.route_enabled and value is not None and value>60: raise ValidationError('Вага має бути не більше 60 т.')
        return value
    def clean(self):
        data=super().clean()
        if data.get('revision') and data['revision']!=self.instance.updated_at:
            self.add_error(None,'Рейс уже змінився. Відкрийте редагування знову, щоб побачити актуальні дані.')
        completed=data.get('completed_at')
        if completed and completed!=self.instance.completed_at and (completed>timezone.now() or completed<self.instance.created_at):
            self.add_error('completed_at','Завершення має бути після створення рейсу й не в майбутньому.')
        return data

class RepairForm(forms.Form):
    reason = forms.CharField(label='Причина ремонту', max_length=1000, widget=forms.Textarea(attrs={'rows':3,'placeholder':'Що сталося або що потрібно відремонтувати'}))
    started_at = forms.DateTimeField(label='Дата й час початку', required=False, widget=forms.DateTimeInput(attrs={'type':'datetime-local'},format='%Y-%m-%dT%H:%M'))
    def clean_started_at(self):
        value = self.cleaned_data.get('started_at') or timezone.now()
        if value > timezone.now(): raise ValidationError('Початок ремонту не може бути в майбутньому.')
        return value

def validate_repair_file(f):
    if not f: return f
    if f.size > settings.MAX_DOCUMENT_BYTES: raise ValidationError('Максимальний розмір — 20 МБ.')
    ext = Path(f.name).suffix.lower()
    try:
        data = f.read()
        if ext == '.pdf' and data.startswith(b'%PDF-'):
            pass
        else:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                image = Image.open(BytesIO(data))
                formats = {'JPEG': ('.jpg', '.jpeg'), 'PNG': ('.png',), 'WEBP': ('.webp',)}
                if image.format not in formats or ext not in formats[image.format]: raise ValueError('Unsupported image')
                image.verify()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValidationError('Додайте справжній PDF, JPG, PNG або WebP.')
    finally:
        f.seek(0)
    return f

class RepairCostsForm(forms.Form):
    amount = forms.DecimalField(label='Витрачено, грн', required=False, min_value=0, max_digits=12, decimal_places=2, widget=forms.NumberInput(attrs={'step':'0.01', 'min':'0', 'placeholder':'Наприклад, 12500.00'}), help_text='Залиште порожнім, якщо сума поки невідома.')
    invoice = forms.FileField(label='Рахунок', required=False, validators=[validate_repair_file], widget=forms.FileInput(attrs={'accept':'.pdf,.jpg,.jpeg,.png,.webp'}))
    act = forms.FileField(label='Акт виконаних робіт', required=False, validators=[validate_repair_file], widget=forms.FileInput(attrs={'accept':'.pdf,.jpg,.jpeg,.png,.webp'}))

class RepairFinishForm(RepairCostsForm):
    finished_at = forms.DateTimeField(label='Дата й час завершення', required=False, widget=forms.DateTimeInput(attrs={'type':'datetime-local'},format='%Y-%m-%dT%H:%M'))
    result = forms.CharField(label='Що зроблено / примітка', required=False, max_length=1000, widget=forms.Textarea(attrs={'rows':3}))
    def clean_finished_at(self):
        value = self.cleaned_data.get('finished_at') or timezone.now()
        if value > timezone.now(): raise ValidationError('Завершення ремонту не може бути в майбутньому.')
        return value

class AssignmentForm(forms.Form):
    driver = forms.ModelChoiceField(label="Новий водій", queryset=User.objects.none())
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["driver"].queryset = User.objects.filter(role="driver",is_active=True)

class DriverSubstitutionForm(forms.Form):
    driver = forms.ModelChoiceField(label='Хто продовжить рейс',queryset=User.objects.none())
    effective_at = forms.DateTimeField(label='Дата й час підміни',required=False,
        widget=forms.DateTimeInput(attrs={'type':'datetime-local'},format='%Y-%m-%dT%H:%M'),help_text='Час за Києвом. Якщо порожньо — поточний час.')
    location = forms.CharField(label='Місце підміни',max_length=180,required=False,widget=forms.TextInput(attrs={'placeholder':'Наприклад, Вінниця, стоянка'}))
    note = forms.CharField(label='Причина / примітка',max_length=1000,required=False,widget=forms.Textarea(attrs={'rows':3,'placeholder':'Наприклад, водій їде відпочивати'}))
    revision = forms.DateTimeField(widget=forms.HiddenInput)
    def __init__(self,*args,trip,**kwargs):
        super().__init__(*args,**kwargs)
        busy=Trip.objects.filter(status__in=Trip.ACTIVE).exclude(pk=trip.pk).values('driver_id')
        self.fields['driver'].queryset=User.objects.filter(role='driver',is_active=True).exclude(pk=trip.driver_id).exclude(pk__in=busy).order_by('full_name')
        self.initial['revision']=trip.updated_at.isoformat()
    def clean_effective_at(self):
        value=self.cleaned_data.get('effective_at') or timezone.now()
        if value>timezone.now(): raise ValidationError('Підміна не може бути в майбутньому.')
        return value

class DocumentForm(forms.ModelForm):
    class Meta:
        model=Document
        fields=["stop","kind","ttn_number","file"]
        labels={"file":"Файл (до 20 МБ)"}
    def __init__(self,*args,**kwargs):
        trip=kwargs.pop('trip',None)
        super().__init__(*args,**kwargs)
        self.trip=trip
        if trip and trip.route_enabled:
            self.fields['stop'].queryset=trip.stops.exclude(state='cancelled')
            self.fields['stop'].required=True;self.fields['stop'].label='Завантаження / розвантаження' if trip.is_simple_route else 'Точка маршруту'
            if trip.is_simple_route: self.fields['stop'].label_from_instance=lambda stop: f'{stop.get_kind_display()}: {stop.address}'
            self.fields['stop'].empty_label='Оберіть завантаження або розвантаження'
            self.initial.setdefault('stop',trip.current_stop)
            self.fields['ttn_number'].help_text='Якщо в точці кілька ТТН — указуйте номер документа, який додаєте.'
        else:
            del self.fields['stop'];del self.fields['ttn_number']
    def clean(self):
        data=super().clean();stop=data.get('stop')
        if stop and data.get('kind') in ['ttn','signed_ttn']:
            if data.get('kind')=='signed_ttn' and stop.kind!='unload': self.add_error('stop','Підписану ТТН додайте до відповідного розвантаження.')
            numbers=stop.ttn_numbers;number=data.get('ttn_number','').strip()
            if not number and len(numbers)==1: data['ttn_number']=numbers[0]
            elif len(numbers)>1 and not number: self.add_error('ttn_number','У цій точці кілька ТТН. Укажіть номер для цих фото.')
            if number and numbers and number not in numbers: self.add_error('ttn_number','Цього номера немає в точці маршруту. Перевірте номер або попросіть логіста уточнити точку.')
        return data
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
                    image_format=image.format
                    if image_format not in (*formats, 'HEIF', 'AVIF', 'GIF', 'BMP', 'TIFF'):
                        raise ValueError("Unsupported image")
                    image.verify()
                    if image_format in formats:
                        self.detected_type=Image.MIME[image_format]
                        if ext not in formats[image_format]:
                            f.name=(Path(f.name).stem or 'photo')+formats[image_format][0]
                    else:
                        with Image.open(BytesIO(data)) as original:
                            original.seek(0)
                            converted=ImageOps.exif_transpose(original)
                            if converted.mode in ('RGBA','LA') or 'transparency' in converted.info:
                                rgba=converted.convert('RGBA')
                                background=Image.new('RGB',rgba.size,'white')
                                background.paste(rgba,mask=rgba.getchannel('A'))
                                converted=background
                            else:
                                converted=converted.convert('RGB')
                            output=BytesIO();converted.save(output,format='JPEG',quality=92)
                        if output.tell()>settings.MAX_DOCUMENT_BYTES:
                            raise ValidationError('Фото після обробки завелике. Оберіть менше фото.')
                        f=SimpleUploadedFile((Path(f.name).stem or 'photo')+'.jpg',output.getvalue(),content_type='image/jpeg')
                        self.detected_type='image/jpeg'
            except (Image.DecompressionBombError, Image.DecompressionBombWarning):
                raise ValidationError("Розміри зображення завеликі для безпечної обробки.")
            except (UnidentifiedImageError, OSError, ValueError):
                # Unknown content is stored as a private download, never rendered inline.
                self.detected_type='application/octet-stream'
        f.seek(0)
        return f

class PersonNameFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['full_name'].required = False
        self.fields['full_name'].widget = forms.HiddenInput()
        if not self.is_bound and self.instance and self.instance.full_name:
            parts = self.instance.full_name.split(maxsplit=2)
            for name, value in zip(('surname', 'given_name', 'patronymic'), parts):
                self.initial[name] = value

    def clean(self):
        data = super().clean()
        parts = [data.get('surname', '').strip(), data.get('given_name', '').strip(), data.get('patronymic', '').strip()]
        if any(parts):
            if not parts[0]: self.add_error('surname', 'Вкажіть прізвище.')
            if not parts[1]: self.add_error('given_name', 'Вкажіть ім’я.')
            full_name = ' '.join(part for part in parts if part)
        elif self.is_bound and any(name in self.data for name in ('surname', 'given_name', 'patronymic')):
            full_name = ''
            self.add_error('surname', 'Вкажіть прізвище та ім’я.')
        else:
            full_name = (data.get('full_name') or '').strip()
            if not full_name: self.add_error('surname', 'Вкажіть прізвище та ім’я.')
        if len(full_name) > 150: self.add_error('surname', 'Повне ім’я має містити не більше 150 символів.')
        data['full_name'] = full_name
        return data

class AccountForm(PersonNameFormMixin, forms.ModelForm):
    surname = forms.CharField(label='Прізвище', required=False, max_length=150)
    given_name = forms.CharField(label='Ім’я', required=False, max_length=150)
    patronymic = forms.CharField(label='По батькові', required=False, max_length=150)
    class Meta:
        model=User
        fields=["full_name","phone","role"]
    def clean_phone(self): return normalize_phone(self.cleaned_data["phone"])

class AccountDetailsForm(PersonNameFormMixin, forms.ModelForm):
    surname = forms.CharField(label='Прізвище', required=False, max_length=150)
    given_name = forms.CharField(label='Ім’я', required=False, max_length=150)
    patronymic = forms.CharField(label='По батькові', required=False, max_length=150)
    class Meta:
        model = User
        fields = ['full_name', 'phone', 'email']
        labels = {'full_name': 'ПІБ', 'phone': 'Телефон', 'email': 'Електронна пошта'}

    def clean_phone(self):
        return normalize_phone(self.cleaned_data['phone'])

class AccountCreateForm(AccountForm):
    email = forms.EmailField(label='Електронна пошта', required=False)
    access_method = forms.ChoiceField(label='Як надати доступ', choices=[('password','Тимчасовий пароль'),('invite','Запрошення на пошту')])
    class Meta(AccountForm.Meta):
        fields = ['full_name','phone','email','role','access_method']
    def clean(self):
        data = super().clean()
        if data.get('access_method') == 'invite' and not data.get('email'):
            self.add_error('email', 'Для запрошення вкажіть електронну пошту.')
        return data

class VehicleForm(forms.ModelForm):
    transfer_from_vehicle = forms.BooleanField(label='Перевести вільного водія з іншого авто',required=False,help_text='Якщо водій закріплений за іншим вільним авто, попереднє закріплення завершиться. Рейси в черзі залишаться без змін.')
    effective_at = forms.DateTimeField(label='Дата й час фактичної пересадки', required=False,
        widget=forms.DateTimeInput(attrs={'type':'datetime-local'}, format='%Y-%m-%dT%H:%M'),
        help_text='Час за Києвом. Можна вказати минулу дату. Якщо залишити порожнім — поточний час.')
    assignment_note = forms.CharField(label='Причина / примітка', required=False, max_length=500,
        widget=forms.Textarea(attrs={'rows':2, 'placeholder':'Наприклад: підміна, відпустка або постійне переведення'}))
    class Meta:
        model = Vehicle
        fields = ['plate', 'trailer_plate', 'kind', 'default_driver']
        labels = {'plate': 'Номер тягача', 'trailer_plate': 'Номер причепа', 'kind': 'Тип авто', 'default_driver': 'Закріплений водій'}
        widgets = {'plate': forms.TextInput(attrs={'placeholder': 'AB1234CD'}), 'trailer_plate': forms.TextInput(attrs={'placeholder': 'AB5678CD'})}
        help_texts = {'default_driver': 'Можна залишити без водія або обрати іншого. Водій в активному рейсі змінюється окремо в самому рейсі.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['default_driver'].queryset = User.objects.filter(role='driver', is_active=True).order_by('full_name')
        self.fields['default_driver'].empty_label = 'Поки без водія'
        self.initial.setdefault('effective_at', timezone.localtime().replace(second=0, microsecond=0))

    def clean_effective_at(self):
        value = self.cleaned_data.get('effective_at')
        if value and value > timezone.now():
            raise ValidationError('Дата пересадки не може бути в майбутньому.')
        return value

    def normalize_plate(self, field):
        value = re.sub(r'\s+', '', self.cleaned_data[field]).upper()
        value = value.translate(str.maketrans('АВЕКМНОРСТУХІ', 'ABEKMHOPCTYXI'))
        if not re.fullmatch(r'[A-Z0-9]{4,12}', value):
            raise ValidationError('Вкажіть номер: від 4 до 12 літер і цифр, без спеціальних символів.')
        return value

    def clean_plate(self): return self.normalize_plate('plate')
    def clean_trailer_plate(self): return self.normalize_plate('trailer_plate') if self.cleaned_data.get('trailer_plate') else None

    def _get_validation_exclusions(self):
        exclude=super()._get_validation_exclusions()
        if self.cleaned_data.get('transfer_from_vehicle'): exclude.add('default_driver')
        return exclude

    def clean(self):
        data = super().clean()
        driver = data.get('default_driver')
        trailer_plate=data.get('trailer_plate')
        if trailer_plate and data.get('kind'):
            trailer=Trailer.objects.filter(plate=trailer_plate).first()
            if trailer and trailer.kind!=data['kind']:
                self.add_error('kind',f'Для причепа {trailer_plate} вказаний тип «{trailer.get_kind_display()}».')
        if driver:
            assigned = Vehicle.objects.filter(default_driver=driver).exclude(pk=self.instance.pk).first()
            if assigned:
                if not data.get('transfer_from_vehicle'):
                    self.add_error('default_driver', f'Водій уже закріплений за {assigned.plate}. Щоб перевести його, позначте відповідний пункт нижче.')
                elif not self.instance.pk or self.instance.in_repair or self.instance.trips.filter(status__in=Trip.ACTIVE).exists() or assigned.in_repair or assigned.trips.filter(status__in=Trip.ACTIVE).exists() or driver.trips.filter(status__in=Trip.ACTIVE).exists():
                    self.add_error('default_driver','Переведення доступне, коли обидва авто і водій вільні та не перебувають на ремонті.')
        if self.instance.pk and (self.instance.trips.exists() or self.instance.assignments.exists() or self.instance.outgoing_trip_transfers.exists() or self.instance.incoming_trip_transfers.exists()):
            for field in ('plate', 'trailer_plate', 'kind'):
                if field in self.changed_data:
                    self.add_error(field, 'Авто вже має історію. Для іншого тягача чи причепа додайте новий комплект, щоб зберегти попередні записи.')
        return data


class TrailerSelectField(forms.ModelChoiceField):
    def label_from_instance(self, trailer):
        vehicle=getattr(trailer,'vehicle',None)
        return f'{trailer.plate} · {trailer.get_kind_display()} · '+(f'тягач {vehicle.plate}' if vehicle else 'вільний')


class TrailerChangeForm(forms.Form):
    trailer = TrailerSelectField(label='Причіп',queryset=Trailer.objects.none(),empty_label='Оберіть причіп')
    previous_trailer = forms.CharField(required=False, widget=forms.HiddenInput)
    note = forms.CharField(label='Причина / примітка', required=False, max_length=500, widget=forms.Textarea(attrs={'rows': 3}))
    def __init__(self, *args, vehicle, **kwargs):
        super().__init__(*args, **kwargs)
        self.vehicle=vehicle
        self.fields['trailer'].queryset=Trailer.objects.select_related('vehicle').order_by('plate')
        self.initial.update(previous_trailer=vehicle.trailer_plate or '',trailer=vehicle.trailer_id)
    def clean_previous_trailer(self):
        value = self.cleaned_data['previous_trailer']
        if value != (self.vehicle.trailer_plate or ''):
            raise ValidationError('Причіп уже змінився. Оновіть сторінку й перевірте актуальні дані.')
        return value


class TrailerCreateForm(forms.ModelForm):
    class Meta:
        model=Trailer
        fields=['plate','kind']
        labels={'plate':'Номер причепа','kind':'Тип причепа'}
    def clean_plate(self):
        return VehicleForm.normalize_plate(self,'plate')
