from django import forms

from .models import OperationsSettings, User, MailSettings

class MailSettingsForm(forms.ModelForm):
    password = forms.CharField(label='Пароль SMTP', required=False, widget=forms.PasswordInput(render_value=False), help_text='Залиште порожнім, щоб зберегти попередній пароль.')
    class Meta:
        model = MailSettings
        fields = ['enabled','host','port','security','username','password','from_email','from_name','site_url']
        labels = {'enabled':'Увімкнути відправку', 'host':'SMTP сервер', 'port':'Порт', 'security':'Захист з’єднання', 'username':'Ім’я користувача', 'from_email':'Адреса відправника', 'from_name':'Ім’я відправника', 'site_url':'Адреса сайту для запрошень'}
    def clean(self):
        data = super().clean()
        if data.get('enabled'):
            for field in ('host','port','username','from_email','from_name','site_url'):
                if not data.get(field): self.add_error(field, 'Заповніть це поле.')
            if not data.get('password') and not self.instance.password_cipher:
                self.add_error('password', 'Вкажіть пароль SMTP.')
        return data


class OperationsSettingsForm(forms.ModelForm):
    backup_time = forms.TimeField(label='Час щоденної копії (Київ)', widget=forms.TimeInput(format='%H:%M', attrs={'type': 'time'}))
    backup_keep = forms.IntegerField(label='Кількість успішних щоденних копій', min_value=1, max_value=90)
    cleanup_days = forms.IntegerField(label='Зберігати технічні записи, днів', min_value=90, max_value=3650)

    class Meta:
        model = OperationsSettings
        fields = ['backup_enabled', 'backup_time', 'backup_keep', 'cleanup_enabled', 'cleanup_days']
        labels = {'backup_enabled': 'Автоматичне резервування', 'cleanup_enabled': 'Автоматичне очищення'}


class RoleForm(forms.Form):
    role = forms.ChoiceField(choices=User.Role.choices, label='Нова роль')


class JournalFilterForm(forms.Form):
    user = forms.ModelChoiceField(queryset=User.objects.order_by('full_name', 'pk'), required=False, label='Користувач')
    category = forms.ChoiceField(required=False, label='Тип дії')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import AuditEvent
        actions = AuditEvent.objects.order_by('action').values_list('action', flat=True).distinct()
        self.fields['category'].choices = [('', 'Усі дії')] + [(action, action) for action in actions]

    q = forms.CharField(required=False, max_length=150, label='Дія, опис або користувач')
    date_from = forms.DateField(required=False, label='Від', widget=forms.DateInput(attrs={'type': 'date'}))
    date_to = forms.DateField(required=False, label='До', widget=forms.DateInput(attrs={'type': 'date'}))

