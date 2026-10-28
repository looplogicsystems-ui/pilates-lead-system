#!/usr/bin/env python3
"""Make an Instagram connect link for one studio (pilot build step 6).

    python3 workflows/build/ig_connect_link.py <studio id> [--days 7]

Send the printed link to the studio owner. She opens it on her phone, logs in
on Instagram's own screen with the studio's account, and taps Allow; the IG
Connect workflow stores the account and its encrypted token. The link works
once and expires after --days.

The link is signed with OWNER_LINK_SECRET, which never leaves the mini: the
signature is computed inside the n8n container over SSH.
"""
import argparse, json, secrets, subprocess, sys, time, urllib.parse

from ig_common import IG_APP_ID, IG_REDIRECT_URI, IG_SCOPES

MINI = "mac-server@100.64.98.51"

SIGN_JS = r"""
const crypto = require('crypto');
const secret = process.env.OWNER_LINK_SECRET || '';
if (secret.length < 32) { console.error('OWNER_LINK_SECRET missing in the n8n container'); process.exit(1); }
const p64 = Buffer.from(process.env.PAYLOAD, 'utf8').toString('base64url');
process.stdout.write(p64 + '.' + crypto.createHmac('sha256', secret).update('ig-connect|' + p64).digest('hex').slice(0, 32));
"""


def ssh(cmd, stdin=None):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", MINI, cmd],
                       input=stdin, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        sys.exit(f"ssh failed: {r.stderr.strip()}")
    return r.stdout.strip()


def main():
    ap = argparse.ArgumentParser(description="Make an Instagram connect link for one studio.")
    ap.add_argument("studio", type=int, help="studios.id")
    ap.add_argument("--days", type=int, default=7, help="days the link stays valid (default 7)")
    a = ap.parse_args()
    studio, days = a.studio, a.days
    if not 1 <= days <= 30:
        sys.exit("--days must be 1-30")
    row = ssh(f"docker exec postgres psql -U n8n -d postgres -A -t -c "
              f"\"SELECT name || '|' || CASE WHEN active THEN 'yes' ELSE 'no' END FROM studios WHERE id = {studio};\"")
    if not row:
        sys.exit(f"no studio with id {studio}")
    name, active = row.rsplit("|", 1)
    if active != "yes":
        sys.exit(f"studio {studio} ({name}) is not active")
    payload = json.dumps({"s": studio, "e": int(time.time()) + days * 86400,
                          "n": secrets.token_urlsafe(18)}, separators=(",", ":"))
    state = ssh(f"docker exec -i -e PAYLOAD='{payload}' n8n node -", stdin=SIGN_JS)
    url = "https://www.instagram.com/oauth/authorize?" + urllib.parse.urlencode({
        "force_reauth": "true", "client_id": IG_APP_ID, "redirect_uri": IG_REDIRECT_URI,
        "response_type": "code", "scope": IG_SCOPES, "state": state})
    print(f"Connect link for studio {studio} ({name}), valid {days} days, works once:\n\n{url}\n")


if __name__ == "__main__":
    main()
