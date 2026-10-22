from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from fleet.models import LoginThrottle
class Command(BaseCommand):
    def handle(self,*args,**kwargs):
        count,_=LoginThrottle.objects.filter(window_start__lt=timezone.now()-timedelta(days=1)).delete()
        self.stdout.write(str(count))
