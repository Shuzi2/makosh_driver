"""Explicit point selection; search results are suggestions, never saved automatically."""
import hashlib
import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode, quote, urlsplit
from urllib.request import Request, urlopen
from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET
from .models import MapSearchState, MapSearchCache

def is_google_maps_link(value):
    if not value or any(ord(char)<32 for char in value) or '\\' in value: return False
    try:
        parsed=urlsplit(value)
        if parsed.scheme!='https' or parsed.username is not None or parsed.password is not None or parsed.port not in (None,443): return False
        host=(parsed.hostname or '').lower()
        if host=='maps.app.goo.gl': return bool(parsed.path.strip('/'))
        if host=='goo.gl': return parsed.path.startswith('/maps/')
        if host in ('maps.google.com','maps.google.com.ua'): return True
        return host in ('google.com','www.google.com','google.com.ua','www.google.com.ua') and (parsed.path=='/maps' or parsed.path.startswith('/maps/'))
    except ValueError:
        return False

def directions_url(address,lat,lon):
    if lat is None or lon is None:
        return 'https://www.google.com/maps/search/?'+urlencode({'api':'1','query':address},quote_via=quote)
    destination=f'{lat},{lon}' if lat is not None and lon is not None else address
    return 'https://www.google.com/maps/dir/?'+urlencode({'api':'1','destination':destination,'travelmode':'driving','dir_action':'navigate'},quote_via=quote)

def validate_points(form,data):
    reference=getattr(form,'location_reference',None) or (form.instance if form.instance.pk else None)
    for stop in ['origin','destination']:
        lat,lon=f'{stop}_lat',f'{stop}_lon'
        if lat not in form.fields or lon not in form.fields: continue
        a,b=data.get(lat),data.get(lon)
        if (a is None)!=(b is None):
            form.add_error(lat if a is None else lon,'Укажіть обидві координати або залиште обидва поля порожніми.')
        if reference and stop in data and data[stop]!=getattr(reference,stop) and a is not None and b is not None and a==getattr(reference,lat) and b==getattr(reference,lon) and not data.get(stop+'_point_confirmed'):
            form.add_error(lat,'Адресу змінено. Перевірте точку на карті й підтвердьте її для нової адреси.')

@require_GET
def search(request):
    q=' '.join(request.GET.get('q','').split())
    if not 3<=len(q)<=250: return JsonResponse({'error':'Укажіть населений пункт, область і адресу (від 3 до 250 символів).'},status=400)
    endpoint=settings.MAP_GEOCODER_URL
    key=hashlib.sha256((endpoint+'\n'+q.casefold()).encode()).hexdigest()
    cached=MapSearchCache.objects.filter(key=key,cached_at__gte=timezone.now()-timedelta(days=7)).first()
    if cached: return JsonResponse({'results':cached.results})
    # One database lock serializes requests across web workers and users.
    with transaction.atomic():
        state=MapSearchState.objects.select_for_update().get(pk=1)
        cached=MapSearchCache.objects.filter(key=key,cached_at__gte=timezone.now()-timedelta(days=7)).first()
        if cached: return JsonResponse({'results':cached.results})
        now=timezone.now()
        if state.last_requested_at and (now-state.last_requested_at).total_seconds()<1:
            response=JsonResponse({'error':'Зачекайте секунду та повторіть пошук.'},status=429);response['Retry-After']='1';return response
        state.last_requested_at=now;state.save(update_fields=['last_requested_at'])
        params=urlencode({'q':q,'format':'jsonv2','limit':5,'addressdetails':1,'accept-language':'uk','viewbox':'22,53,41,44','bounded':0})
        try:
            req=Request(endpoint+'?'+params,headers={'User-Agent':'MakoshFleet/1.0 (+https://makosh-group.com.ua)','Accept':'application/json'})
            with urlopen(req,timeout=6) as response:
                payload=response.read(256*1024+1)
            if len(payload)>256*1024: raise ValueError('oversized')
            raw=json.loads(payload)
            if not isinstance(raw,list): raise ValueError('invalid')
            results=[]
            for item in raw[:5]:
                lat,lon=Decimal(str(item['lat'])),Decimal(str(item['lon']))
                if not lat.is_finite() or not lon.is_finite() or not -90<=lat<=90 or not -180<=lon<=180: continue
                results.append({'lat':str(lat),'lon':str(lon),'label':str(item['display_name'])[:600]})
        except (OSError,ValueError,KeyError,TypeError,InvalidOperation):
            return JsonResponse({'error':'Пошук карти зараз недоступний. Можна вибрати точку вручну або ввести координати.'},status=503)
        MapSearchCache.objects.update_or_create(key=key,defaults={'results':results,'cached_at':timezone.now()})
    return JsonResponse({'results':results})
