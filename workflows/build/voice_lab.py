#!/usr/bin/env python3
"""Voice lab: run scripted conversations through the REAL Lead Brain and print
them as transcripts, so the AI's voice can be judged and tuned.

Each conversation is a fresh 'test' lead on the demo studio (channel_accounts
test/voice-lab -> studio 2). Owner emails for these go to Resend's sink.

Needs the SSH tunnel (localhost:5679) and the token in
~/.config/looplogic/voicelab.token (never committed).

  python3 workflows/build/voice_lab.py            # all scenarios
  python3 workflows/build/voice_lab.py nervous    # scenarios whose name contains "nervous"
"""
import json, os, sys, time, urllib.request, urllib.error

URL = os.environ.get("N8N_API_URL", "http://localhost:5679").rstrip("/") + "/webhook/ops/voice-lab"
TOKEN = open(os.path.expanduser("~/.config/looplogic/voicelab.token")).read().strip()

SCENARIOS = {
    "beginner-books": ["hi! do you have anything for total beginners? weekday evenings work for me",
                       "yes that works, how do I book?",
                       "booked!"],
    "nervous": ["I've never done pilates and I'm pretty unfit tbh, will I be the worst one there?"],
    "terse": ["times?"],
    "price-shopper": ["how much is it"],
    "haggle": ["can you do the intro pass for $50? thats what the other place charges"],
    "pregnant": ["hi I'm 12 weeks pregnant, can I still do reformer?"],
    "bot-question": ["is this a bot lol"],
    "returning": ["I used to come last year, can I just drop in on saturday?"],
    "no-such-time": ["do you have anything at 8pm on weekdays?"],
    "excited": ["omg hi!! my friend goes to you and is obsessed 😍 I want to try reformer, anything saturday morning??"],
}


def say(lead, message):
    body = json.dumps({"lead": lead, "message": message}).encode()
    req = urllib.request.Request(URL, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-Voice-Lab-Token": TOKEN})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            out = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"reply": f"<HTTP {e.code}>", "intent": "error"}, time.time() - t
    return out, time.time() - t


def main():
    wanted = sys.argv[1:]
    run = f"{int(time.time()) % 100000}"
    for name, turns in SCENARIOS.items():
        if wanted and not any(w in name for w in wanted):
            continue
        lead = f"{name}-{run}"
        print(f"\n=== {name} " + "=" * (60 - len(name)))
        for msg in turns:
            out, secs = say(lead, msg)
            print(f"  LEAD : {msg}")
            reply = (out.get("reply") or "").replace("\n", "\n         ")
            print(f"  AI   : {reply}")
            print(f"         [{out.get('intent')}, {secs:.1f}s]")


if __name__ == "__main__":
    main()
