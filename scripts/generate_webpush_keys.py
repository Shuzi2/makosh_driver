"""Print VAPID keys once; redirect stdout to a private environment file."""
import base64
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

key = ec.generate_private_key(ec.SECP256R1())
encode = lambda value: base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')
print('WEBPUSH_PUBLIC_KEY=' + encode(key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)))
print('WEBPUSH_PRIVATE_KEY=' + encode(key.private_numbers().private_value.to_bytes(32, 'big')))
