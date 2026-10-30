#!/usr/bin/env python3
"""Run SQL scenario tests against the mini's Postgres INSIDE a transaction that
is always rolled back (nothing is kept). Each *.sql here may contain
`{{QUERY:<workflow file>:<node name>}}`, replaced by that node's current query
from workflows/<file>, optionally with `|RETURNING=<expr>` to shorten output.

    python3 tests/sql/run.py                 # all
    python3 tests/sql/run.py followup        # files whose name contains "followup"
"""
import json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MINI = "mac-server@100.64.98.51"


def node_query(m):
    f, node = m.group(1), m.group(2)
    w = json.load(open(os.path.join(ROOT, "workflows", f)))
    q = [n for n in w["nodes"] if n["name"] == node][0]["parameters"]["query"].strip().rstrip(";")
    if m.group(3):
        q = re.sub(r"RETURNING\s+due\.\*\s*$", "RETURNING " + m.group(3), q)
    return q


for name in sorted(os.listdir(HERE)):
    if not name.endswith(".sql") or (sys.argv[1:] and not any(a in name for a in sys.argv[1:])):
        continue
    sql = re.sub(r"\{\{QUERY:([^:}]+):([^|}]+)(?:\|RETURNING=([^}]+))?\}\}", node_query, open(os.path.join(HERE, name)).read())
    assert sql.lstrip().startswith("BEGIN;") and sql.rstrip().endswith("ROLLBACK;"), f"{name} must be BEGIN ... ROLLBACK"
    print(f"== {name}")
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", MINI,
                        "docker exec -i postgres psql -U n8n -d postgres -v ON_ERROR_STOP=1 -P pager=off"],
                       input=sql, capture_output=True, text=True, timeout=120)
    print("\n".join(l for l in (r.stdout + r.stderr).splitlines()
                    if l and not re.match(r"^(BEGIN|PREPARE|CREATE TABLE|ROLLBACK|INSERT 0 \d+|UPDATE \d+)$", l)))
