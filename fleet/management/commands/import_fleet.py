import csv
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from fleet.models import User, Vehicle, normalize_phone
from fleet.assignment_history import record_assignment
from django.utils import timezone
class Command(BaseCommand):
    help="Import a private UTF-8 CSV roster. New accounts have no usable password; admin sets one in the app."
    def add_arguments(self,parser): parser.add_argument("file")
    @transaction.atomic
    def handle(self,*args,**options):
        with open(options["file"],encoding="utf-8-sig",newline="") as stream:
            rows=list(csv.DictReader(stream))
        seen=set()
        for line,row in enumerate(rows,2):
            try:
                phone=normalize_phone(row["phone"])
                if phone in seen: raise ValueError("Duplicate phone")
                seen.add(phone)
                if not row.get("full_name","").strip(): raise ValueError("Missing name")
                user,created=User.objects.get_or_create(phone=phone,defaults={"full_name":row["full_name"].strip(),"role":"driver","must_change_password":True})
                if user.role!="driver": raise ValueError("Phone belongs to non-driver account")
                if created: user.set_unusable_password();user.save()
                else: user.full_name=row["full_name"].strip();user.save(update_fields=["full_name"])
                if row.get("vehicle_plate"):
                    if row.get("kind") not in ("tent","grain") or not row.get("trailer_plate"): raise ValueError("Missing vehicle kind or trailer")
                    existing = Vehicle.objects.select_for_update().filter(plate=row["vehicle_plate"].strip()).first()
                    previous_driver_id = existing.default_driver_id if existing else None
                    if existing and (existing.trips.exists() or existing.assignments.exists()) and (existing.trailer_plate != row['trailer_plate'].strip() or existing.kind != row['kind']):
                        raise ValueError('Vehicle history exists; do not overwrite trailer or kind')
                    vehicle, _ = Vehicle.objects.update_or_create(plate=row["vehicle_plate"].strip(),defaults={"trailer_plate":row["trailer_plate"].strip(),"kind":row["kind"],"default_driver":user,"brand":"SCANIA"})
                    User.objects.select_for_update().get(pk=user.pk)
                    record_assignment(vehicle, previous_driver_id, timezone.now(), note='Імпорт списку автопарку')
            except Exception as exc:
                raise CommandError(f"Import aborted at CSV line {line}: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"Imported {len(rows)} drivers. Set initial passwords in Accounts. No passwords were generated or exported."))
