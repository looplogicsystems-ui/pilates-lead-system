#!/usr/bin/env bash
# Apply ONE studio seed (db/seed/studios/<slug>.sql) to the mini's database.
# Refuses while placeholders are left; takes an llctl snapshot first.
set -euo pipefail
MINI="${MINI_HOST:-mac-server@100.64.98.51}"
f="${1:-}"
[ -f "$f" ] || { echo "usage: $0 db/seed/studios/<slug>.sql"; exit 1; }
case "$f" in *_template*) echo "copy the template to db/seed/studios/<slug>.sql and fill it in first"; exit 1;; esac
if grep -nE '<[A-Z][A-Z_ ]*[^>]*>' "$f"; then echo "^ unfilled <PLACEHOLDERS> left in $f"; exit 1; fi
ssh -o BatchMode=yes "$MINI" 'llctl snapshot' | tail -1
ssh -o BatchMode=yes "$MINI" 'docker exec -i postgres psql -U n8n -d postgres -v ON_ERROR_STOP=1 -q' < "$f"
