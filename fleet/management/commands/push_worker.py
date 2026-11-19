import logging
import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from fleet.push import process_deliveries

logger=logging.getLogger(__name__)

class Command(BaseCommand):
    help='Send queued driver push notifications.'
    def add_arguments(self,parser):
        parser.add_argument('--once',action='store_true')
    def handle(self,*args,**options):
        while True:
            close_old_connections()
            try: process_deliveries()
            except Exception: logger.exception('Push worker error')
            if options['once']: return
            time.sleep(5)
