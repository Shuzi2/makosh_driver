import base64
import hashlib
from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from email.utils import formataddr
from cryptography.fernet import Fernet

def cipher():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(('makosh-smtp-v1:' + settings.SECRET_KEY).encode()).digest()))

def send_mail(config, recipient, subject, text):
    if not config.enabled:
        raise ValueError('Пошту не налаштовано')
    password = cipher().decrypt(config.password_cipher.encode()).decode() if config.password_cipher else ''
    connection = get_connection(host=config.host, port=config.port, username=config.username, password=password, use_tls=config.security == 'starttls', use_ssl=config.security == 'tls', timeout=15, fail_silently=False)
    EmailMessage(subject, text, formataddr((config.from_name, config.from_email)), [recipient], connection=connection).send(fail_silently=False)
