from django.contrib.auth.decorators import login_required
from django.http import FileResponse,Http404,JsonResponse
from django.shortcuts import get_object_or_404,render
from django.urls import reverse
from django.views.decorators.http import require_GET
from .models import Document


def own_document(request,pk):
    from .views import trips_for
    return get_object_or_404(Document.objects.select_related('trip','stop'),pk=pk,trip__in=trips_for(request.user))


@login_required
@require_GET
def inline(request,pk):
    doc=own_document(request,pk)
    preview_type=doc.preview_content_type
    if preview_type not in ['image/jpeg','image/png','image/webp','application/pdf']: raise Http404
    try:
        if doc.content_type=='application/octet-stream':
            from .image_compat import mpo_preview
            handle=mpo_preview(doc)
        else: handle=doc.file.open('rb')
    except (OSError, ValueError): raise Http404('Не вдалося відкрити фото.')
    response=FileResponse(handle,as_attachment=False,filename=doc.original_name,content_type=preview_type)
    response['Cache-Control']='private, no-store';response['X-Content-Type-Options']='nosniff'
    # Native PDF viewing may be framed only by this same application.
    response['Content-Security-Policy']="default-src 'none'; frame-ancestors 'self'; sandbox"
    response['X-Frame-Options']='SAMEORIGIN'
    return response


@login_required
@require_GET
def preview(request,pk):
    doc=own_document(request,pk)
    docs=Document.objects.filter(trip_id=doc.trip_id).select_related('stop').order_by('stop__position','uploaded_at','pk')
    items=[]
    for item in docs:
        place=item.stop.driver_place['name'] if item.stop_id else ''
        items.append({'id':item.pk,'title':item.get_kind_display(), 'number':item.ttn_number,'place':place,
            'name':item.original_name,'type':item.preview_content_type,'uploaded':item.uploaded_at.isoformat(),
            'url':reverse('document_inline',args=[item.pk]),'download':reverse('document_download',args=[item.pk]),
            'preview':reverse('document_preview',args=[item.pk])})
    if request.GET.get('format')=='json':
        response=JsonResponse({'selected':doc.pk,'trip':doc.trip_id,'documents':items});response['Cache-Control']='private, no-store';return response
    selected=next(item for item in items if item['id']==doc.pk)
    return render(request,'fleet/document_preview.html',{'doc':doc,'items':items,'selected':selected,'documents_screen':request.user.role=='driver'})
