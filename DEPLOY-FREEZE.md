# Deploy freeze — 2026-08-23

Production (`Lead Brain`, id `fvHhAUDCbVN9ELb0`, Mac mini) runs a **live-edited**
version with computed availability. `master` does NOT contain it.

**Do not run `deploy.sh` from `master`** — it rebuilds from the Python generators
and silently overwrites the running fix.

Live snapshot: branch `demo/computed-availability`, `snapshots/`.

Open question before merge: computed hourly slots (08:00–21:00) offer every hour
of the day, which doesn't match a class-based studio. Needs a product decision.
