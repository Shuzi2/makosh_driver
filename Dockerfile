FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
COPY --chown=app:app . .
RUN mkdir -p /data/uploads /app/staticfiles && chown -R app:app /data /app/staticfiles
USER app
RUN DJANGO_SECRET_KEY=build-collectstatic-only-no-production-secret-0123456789abcdef USE_SQLITE=1 python manage.py collectstatic --noinput
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--timeout", "90", "--access-logfile", "-", "--error-logfile", "-"]
