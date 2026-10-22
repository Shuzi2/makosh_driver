#!/bin/sh
# Run from the repository root. Briefly stop writes for a consistent DB+file backup.
set -eu
umask 077
backup_dir="${1:-backups/$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "$backup_dir"
docker compose stop web
trap 'docker compose start web >/dev/null' EXIT INT TERM
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup_dir/database.dump"
docker compose run --rm --no-deps -T web python -c 'import sys,tarfile; t=tarfile.open(fileobj=sys.stdout.buffer,mode="w|gz");t.add("/data/uploads",arcname="uploads");t.close()' > "$backup_dir/uploads.tar.gz"
chmod 600 "$backup_dir/database.dump" "$backup_dir/uploads.tar.gz"
echo "Backup saved in $backup_dir. Keep it outside this server and Git."
