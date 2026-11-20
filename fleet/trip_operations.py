"""Driver-reported delays and expense records scoped to their own trips."""
from decimal import Decimal
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods
from .forms import DocumentForm
from .models import AuditEvent, DelayNotification, Trip, TripDelay, TripExpense, User
from .permissions_models import ModeratorPermissionSettings


def own_trip(request, pk):
    if request.user.role != 'driver': raise PermissionDenied
    return get_object_or_404(Trip.objects.select_related('vehicle','driver').filter(driver=request.user),pk=pk)


class DelayForm(forms.ModelForm):
    class Meta:
        model=TripDelay
        fields=['reason','minutes','comment']
        labels={'reason':'Причина затримки','minutes':'Орієнтовна затримка, хв (необов’язково)','comment':'Коментар (необов’язково)'}
        widgets={'minutes':forms.NumberInput(attrs={'min':'1','max':'10080'}),'comment':forms.Textarea(attrs={'rows':3,'maxlength':500})}
    def clean_minutes(self):
        value=self.cleaned_data['minutes']
        if value is not None and not 1<=value<=10080: raise forms.ValidationError('Вкажіть від 1 до 10080 хвилин.')
        return value


class ExpenseForm(forms.ModelForm):
    amount=forms.DecimalField(label='Сума',min_value=Decimal('0.01'),max_digits=12,decimal_places=2)
    litres=forms.DecimalField(label='Літри (необов’язково)',min_value=Decimal('0.01'),max_digits=10,decimal_places=2,required=False)
    class Meta:
        model=TripExpense
        fields=['category','amount','currency','litres','comment','receipt']
        labels={'category':'Категорія','currency':'Валюта','comment':'Коментар (необов’язково)','receipt':'Фото або PDF чека (необов’язково)'}
        widgets={'comment':forms.Textarea(attrs={'rows':3,'maxlength':500}),'receipt':forms.FileInput(attrs={'accept':'image/jpeg,image/png,image/webp,application/pdf'})}
    def clean_receipt(self):
        receipt=self.cleaned_data.get('receipt')
        if receipt:
            validator=DocumentForm({'kind':'other'},{'file':receipt})
            if not validator.is_valid(): raise forms.ValidationError(validator.errors.get('file',['Не вдалося перевірити файл.']))
            receipt.seek(0)
        return receipt
    def clean(self):
        data=super().clean()
        if data.get('litres') and data.get('category')!='fuel': self.add_error('litres','Літри вказуються лише для пального.')
        return data


@login_required
@require_http_methods(['GET','POST'])
def delay(request,pk):
    trip=own_trip(request,pk)
    if trip.status not in Trip.ACTIVE: raise PermissionDenied
    form=DelayForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            trip=get_object_or_404(Trip.objects.select_for_update().filter(driver=request.user,status__in=Trip.ACTIVE),pk=pk)
            record=form.save(commit=False);record.trip=trip;record.driver=request.user;record.save()
            detail=record.get_reason_display()+(f' · {record.minutes} хв' if record.minutes else '')+(f' · {record.comment}' if record.comment else '')
            AuditEvent.objects.create(trip=trip,actor=request.user,action='Водій повідомив про затримку',detail=detail)
            permissions=ModeratorPermissionSettings.objects.filter(pk=1).first()
            roles=['admin']
            if permissions is None or (permissions.view_documents and permissions.view_trips): roles.append('moderator')
            DelayNotification.objects.bulk_create([DelayNotification(recipient_id=user_id,delay=record) for user_id in User.objects.filter(is_active=True,role__in=roles).values_list('pk',flat=True)])
            from .push import enqueue_staff
            enqueue_staff('staff_delay',trip)
        messages.success(request,'Логістів повідомлено про затримку.')
        return redirect('trip_detail',pk=trip.pk)
    return render(request,'fleet/driver_operation_form.html',{'trip':trip,'form':form,'title':'Повідомити про затримку','submit_label':'Надіслати логістам','description':'Оберіть причину та, за бажанням, вкажіть приблизний час.'})


@login_required
@require_http_methods(['GET','POST'])
def expense(request,pk):
    trip=own_trip(request,pk)
    if trip.status not in Trip.ACTIVE+['completed']: raise PermissionDenied
    form=ExpenseForm(request.POST or None,request.FILES or None)
    if request.method=='POST' and form.is_valid():
        record=form.save(commit=False);record.trip=trip;record.driver=request.user
        try:
            with transaction.atomic():
                record.trip=get_object_or_404(Trip.objects.select_for_update().filter(driver=request.user,status__in=Trip.ACTIVE+['completed']),pk=pk)
                record.save()
                detail=f'{record.get_category_display()} · {record.amount} {record.currency}'+(f' · {record.litres} л' if record.litres else '')+(f' · {record.comment}' if record.comment else '')
                AuditEvent.objects.create(trip=trip,actor=request.user,action='Додано витрату',detail=detail)
        except Exception:
            if record.receipt and record.receipt._committed: record.receipt.delete(save=False)
            raise
        messages.success(request,'Витрату збережено.')
        return redirect('trip_detail',pk=trip.pk)
    return render(request,'fleet/driver_operation_form.html',{'trip':trip,'form':form,'title':'Додати витрату','submit_label':'Зберегти витрату','description':'Вкажіть суму, валюту та за потреби додайте чек.'})


@login_required
@require_GET
def receipt(request,pk):
    from .views import trips_for
    expense=get_object_or_404(TripExpense.objects.filter(trip__in=trips_for(request.user)),pk=pk)
    if not expense.receipt: raise Http404
    try: return FileResponse(expense.receipt.open('rb'),as_attachment=True,filename=expense.receipt.name.rsplit('/',1)[-1])
    except FileNotFoundError: raise Http404
