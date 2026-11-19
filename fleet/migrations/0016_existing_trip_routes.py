import hashlib
from django.db import migrations
from django.db.models import Q


def forwards(apps,schema_editor):
    alias=schema_editor.connection.alias
    Trip=apps.get_model('fleet','Trip');Stop=apps.get_model('fleet','TripStop')
    Document=apps.get_model('fleet','Document');Skip=apps.get_model('fleet','AttentionSkip');Event=apps.get_model('fleet','AuditEvent')
    for trip in Trip.objects.using(alias).filter(route_enabled=False).iterator():
        load_state={'loading':'arrived','road':'completed','unloading':'completed','completed':'completed','cancelled':'cancelled'}.get(trip.status,'pending')
        unload_state={'unloading':'arrived','completed':'completed','cancelled':'cancelled'}.get(trip.status,'pending')
        signed=Document.objects.using(alias).filter(trip_id=trip.pk,kind='signed_ttn').exists()
        # Unknown historical arrival/loading times stay unknown; no invented timestamps.
        load=Stop.objects.using(alias).create(trip_id=trip.pk,position=1,kind='load',address=trip.origin,lat=trip.origin_lat,lon=trip.origin_lon,contact_name=trip.loading_contact_name,contact_phone=trip.loading_contact_phone,cargo=trip.cargo,planned_tons=trip.planned_tons,ttn_number=trip.ttn_number,state=load_state)
        unload=Stop.objects.using(alias).create(trip_id=trip.pk,position=2,kind='unload',address=trip.destination,lat=trip.destination_lat,lon=trip.destination_lon,contact_name=trip.contact_name,contact_phone=trip.contact_phone,cargo=trip.cargo,planned_tons=trip.planned_tons,actual_tons=trip.actual_tons,ttn_number=trip.ttn_number,state=unload_state,completed_at=trip.completed_at if trip.status=='completed' else None,signed_complete=signed,original_received=trip.original_received)
        Document.objects.using(alias).filter(trip_id=trip.pk,kind='signed_ttn').update(stop_id=unload.pk,ttn_number=trip.ttn_number)
        Document.objects.using(alias).filter(trip_id=trip.pk).exclude(kind='signed_ttn').update(stop_id=load.pk,ttn_number=trip.ttn_number)
        for key in ['documents','weight']:
            weight_revision=Event.objects.using(alias).filter(trip_id=trip.pk).filter(Q(action='Оновлено фактичну вагу')|Q(detail__startswith='Фактична вага')).order_by('-pk').values_list('pk',flat=True).first()
            old_identity=(trip.completed_at,weight_revision) if key=='weight' else (trip.completed_at,)
            old_episode=hashlib.sha256(repr((trip.pk,key,old_identity)).encode()).hexdigest()
            new_episode=hashlib.sha256(repr((trip.pk,key,(unload.pk,unload.completed_at,0))).encode()).hexdigest()
            Skip.objects.using(alias).filter(trip_id=trip.pk,category=key,episode=old_episode).update(stop_id=unload.pk,episode=new_episode)
        # Preserve updated_at so converting the representation is not a new business event.
        Trip.objects.using(alias).filter(pk=trip.pk).update(route_enabled=True)


class Migration(migrations.Migration):
    dependencies=[('fleet','0015_document_ttn_number_trip_route_enabled_and_more')]
    operations=[migrations.RunPython(forwards,migrations.RunPython.noop)]
