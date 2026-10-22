#!/usr/bin/env python3
"""Create local/server configuration without printing or committing secrets."""
import argparse, os, secrets
from pathlib import Path
parser=argparse.ArgumentParser()
group=parser.add_mutually_exclusive_group(required=True)
group.add_argument("--local",action="store_true")
group.add_argument("--domain",help="Public HTTPS domain, e.g. fleet.company.ua")
args=parser.parse_args()
path=Path(__file__).resolve().parent.parent/".env"
if path.exists(): parser.error(".env already exists; keep it and edit manually. No files changed.")
if args.domain and ("/" in args.domain or ":" in args.domain or " " in args.domain): parser.error("Use a hostname without scheme, slash or port.")
local=args.local
hosts="localhost,127.0.0.1"+("" if local else ","+args.domain)
origins="http://localhost:8000,http://127.0.0.1:8000" if local else "https://"+args.domain
content=f"""DJANGO_SECRET_KEY={secrets.token_urlsafe(64)}
POSTGRES_PASSWORD={secrets.token_urlsafe(36)}
POSTGRES_DB=makosh
POSTGRES_USER=makosh
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS={hosts}
DJANGO_CSRF_TRUSTED_ORIGINS={origins}
DJANGO_SECURE_COOKIES={0 if local else 1}
DJANGO_HTTPS_REDIRECT={0 if local else 1}
DJANGO_TRUST_PROXY={0 if local else 1}
DJANGO_HSTS_SECONDS={0 if local else 3600}
WEB_PORT=8000
"""
fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,"w") as stream: stream.write(content)
print("Created .env. Secrets were not printed. Mode:","localhost test" if local else "company server HTTPS")
