#!/usr/bin/env bash
# Regenerate, import, publish and reload every Phase 2a workflow.
#
# n8n 2.x separates the draft (workflow_entity) from the published graph
# (workflow_history + activeVersionId). `import:workflow` only writes the
# draft, so after importing we refresh the history row for the workflow's
# versionId and publish it — otherwise n8n keeps running the previous
# definition and the import looks like it silently did nothing.
#
# Usage: workflows/build/deploy.sh [--no-restart]
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Holds the *unredacted* generated graphs (real credential ids) on their way into
# n8n. Repo-local and gitignored so this runs on any host; override with
# SCRATCH_DIR if you want them somewhere else.
SCRATCH="${SCRATCH_DIR:-$REPO/.deploy-tmp}"
mkdir -p "$SCRATCH"
BRAIN_ID="${BRAIN_ID:-fvHhAUDCbVN9ELb0}"

DOCKER=""
for c in docker docker.exe; do
    if command -v "$c" >/dev/null 2>&1 && "$c" ps >/dev/null 2>&1; then DOCKER="$c"; break; fi
done
[ -n "$DOCKER" ] || { echo "no working docker CLI" >&2; exit 1; }

psql_n8n () { "$DOCKER" exec -i postgres psql -q -v ON_ERROR_STOP=1 -U n8n -d n8n "$@"; }

echo "==> generating"
# Start from an empty scratch dir: the import list below is built from whatever
# *.live.json files exist, so a stale file from an old build must not survive.
rm -f "$SCRATCH"/*.live.json
python3 "$REPO/workflows/build/build_lead_brain.py" "$SCRATCH"
python3 "$REPO/workflows/build/build_adapters.py"  "$SCRATCH" "$BRAIN_ID"
python3 "$REPO/workflows/build/build_ops.py"       "$SCRATCH"
python3 "$REPO/workflows/build/build_pilot.py"     "$SCRATCH"

# Every generated workflow is imported AND published. This list used to be typed
# by hand in three places; a new workflow missing from any of them imported as an
# unpublished draft and never ran, while the deploy still reported success.
FILES="$(cd "$SCRATCH" && ls *.live.json | sed 's/\.live\.json$//' | tr '\n' ' ')"
NAMES_SQL="$(python3 - "$SCRATCH" <<'PYEOF'
import glob, json, os, sys
names = [json.load(open(f))["name"] for f in sorted(glob.glob(os.path.join(sys.argv[1], "*.live.json")))]
print(",".join("'" + n.replace("'", "''") + "'" for n in names))
PYEOF
)"
[ -n "$FILES" ] && [ -n "$NAMES_SQL" ] || { echo "nothing generated" >&2; exit 1; }
echo "    workflows: $FILES"

echo "==> importing"
for f in $FILES; do
    "$DOCKER" cp "$SCRATCH/$f.live.json" n8n:/tmp/$f.json >/dev/null
    "$DOCKER" exec n8n n8n import:workflow --input=/tmp/$f.json 2>&1 \
        | grep -qi "Successfully imported" && echo "    $f" \
        || { echo "    $f FAILED"; "$DOCKER" exec n8n n8n import:workflow --input=/tmp/$f.json 2>&1 | tail -3; exit 1; }
done

echo "==> syncing published versions"
psql_n8n -c "
INSERT INTO workflow_history (\"versionId\", \"workflowId\", authors, nodes, connections, name)
SELECT w.\"versionId\", w.id, 'phase-2a-deploy', w.nodes, w.connections, w.name
  FROM workflow_entity w
 WHERE w.name IN ($NAMES_SQL)
ON CONFLICT (\"versionId\") DO UPDATE
   SET nodes = EXCLUDED.nodes,
       connections = EXCLUDED.connections,
       name = EXCLUDED.name,
       \"updatedAt\" = now();" >/dev/null

echo "==> publishing"
for id in $(psql_n8n -t -A -c "
SELECT id FROM workflow_entity
 WHERE name IN ($NAMES_SQL);"); do
    "$DOCKER" exec n8n n8n publish:workflow --id="$id" >/dev/null 2>&1 \
        && echo "    published $id" || echo "    publish $id FAILED"
done

if [ "${1:-}" != "--no-restart" ]; then
    echo "==> restarting n8n"
    "$DOCKER" restart n8n >/dev/null
    for i in $(seq 1 40); do
        if [ "$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://localhost:5678/healthz 2>/dev/null)" = "200" ]; then
            echo "    healthy after ~$((i*3))s"; break
        fi
        sleep 3
    done
fi

psql_n8n -c "SELECT name, active FROM workflow_entity WHERE active ORDER BY name;"

# Don't trust the exit codes above: confirm every generated workflow is published.
inactive="$(psql_n8n -t -A -c "SELECT name FROM workflow_entity WHERE name IN ($NAMES_SQL) AND NOT active;")"
if [ -n "$inactive" ]; then
    echo "!! NOT ACTIVE after deploy:" >&2; echo "$inactive" | sed 's/^/   /' >&2; exit 1
fi
echo "==> all $(echo $FILES | wc -w) generated workflows are active"
