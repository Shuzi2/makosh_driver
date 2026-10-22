# Fleet application

A self-hosted fleet management application with phone/password sign-in, role-based access, trips, document uploads and CSV reports.

## Development

Requirements: Docker Engine or Docker Desktop with Docker Compose v2; Python 3 for configuration generation.

```sh
python3 scripts/init_env.py --local
docker compose up --build -d
docker compose exec web python manage.py createsuperuser
```

Open http://localhost:8000.

```sh
docker compose exec web python manage.py test --verbosity 2
```

The `--local` configuration is for localhost testing. Company-server configuration and operational instructions are distributed separately. Do not commit credentials, real contact lists, uploaded documents or database files.

`examples/roster.csv` contains synthetic sample data only. The real initial roster is provided separately.

## Stack

Python 3.12, Django 5.2, PostgreSQL 17, Gunicorn and Docker Compose. The GitHub Actions workflow runs application tests and container checks.
