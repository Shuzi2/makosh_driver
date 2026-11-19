import time
import logging
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from fleet.operations import worker_tick
from fleet.queue_promotion import promote_ready

logger=logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Run scheduled backup, restore verification and technical cleanup worker.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            try:
                promote_ready()
            except Exception:
                logger.exception('Не вдалося перевірити чергу рейсів')
            worker_tick()
            if options['once']:
                return
            time.sleep(30)
