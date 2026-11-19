from django import forms
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404,render,redirect
from django.views.decorators.http import require_GET,require_POST
from .models import SavedPlace,AuditEvent
from .route_forms import StopForm

COPY_FIELDS=['address','settlement_code','settlement_name','region','district','community','lat','lon','contact_name','contact_phone','contact_phone_alt','notes']


def remember_stop(stop,actor):
    name=(stop.driver_place['name']+(' · '+stop.contact_name if stop.contact_name else ''))[:150]
    place=SavedPlace.objects.filter(name=name,is_active=True,**{f:getattr(stop,f) for f in COPY_FIELDS}).first()
    if not place:
        place=SavedPlace.objects.create(name=name,created_by=actor,**{f:getattr(stop,f) for f in COPY_FIELDS})
        AuditEvent.objects.create(actor=actor,trip=stop.trip,action='Збережено місце',detail=f'{place.name}: {place.address}')
    return place


class SavedPlaceForm(StopForm):
    revision=forms.DateTimeField(required=False,widget=forms.HiddenInput)
    class Meta(StopForm.Meta):
        model=SavedPlace
        fields=['name','operation']+COPY_FIELDS
        labels={'name':'Назва складу, елеватора чи місця'}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.initial['revision']=self.instance.updated_at.isoformat() if self.instance.pk else ''
        self.original={f:getattr(self.instance,f) for f in COPY_FIELDS} if self.instance.pk else {}
    def clean(self):
        data=super().clean()
        if self.instance.pk and data.get('revision')!=self.instance.updated_at: self.add_error(None,'Місце вже змінилося. Відкрийте актуальні дані.')
        return data


@require_GET
def search(request):
    query=request.GET.get('q','').strip()[:100];kind=request.GET.get('kind')
    qs=SavedPlace.objects.filter(is_active=True)
    if kind in ['load','unload']: qs=qs.filter(operation__in=['both',kind])
    for word in query.split(): qs=qs.filter(Q(name__icontains=word)|Q(address__icontains=word)|Q(region__icontains=word)|Q(community__icontains=word))
    result=[]
    for place in qs[:20]:
        row={f:getattr(place,f) for f in COPY_FIELDS};row.update(id=place.pk,name=place.name)
        for f in ['lat','lon']: row[f]=str(row[f]) if row[f] is not None else ''
        result.append(row)
    response=JsonResponse({'results':result});response['Cache-Control']='private, no-store';return response


def places(request):
    qs=SavedPlace.objects.all();q=request.GET.get('q','').strip()[:100]
    if request.GET.get('archived')!='1': qs=qs.filter(is_active=True)
    if q: qs=qs.filter(Q(name__icontains=q)|Q(address__icontains=q)|Q(contact_name__icontains=q))
    return render(request,'fleet/places.html',{'places':qs[:200],'q':q,'archived':request.GET.get('archived')=='1'})


def edit(request,pk=None):
    with transaction.atomic():
        place=get_object_or_404(SavedPlace.objects.select_for_update(),pk=pk) if pk else SavedPlace()
        form=SavedPlaceForm(request.POST if request.method=='POST' else None,instance=place,prefix='place')
        if request.method=='POST' and form.is_valid():
            saved=form.save(commit=False)
            if not saved.pk: saved.created_by=request.user
            saved.save();AuditEvent.objects.create(actor=request.user,action='Оновлено збережене місце' if pk else 'Створено збережене місце',detail=f'{saved.pk}: {saved.name}, {saved.address}')
            messages.success(request,'Місце збережено. Дані вже створених рейсів залишилися незмінними.')
            return redirect('saved_places')
    return render(request,'fleet/place_edit.html',{'form':form,'place':place})


@require_POST
def toggle(request,pk):
    with transaction.atomic():
        place=get_object_or_404(SavedPlace.objects.select_for_update(),pk=pk)
        target=request.POST.get('active')
        if target not in ['0','1']: return JsonResponse({'error':'Невідома дія'},status=400)
        place.is_active=target=='1';place.save(update_fields=['is_active','updated_at'])
        AuditEvent.objects.create(actor=request.user,action='Повернено місце' if place.is_active else 'Архівовано місце',detail=f'{place.pk}: {place.name}')
    return redirect('saved_places')
