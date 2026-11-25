"""Text for staff notifications about driver-uploaded consignment notes."""

def describe(doc):
    signed=doc.kind=='signed_ttn'
    label='Підписана ТТН розвантаження' if signed else 'ТТН із завантаження'
    action='Розвантаження' if signed else 'Завантаження'
    if doc.stop_id:
        place=doc.stop.address
        short=doc.stop.driver_place['name']
        point=f'{action} · точка {doc.stop.position}'
    else:
        place=doc.trip.destination if signed else doc.trip.origin
        short=place
        point=action
    full_point=f'{point}: {place}' if place else point
    short_point=f'{point}: {short}' if short else point
    number=f' · ТТН №{doc.ttn_number}' if doc.ttn_number else ''
    message=f'{label} · {doc.uploaded_by.full_name} · {short_point}{number}'
    return {'label':label,'location':full_point,'message':message}
