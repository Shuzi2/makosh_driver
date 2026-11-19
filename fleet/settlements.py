"""Local settlement suggestions from the dispatcher-supplied KATOTTG workbook."""
import gzip
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def normalize(value):
    value=unicodedata.normalize('NFKC',value).casefold()
    value=re.sub("[’'ʼ`\u2018]",'',value)
    return ' '.join(re.sub(r'[^\w]+',' ',value).split())


@lru_cache(maxsize=1)
def catalogue():
    with gzip.open(Path(__file__).parent/'data'/'settlements.json.gz','rt',encoding='utf-8') as stream:
        source=json.load(stream)
    records=[]
    for item in source['items']:
        region=(item['region']+' область') if item['region'] not in ['Київ','Севастополь','Автономна Республіка Крим'] else ('м. '+item['region'] if item['region'] in ['Київ','Севастополь'] else item['region'])
        district=item['district']+' район' if item['district'] else ''
        community=item['community']+' громада' if item['community'] else ''
        title={'місто':'м.','село':'с.','селище':'с-ще'}[item['kind']]+' '+item['name']
        parts=[title,community,district]
        if item['name']!=item['region']: parts.append(region)
        record={**item,'region':region,'district':district,'community':community,'title':title,'address':', '.join(p for p in parts if p)}
        records.append((record,normalize(item['name']),normalize(' '.join([item['code'],title,region,district,community]))))
    return records


@lru_cache(maxsize=1)
def lookup():
    by_code={};by_name={}
    for record,name,text in catalogue():
        by_code[record['code']]=record
        by_name.setdefault(name,[]).append(record)
    return by_code,by_name


def resolve_address(address):
    first=address.split(',')[0].strip()
    name=normalize(re.sub(r'^(?:м\.|с\.|с-ще|місто\s|село\s|селище\s)\s*','',first,flags=re.I))
    matches=lookup()[1].get(name,[])
    if len(matches)==1: return matches[0]
    exact=[item for item in matches if address.startswith(item['address'])]
    return exact[0] if len(exact)==1 else None


@require_GET
def search(request):
    query=normalize(request.GET.get('q','')[:100])
    if len(query)<2: return JsonResponse({'results':[],'more':False})
    tokens=query.split()
    matches=[(item,name) for item,name,text in catalogue() if all(token in text for token in tokens)]
    matches.sort(key=lambda pair:(pair[1]!=query,not pair[1].startswith(tokens[0]),pair[1],pair[0]['region'],pair[0]['district'],pair[0]['community'],pair[0]['code']))
    response=JsonResponse({'results':[item for item,name in matches[:20]],'more':len(matches)>20})
    response['Cache-Control']='private, max-age=300'
    return response


@login_required
@require_GET
def driver_search(request):
    if request.user.role != 'driver': raise PermissionDenied
    return search(request)
