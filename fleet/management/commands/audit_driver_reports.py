from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from fleet.models import DriverReportHistory
from fleet.management.commands.import_driver_reports import number

class Command(BaseCommand):
    help='Flag incomplete or future-dated source rows without changing their values.'
    def add_arguments(self,parser): parser.add_argument('--apply',action='store_true')
    def handle(self,*args,**opts):
        today=timezone.localdate();changed=[]
        for row in DriverReportHistory.objects.all().iterator():
            issues=list(row.anomalies)
            if row.trip_date and row.trip_date>today:
                note='Дата у майбутньому на момент перевірки імпорту.'
                if note not in issues: issues.append(note)
            if number(row.source_values.get('kilometers')) is None:
                note='У вихідному рядку не вказано пробіг.'
                if note not in issues: issues.append(note)
            if issues!=row.anomalies:
                row.anomalies=issues;changed.append(row)
        if opts['apply']:
            with transaction.atomic(): DriverReportHistory.objects.bulk_update(changed,['anomalies'],batch_size=200)
        self.stdout.write(f'{"Updated" if opts["apply"] else "Would update"}: {len(changed)} records')
