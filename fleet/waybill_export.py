"""Excel export of filtered waybills and their individual route legs."""
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
from django.http import HttpResponse
from django.utils import timezone
from .driver_report_export import workbook_bytes, cell, col
from .waybills import waybill_data

FIELDS=[('number','№ шляхового'),('date','Дата шляхового'),('organisation','Організація'),('organisation_address','Адреса організації'),('brand','Марка автомобіля'),('plates','Авто / причіп'),('driver_name','Водій'),('recipient','В розпорядження'),('dispatch_address','Адреса подачі'),('notes','Відмітки'),('driver_received','Прийняв водій'),('driver_returned','Автомобіль здав водій'),('odometer_start','Спідометр на початку, км'),('odometer_end','Спідометр при поверненні, км'),('fuel_start','Пальне до початку, л'),('fuel_issued','Видано пального, л'),('fuel_ticket','№ заправного листа'),('fuel_remaining','Залишок пального, л'),('fuel_norm','Витрата за нормою, л'),('fuel_actual','Витрата фактична, л'),('fuel_economy','Економія, л'),('fuel_overuse','Перевитрата, л'),('distance_total','Загальний пробіг, км'),('authorised_by','Виїзд дозволив'),('director','Директор'),('received_by','Автомобіль прийняв'),('signature','Підпис')]
NUMERIC={'odometer_start','odometer_end','fuel_start','fuel_issued','fuel_remaining','fuel_norm','fuel_actual','fuel_economy','fuel_overuse','distance_total'}

def sheet(headers,rows):
    records=[headers,*rows]
    def xml_cell(value,ref,header):
        if not header and isinstance(value,(int,Decimal)) and not isinstance(value,bool): return f'<c r="{ref}"><v>{value}</v></c>'
        return cell(value,ref,1 if header else 0)
    data=''.join(f'<row r="{i}">'+''.join(xml_cell(value,f'{col(j)}{i}',i==1) for j,value in enumerate(row,1))+'</row>' for i,row in enumerate(records,1))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f'<cols><col min="1" max="{len(headers)}" width="23" customWidth="1"/></cols><sheetData>{data}</sheetData><autoFilter ref="A1:{col(len(headers))}{len(records)}"/></worksheet>')

def export_waybills(trips):
    main=[];legs=[]
    for trip in trips.select_related('vehicle','driver').prefetch_related('stops'):
        info=waybill_data(trip);data=info['data']
        values=[]
        for key,_ in FIELDS:
            value=data.get(key)
            if key in NUMERIC and value not in (None,''):
                try: value=Decimal(str(value))
                except InvalidOperation: value=None
            if isinstance(value,date): value=value.isoformat()
            values.append(value)
        main.append([trip.pk,timezone.localtime(trip.completed_at).strftime('%d.%m.%Y %H:%M') if trip.completed_at else '',*values])
        for index,leg in enumerate(info['legs'],1):
            legs.append([trip.pk,data['number'],index,leg.get('origin'),leg.get('destination'),leg.get('distance'),leg.get('label'),leg.get('unloaded_tons')])
    output=BytesIO()
    with ZipFile(BytesIO(workbook_bytes([],[],''))) as source, ZipFile(output,'w',ZIP_DEFLATED) as target:
        for entry in source.infolist():
            content=source.read(entry.filename)
            if entry.filename=='xl/workbook.xml': content=content.replace('Рейси сайту'.encode(),'Шляхові листи'.encode()).replace('Архів таблиці'.encode(),'Ділянки маршруту'.encode())
            elif entry.filename=='xl/worksheets/sheet1.xml': content=sheet(['№ рейсу','Завершено',*[label for _,label in FIELDS]],main).encode()
            elif entry.filename=='xl/worksheets/sheet2.xml': content=sheet(['№ рейсу','№ шляхового','№ ділянки','Звідки','Куди','Пробіг, км','Вид переїзду','Фактично розвантажено, т'],legs).encode()
            target.writestr(entry,content)
    response=HttpResponse(output.getvalue(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition']='attachment; filename="waybills.xlsx"'
    response['Cache-Control']='private, no-store'
    return response
