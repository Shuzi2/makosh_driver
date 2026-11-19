"""Import a reviewed, read-only snapshot without modifying or inventing operational trips."""
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from fleet.models import DriverReportHistory,User

KEYS=['date','kilometers','daily_rate','days','bonus_per_km','adblue_price','fuel_price','fuel_rate','adblue_rate','depreciation_per_km','credit_per_km','revenue','extra_cost','empty_route','loading','unloading','route','fuel_cost','adblue_cost','depreciation_cost','driver_cost','expenses','profit','cost_per_km','profit_per_km','margin']

def number(value):
    if value is None or isinstance(value,bool): return None
    try: return Decimal(str(value).replace('\u00a0','').replace(' ','').replace(',','.'))
    except (InvalidOperation,ValueError): return None

def parsed_date(value):
    if isinstance(value,(int,float)) and not isinstance(value,bool) and 40000<=value<=60000:
        return date(1899,12,30)+timedelta(days=int(value))
    if isinstance(value,str):
        for fmt in ('%d.%m.%Y','%Y-%m-%d','%d/%m/%Y'):
            try: return datetime.strptime(value.strip(),fmt).date()
            except ValueError: pass
    return None

def parse_sheet(sheet,formula_sheet,display_sheet,drivers):
    name=sheet['name']; rows=sheet['values']; formulas=formula_sheet.get('values',[]); displayed=display_sheet.get('values',[])
    offset=0 if name=='Базюнь підмінний Зерновоз' else 1
    surname=name.split()[0].split('(')[0]
    candidates=[u for u in drivers if u.full_name.split() and u.full_name.split()[0].casefold()==surname.casefold()]
    driver=candidates[0] if len(candidates)==1 else None
    label=driver.full_name if driver else surname
    kind='grain' if 'зерновоз' in name.casefold() else 'tent' if 'тент' in name.casefold() else ''
    result=[]
    for index,row in enumerate(rows):
        if len(row)<=offset: continue
        when=parsed_date(row[offset])
        values={key:(row[offset+i] if offset+i<len(row) else None) for i,key in enumerate(KEYS)}
        if not when and not (number(values.get('kilometers')) is not None and any(isinstance(values.get(k),str) and values[k].strip() for k in ('loading','unloading'))): continue
        display_row=displayed[index] if index<len(displayed) else []
        display_values={key:(display_row[offset+i] if offset+i<len(display_row) else None) for i,key in enumerate(KEYS)}
        extras=[]
        for col in range(offset+len(KEYS),len(display_row)):
            value=display_row[col]
            if value not in (None,''):
                letter=''
                n=col+1
                while n: n,rem=divmod(n-1,26);letter=chr(65+rem)+letter
                extras.append({'column':letter,'value':value})
        display_values['extra_columns']=extras
        if all(values.get(k) in (None,'') for k in ('kilometers','revenue','empty_route','loading','unloading')): continue
        issues=[] if when else ['Дата рейсу не вказана або не розпізнана.']
        if when and when > timezone.localdate():
            issues.append('Дата у майбутньому на момент перевірки імпорту.')
        if number(values.get('kilometers')) is None:
            issues.append('У вихідному рядку не вказано пробіг.')
        revenue,expenses,profit=map(number,(values['revenue'],values['expenses'],values['profit']))
        if all(x is not None for x in (revenue,expenses,profit)) and abs(revenue-expenses-profit)>Decimal('1'):
            issues.append('Прибуток у таблиці не збігається з різницею ціни рейсу та витрат.')
        km,rate,price,cost=map(number,(values['kilometers'],values['fuel_rate'],values['fuel_price'],values['fuel_cost']))
        if all(x is not None for x in (km,rate,price,cost)) and abs(km*rate*price/100-cost)>Decimal('1'):
            issues.append('Витрати на пальне у таблиці не збігаються з розрахунком за значеннями цього рядка.')
        formula_row=formulas[index] if index<len(formulas) else []
        fuel_formula=formula_row[offset+17] if len(formula_row)>offset+17 else ''
        fuel_column='G' if offset==0 else 'H'
        if isinstance(fuel_formula,str) and fuel_formula.startswith('=') and any(column==fuel_column and int(refrow)!=index+1 for column,refrow in re.findall(r'\$?([A-Z]{1,2})\$?(\d+)',fuel_formula.upper())):
            issues.append('Формула пального посилається на ціну з іншого рядка таблиці.')
        result.append(DriverReportHistory(source_sheet=name,source_row=index+1,driver=driver,driver_label=label,vehicle_kind=kind,trip_date=when,source_values=values,display_values=display_values,source_raw=row,source_formulas=formula_row,anomalies=issues))
    return result

class Command(BaseCommand):
    help='Import an immutable Google Sheets snapshot; existing rows are never replaced.'
    def add_arguments(self,parser):
        parser.add_argument('snapshot');parser.add_argument('formulas');parser.add_argument('displayed');parser.add_argument('--apply',action='store_true')
    def handle(self,*args,**opts):
        with open(opts['snapshot'],encoding='utf-8') as f: source=json.load(f)
        with open(opts['formulas'],encoding='utf-8') as f: formula=json.load(f)
        with open(opts['displayed'],encoding='utf-8') as f: displayed=json.load(f)
        if source.get('spreadsheet')!='1Y420d-f6QSaQdzGYKmoBE1BSQXe1MXWf5ddDxSZzVdc': raise CommandError('Unexpected source')
        formulas={x['name']:x for x in formula['sheets']};displays={x['name']:x for x in displayed['sheets']};drivers=list(User.objects.filter(role='driver'))
        objects=[]
        for sheet in source['sheets']:
            if sheet['name'] not in formulas or sheet['name'] not in displays: raise CommandError('Missing formula or displayed snapshot')
            parsed=parse_sheet(sheet,formulas[sheet['name']],displays[sheet['name']],drivers);objects+=parsed
            self.stdout.write(f'{sheet["name"]}: {len(parsed)} rows; unlinked: {sum(o.driver_id is None for o in parsed)}; discrepancies: {sum(bool(o.anomalies) for o in parsed)}')
        if not opts['apply']:
            self.stdout.write(f'DRY RUN: {len(objects)} rows, no changes');return
        with transaction.atomic():
            existing={(x.source_sheet,x.source_row):x for x in DriverReportHistory.objects.all()}
            conflicts=[o for o in objects if (o.source_sheet,o.source_row) in existing and existing[(o.source_sheet,o.source_row)].source_raw!=o.source_raw]
            if conflicts: raise CommandError(f'{len(conflicts)} source rows changed; import stopped for review')
            new=[o for o in objects if (o.source_sheet,o.source_row) not in existing]
            DriverReportHistory.objects.bulk_create(new,batch_size=200)
        self.stdout.write(f'Imported {len(new)} rows; {len(objects)-len(new)} already present.')
