#!/usr/bin/env python3
"""
Push generated workflows straight to the mini's n8n over its public REST API,
through an SSH tunnel -- no git pull on the mini, no deploy.sh, no restart.

This is the DEVELOPMENT path. deploy.sh stays the rebuild-from-git path
(llctl deploy / rollback / disaster recovery). Both read the same generated
*.live.json files, so they cannot drift as long as every pushed change is
also committed.

Setup (once):
  ssh -f -N -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes \
      -L 5679:127.0.0.1:5678 mac-server@100.64.98.51
  API key in ~/.config/looplogic/n8n-mini.key (mode 600) or $N8N_API_KEY.

Usage:
  python3 workflows/build/push.py <scratch-dir> [name-or-file ...] [--dry-run]
    no names   -> every *.live.json in <scratch-dir>
    --dry-run  -> show what would change, push nothing
    --force    -> push even when nothing differs (to prove the path works)

What it does, per workflow:
  1. GET the live workflow and save it to ~/.looplogic-push-backups/<ts>/<id>.json
     (restore with: push.py --restore <that file>)
  2. compare nodes/connections/settings with the generated file; skip if identical
  3. PUT name/nodes/connections/settings. On an ACTIVE workflow the public API
     publishes immediately (verified 2026-08-23) -- the change is live at once.
  4. if the generated file says active but n8n says inactive, POST /activate

A workflow that does not exist yet (404) is CREATED with POST. n8n assigns the
id; push.py prints it, and that id must then be written into the generator so
every later push and every deploy.sh import targets the same workflow.
"""
import json, os, sys, glob, time, urllib.request, urllib.error

API = os.environ.get("N8N_API_URL", "http://localhost:5679").rstrip("/") + "/api/v1"
KEY_FILE = os.path.expanduser("~/.config/looplogic/n8n-mini.key")
BACKUPS = os.path.expanduser("~/.looplogic-push-backups")

# Settings keys the public API accepts on PUT; anything else is rejected with 400.
SETTINGS_OK = {"saveExecutionProgress", "saveManualExecutions", "saveDataErrorExecution",
               "saveDataSuccessExecution", "executionTimeout", "errorWorkflow", "timezone",
               "executionOrder", "callerPolicy", "callerIds"}


def key():
    k = os.environ.get("N8N_API_KEY") or (open(KEY_FILE).read().strip() if os.path.exists(KEY_FILE) else "")
    if not k:
        sys.exit(f"no API key: set N8N_API_KEY or write it to {KEY_FILE}")
    return k


def call(method, path, body=None):
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-N8N-API-KEY": key(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, (json.loads(e.read() or b"{}") if e.headers.get("Content-Type", "").startswith("application/json") else {})
    except urllib.error.URLError as e:
        sys.exit(f"cannot reach {API} ({e.reason}) -- is the SSH tunnel up?")


def _norm(n):
    """A node minus fields that don't affect behaviour: webhookId, and credential
    display names (n8n resolves credentials by id; the stored name is cosmetic and
    the generators' names differ from the ones on the mini)."""
    n = {k: v for k, v in n.items() if k != "webhookId"}
    if "credentials" in n:
        n["credentials"] = {t: {"id": c.get("id")} for t, c in n["credentials"].items()}
    return n


def comparable(wf):
    """The parts of a workflow that define its behaviour, normalised for comparison."""
    nodes = sorted((_norm(n) for n in wf.get("nodes", [])),
                   key=lambda n: n.get("name", ""))
    settings = {k: v for k, v in (wf.get("settings") or {}).items() if k in SETTINGS_OK}
    return json.dumps({"name": wf.get("name"), "nodes": nodes,
                       "connections": wf.get("connections", {}), "settings": settings}, sort_keys=True)


def node_diff(live, gen):
    ln = {n["name"]: n for n in live.get("nodes", [])}
    gn = {n["name"]: n for n in gen.get("nodes", [])}
    out = [f"+ {n}" for n in gn if n not in ln] + [f"- {n}" for n in ln if n not in gn]
    for n in gn:
        if n in ln:
            a, b = _norm(ln[n]), _norm(gn[n])
            if json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True):
                out.append(f"~ {n}")
    if json.dumps(live.get("connections", {}), sort_keys=True) != json.dumps(gen.get("connections", {}), sort_keys=True):
        out.append("~ (connections)")
    return out


def push(path, dry, stamp, force=False):
    gen = json.load(open(path))
    wid, name = gen.get("id"), gen.get("name")
    body = {"name": name, "nodes": gen["nodes"], "connections": gen["connections"],
            "settings": {k: v for k, v in (gen.get("settings") or {}).items() if k in SETTINGS_OK}}
    status, live = call("GET", f"/workflows/{wid}") if wid else (404, {})

    if status == 404:
        print(f"  NEW   {name}: not on the mini")
        if dry:
            return
        status, created = call("POST", "/workflows", body)
        if status >= 300:
            sys.exit(f"  create failed ({status}): {created}")
        print(f"        created with id {created['id']}  -> put this id in the generator, then push again")
        return
    if status >= 300:
        sys.exit(f"  GET {wid} failed ({status}): {live}")

    changes = node_diff(live, gen)
    settings_changed = comparable(live) != comparable(gen) and not changes
    if not changes and comparable(live) == comparable(gen) and not force:
        print(f"  same  {name} ({wid})")
    else:
        print(f"  {'WOULD' if dry else 'PUSH '} {name} ({wid}): " +
              (", ".join(changes) if changes else "settings only" if settings_changed else "metadata"))
        if not dry:
            os.makedirs(os.path.join(BACKUPS, stamp), exist_ok=True)
            bfile = os.path.join(BACKUPS, stamp, f"{wid}.json")
            json.dump(live, open(bfile, "w"), indent=2)
            s, resp = call("PUT", f"/workflows/{wid}", body)
            if s >= 300:
                sys.exit(f"  PUT failed ({s}): {resp}\n  live copy saved at {bfile}")
            print(f"        live copy saved: {bfile}")

    if gen.get("active") and not live.get("active") and not dry:
        s, resp = call("POST", f"/workflows/{wid}/activate")
        print(f"        activated" if s < 300 else f"        ACTIVATE FAILED ({s}): {resp}")


def restore(path):
    wf = json.load(open(path))
    body = {"name": wf["name"], "nodes": wf["nodes"], "connections": wf["connections"],
            "settings": {k: v for k, v in (wf.get("settings") or {}).items() if k in SETTINGS_OK}}
    s, resp = call("PUT", f"/workflows/{wf['id']}", body)
    print(f"restored {wf['name']} ({wf['id']})" if s < 300 else f"restore failed ({s}): {resp}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--restore" in sys.argv:
        return restore(args[0])
    if not args:
        sys.exit(__doc__)
    scratch, wanted, dry = args[0], args[1:], "--dry-run" in sys.argv
    files = sorted(glob.glob(os.path.join(scratch, "*.live.json")))
    if wanted:
        files = [f for f in files if any(w in os.path.basename(f) or w == json.load(open(f)).get("name")
                                         for w in wanted)]
    if not files:
        sys.exit(f"no matching *.live.json in {scratch}")
    stamp = time.strftime("%Y-%m-%dT%H%M%S")
    print(f"{'DRY RUN -- ' if dry else ''}pushing to {API}")
    for f in files:
        push(f, dry, stamp, force="--force" in sys.argv)


if __name__ == "__main__":
    main()
