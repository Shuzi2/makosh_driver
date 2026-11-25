"""Export a driver's report as a read-only XLSX snapshot."""
from decimal import Decimal
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape
import re
from django.http import HttpResponse, Http404
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET
from django.utils import timezone
from .models import User, DriverReportHistory
from .driver_reports import guard, report_context, PeriodForm, RESULT_FIELDS, archive_effective
from .management.commands.import_driver_reports import KEYS, number

HEADERS=['№ / рейс','Дата','Кілометраж, км','Ставка водія, грн/день','Кількість днів','Бонус водія, грн/км','Ціна AdBlue, грн/л','Ціна пального, грн/л','Витрата пального, л/100 км','Витрата AdBlue, л/100 км','Амортизація, грн/км','Кредит, грн/км','Ціна рейсу, грн','Додаткові витрати, грн','Переїзд','Точка завантаження','Точка розвантаження','Маршрут','Витрати на пальне, грн','Витрати на AdBlue, грн','Амортизація і кредит, грн','Оплата водія, грн','Загальні витрати, грн','Прибуток, грн','Собівартість, грн/км','Прибуток, грн/км','Маржа, %','Пальне, л · AB','Примітки']
TEXT_KEYS={'date','empty_route','loading','unloading','route'}

def numeric(value):
    result=number(value)
    return result if result is not None and result.is_finite() else value

def archive_row(row):
    raw=archive_effective(row)
    values=[]
    for key in KEYS:
        display=row.display.get(key)
        value=display if key in TEXT_KEYS else raw.get(key)
        if key not in TEXT_KEYS and value not in (None,''):
            value=numeric(value)
        values.append(value)
    notes=list(row.anomalies)
    if row.report_corrections: notes.append('Дані виправлено у звіті; початкові значення збережено')
    return [row.source_number,*values,numeric(row.extra_ab) if row.extra_ab not in (None,'') else None,'; '.join(notes)]

def live_row(trip):
    fields=trip.economics_form
    def field(key):
        value=fields[key].value()
        return numeric(value) if value not in (None,'') else None
    metrics=trip.report_finances
    note=[]
    if trip.report_economics is None: note.append('Попередні значення з поточних налаштувань, ставки не збережені')
    if metrics['other_uah']:
        suffix='перевірте ручне значення загальних витрат' if 'total' in trip.report_manual else 'враховується у формулі загальних витрат'
        note.append(f"Додатково витрати водія без пального: {metrics['other_uah']} грн; {suffix}")
    if metrics['foreign']: note.append('Для витрат в іноземній валюті потрібна сума у гривнях: загальна сума не розрахована')
    date=trip.report_fields['date']
    values=[date,trip.report_kilometers,field('daily_rate'),field('days'),field('bonus_per_km'),field('adblue_price'),field('fuel_price'),metrics.get('fuel_rate'),field('adblue_rate'),field('depreciation_per_km'),field('credit_per_km'),field('price'),field('extra_cost'),trip.report_fields['empty_route'],trip.report_fields['loading'],trip.report_fields['unloading'],trip.report_fields['route'],*[metrics.get(k) for k in ('fuel','adblue','depreciation','wage','total','profit','cost_per_km','profit_per_km','margin')]]
    if trip.report_fuel_manual != '': note.append('Витрата пального виправлена лише у звіті')
    return [trip.report_fields['report_number'],*values,metrics.get('report_fuel_litres',trip.fuel_used_l),'; '.join(note)]

def col(index):
    result=''
    while index:
        index,remainder=divmod(index-1,26); result=chr(65+remainder)+result
    return result

def cell(value,ref,style=0):
    if value is None or value=='': return ''
    if isinstance(value,(int,float,Decimal)) and not isinstance(value,bool):
        return f'<c r="{ref}" s="{style or 2}"><v>{value}</v></c>'
    text=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',str(value))[:32767]
    return f'<c r="{ref}" t="inlineStr" s="{style}"><is><t xml:space="preserve">{escape(text)}</t></is></c>'

def worksheet(rows, driver_name):
    records=[HEADERS + [f'Водій: {driver_name}'], *[row + [driver_name] for row in rows]]
    data=''.join(f'<row r="{i}">'+''.join(cell(value,f'{col(j)}{i}',1 if i==1 else 0) for j,value in enumerate(row,1))+'</row>' for i,row in enumerate(records,1))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
      '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
      '<sheetViews><sheetView workbookViewId="0"><pane xSplit="2" ySplit="1" topLeftCell="C2" activePane="bottomRight" state="frozen"/></sheetView></sheetViews>'
      '<cols><col min="1" max="1" width="14" customWidth="1"/><col min="2" max="14" width="19" customWidth="1"/><col min="15" max="18" width="36" customWidth="1"/><col min="19" max="28" width="20" customWidth="1"/><col min="29" max="29" width="55" customWidth="1"/><col min="30" max="30" width="28" customWidth="1"/></cols>'
      f'<sheetData>{data}</sheetData><autoFilter ref="A1:AD{len(records)}"/></worksheet>')

def workbook_bytes(site_rows,archive_rows,driver_name):
    content=BytesIO()
    with ZipFile(content,'w',ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','''<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>''')
        z.writestr('_rels/.rels','''<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''')
        z.writestr('xl/workbook.xml','''<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Рейси сайту" sheetId="1" r:id="rId1"/><sheet name="Архів таблиці" sheetId="2" r:id="rId2"/></sheets></workbook>''')
        z.writestr('xl/_rels/workbook.xml.rels','''<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>''')
        z.writestr('xl/styles.xml','''<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFB84110"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/><xf numFmtId="2" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>''')
        z.writestr('xl/worksheets/sheet1.xml',worksheet(site_rows,driver_name))
        z.writestr('xl/worksheets/sheet2.xml',worksheet(archive_rows,driver_name))
    return content.getvalue()

@login_required
@require_GET
def export_driver(request,pk=None,label=None):
    guard(request)
    driver=get_object_or_404(User,pk=pk,role='driver') if pk is not None else None
    if driver is None and not DriverReportHistory.objects.filter(driver__isnull=True,driver_label=label).exists(): raise Http404
    period=PeriodForm(request.GET or None)
    if period.is_bound and not period.is_valid(): return HttpResponse('Некоректний період.',status=400)
    context=report_context(driver=driver,label=label,period=period,query=request.GET.get('q','').strip()[:150])
    content=workbook_bytes([live_row(trip) for trip in context['trips']],[archive_row(row) for row in context['historical']],context['name'])
    response=HttpResponse(content,content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition']=f'attachment; filename="driver-report-{pk or "archive"}.xlsx"'
    response['Cache-Control']='private, no-store'
    return response
