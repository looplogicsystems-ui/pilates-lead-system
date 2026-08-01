#!/usr/bin/env bash
# Apply every migration in db/migrations in order, then the local ID seed if present.
#
# Safe to re-run: every migration is idempotent and records itself in
# `schema_migrations`. Applying to a fresh, empty database works too — run
# lead_system_schema.sql first for the base tables, then this.
#
# Usage:
#   db/migrate.sh                       # apply to the default local Docker Postgres
#   PGDATABASE=postgres db/migrate.sh   # override target database
#
# The lead-system tables live in the `postgres` database on this deployment
# (n8n's own tables are in `n8n` — do not point this at that one).
set -euo pipefail

CONTAINER="${PG_CONTAINER:-postgres}"
DB="${PGDATABASE:-postgres}"
USER_="${PGUSER:-n8n}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Pick a working docker binary. On WSL without Docker Desktop's WSL integration
# a `docker` shim exists on PATH but only prints an error, so test that it
# actually talks to a daemon rather than merely that the command resolves.
DOCKER=""
for candidate in docker docker.exe; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" ps >/dev/null 2>&1; then
        DOCKER="$candidate"; break
    fi
done
if [ -z "$DOCKER" ]; then
    echo "No working docker CLI found (tried: docker, docker.exe). Is Docker running?" >&2
    exit 1
fi

run_sql () {
    "$DOCKER" exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -U "$USER_" -d "$DB" -q < "$1"
}

echo "Applying migrations to database '$DB' in container '$CONTAINER'"

for f in "$DIR"/migrations/*.sql; do
    name="$(basename "$f")"
    printf '  %-38s ' "$name"
    run_sql "$f"
    echo "ok"
done

if [ -f "$DIR/seed/local-ids.sql" ]; then
    printf '  %-38s ' "seed/local-ids.sql"
    run_sql "$DIR/seed/local-ids.sql"
    echo "ok"
else
    echo "  (no db/seed/local-ids.sql — deployment IDs not applied;"
    echo "   copy db/seed/local-ids.sql.example and fill it in)"
fi

echo "Done."
