from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from .attention import issue_snapshot, CATEGORIES
from .models import Trip, AttentionSkip, AuditEvent
from .views import staff_required


@staff_required
@require_GET
def inbox(request):
    issues=issue_snapshot()
    skipped=request.GET.get('tab')=='skipped'
    category=request.GET.get('category','')
    if category not in dict((c[0],c[1]) for c in CATEGORIES): category=''
    query=request.GET.get('q','').strip()[:180]
    active=[i for i in issues if not i['skip']]
    archived=[i for i in issues if i['skip']]
    selected=archived if skipped else active
    groups=[{'key':key,'title':title,'count':sum(i['key']==key for i in selected)} for key,title,*_ in CATEGORIES]
    rows=[i for i in selected if not category or i['key']==category]
    if query:
        rows=[i for i in rows if query.casefold() in ' '.join(str(v) for v in [i['trip'].pk,i['trip'].origin,i['trip'].destination,i['trip'].driver.full_name,i['trip'].vehicle.plate,i['trip'].vehicle.trailer_plate,i['stop'].address if i['stop'] else '']).casefold()]
    page=Paginator(rows,18).get_page(request.GET.get('page'))
    params=request.GET.copy()
    params.pop('page',None); params.pop('live',None)
    context={'issue_page':page,'categories':groups,'category':category,'query':query,'skipped':skipped,'active_count':len(active),'skipped_count':len(archived),'affected_count':len({i['trip'].pk for i in active}),'result_count':len(rows),'query_string':params.urlencode(),'return_query':params.urlencode(),'attention_nav_count':len(active)}
    # Reuse the snapshot for the sidebar instead of querying the same issues twice.
    request.attention_nav_count=len(active)
    if request.GET.get('live')=='1':
        from .models import DriverServiceRequest
        response=JsonResponse({'html':render_to_string('fleet/attention_content.html',context,request=request),'count':len(active),'service_count':DriverServiceRequest.objects.filter(status='new').count()})
        response['Cache-Control']='no-store'
        return response
    return render(request,'fleet/attention.html',context)


@staff_required
@require_GET
def count(request):
    from .models import DriverServiceRequest
    response=JsonResponse({'count':sum(not i['skip'] for i in issue_snapshot()),'service_count':DriverServiceRequest.objects.filter(status='new').count()})
    response['Cache-Control']='no-store'
    return response


@staff_required
@require_POST
def action(request,pk,key):
    mode=request.POST.get('action')
    if mode not in ['skip','restore'] or key not in [c[0] for c in CATEGORIES]: return HttpResponseBadRequest()
    note=request.POST.get('note','').strip()
    if len(note)>500: return HttpResponseBadRequest('Коментар має містити не більше 500 символів.')
    with transaction.atomic():
        trip=get_object_or_404(Trip.objects.select_for_update(),pk=pk)
        issue=next((i for i in issue_snapshot(Trip.objects.filter(pk=pk)) if i['key']==key and str(i['stop'].pk if i['stop'] else '')==request.POST.get('stop_id','')),None)
        if not issue:
            messages.info(request,'Це питання вже вирішено. Список оновлено.')
        elif issue['episode']!=request.POST.get('episode'):
            messages.warning(request,'Обставини рейсу змінилися. Перегляньте актуальне питання перед дією.')
        elif issue['skip_version']!=request.POST.get('skip_version',''):
            messages.info(request,'Інший логіст уже оновив це питання. Список оновлено.')
        elif mode=='skip' and not issue['skip']:
            AttentionSkip.objects.update_or_create(trip=trip,category=key,episode=issue['episode'],defaults={'stop':issue['stop'],'skipped_by':request.user,'skipped_at':timezone.now(),'note':note,'restored_at':None})
            AuditEvent.objects.create(actor=request.user,trip=trip,action='Пропущено питання уваги',detail=f"{issue['title']}"+(f'. Коментар: {note}' if note else ''))
            messages.success(request,'Питання перенесено до пропущених. За потреби його можна повернути.')
        elif mode=='restore' and issue['skip']:
            AttentionSkip.objects.filter(pk=issue['skip'].pk).update(restored_at=timezone.now())
            AuditEvent.objects.create(actor=request.user,trip=trip,action='Повернуто питання до уваги',detail=issue['title'])
            messages.success(request,'Питання повернуто до активного списку.')
    # Only retain known filters, never accept an arbitrary redirect URL.
    from django.http import QueryDict
    supplied=QueryDict(request.POST.get('return_query',''))
    params=QueryDict(mutable=True)
    for name in ['tab','category','q','page']:
        if supplied.get(name): params[name]=supplied[name][:180]
    return redirect(reverse('attention')+('?' + params.urlencode() if params else ''))
