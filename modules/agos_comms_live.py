#!/usr/bin/env python3
# modules/agos_comms_live.py — Agent OS orchestration, Phase 1 CUTOVER: the LIVE routing layer that
# promotes agos-events from SHADOW (observe + parallel-emit, change nothing) to the ROUTING TRUTH.
# The shadow proved parity (BUILD-PLAN §criteria: 0 missed, 0 double-fires, byte-match over the tested
# window). Cutover = the dispatch consumers stop RE-SCANNING brain-comms on a poll and instead REACT on
# the log's delta. PIN #2 stays: brains keep EMITTING the markdown comm (the content artifact); only the
# ROUTING mechanism moves to the log. Rollback is trivial (BUILD-PLAN): stop reading the log, glob again —
# nothing to un-migrate, because the markdown never stopped being written.
#
# THREE LIVE ROLES, all thin over agos_events + agos_comms_shadow (no new routing logic — the shadow's
# route derivations are the proven ones; this file only changes WHO drives them and WHEN):
#
#   emit_once()      — the EMITTER. Verbatim S.scan_once: one `request` per new comm (routed by
#                      file_route, PIN #2 payload carries {path,hash,addressees,mtime}) + one `done` per
#                      comm now in _done/. Idempotent (corr_id==basename; re-run emits 0). At cutover the
#                      SAME code that was the shadow observer becomes the authoritative feed — it was
#                      built cutover-faithful on purpose. Single-writer per machine-file (Geist ruling #1):
#                      during the DVo-first stage DVo is the SOLE emitter and writes every author-host
#                      file; when another machine cuts over it takes over ITS OWN host file (a Geist-
#                      blessed topology follow-up — see the handback). Changes ZERO routing behavior.
#
#   wake_pending()   — the per-brain CONSUMER that REPLACES the glob-poll. consume() the `comms` topic for
#                      one brain, advancing its durable cursor, and return the corr_ids of the NEW
#                      request events routed to it. Non-empty => the caller wakes that brain (react-on-
#                      delta), empty => nothing new, DON'T wake (the poll-cost win). EXACTLY-ONCE by the
#                      cursor: a re-run over the same log returns [] — the mtime-refire double-fire scar is
#                      structurally impossible, not merely unlikely. A `defer` (Geist PIN #1) leaves the
#                      event unconsumed for the next wake; wake_pending never defers (a wake is not work).
#
#   cohesion_sweep() — SAGA's consumer (Geist ruling #7, the named cutover-scope item). READ-ONLY, advances
#                      NO cursor. Threads request<->done by corr_id off the log and returns the UNRESOLVED
#                      chains (a `request` with no `done`). This is the crisp on-the-log replacement for
#                      Saga's old filename heuristic (guessing "unresolved" from a _done/ absence).
#
# Env (shared with the shadow so the live root == the shadow root == cursors already at "now", so cutover
# does NOT re-fire history): AGOS_EVENTS_DIR (root), AGOS_COMMS_DIR (brain-comms), AGOS_MESH_MODEL.

import os

import agos_events as E
import agos_comms_shadow as S


# ---- config (defaults match agos_comms_shadow: the live root IS the proven shadow root) --------------
def default_root():
    return os.path.expanduser(os.environ.get(
        "AGOS_EVENTS_DIR", "~/jarvis-sync/dvo-orchestration-shadow/events-root"))


def default_comms_dir():
    return os.path.expanduser(os.environ.get("AGOS_COMMS_DIR", "~/jarvis-sync/brain-comms"))


def load_model():
    return S.load_model(os.environ.get("AGOS_MESH_MODEL"))


# ---- the EMITTER (verbatim shadow scan; authoritative at cutover) -----------------------------------
def emit_once(comms_dir, root, model):
    """Emit one request per new comm + one done per newly-completed comm. Idempotent. Returns
    {'emitted': n, 'dones': n}. This is exactly S.scan_once — the shadow emitter, now the live feed."""
    return S.scan_once(comms_dir, root, model)


# ---- the per-brain CONSUMER (react-on-delta; replaces the glob-poll) --------------------------------
def wake_pending(root, brain, reader_machine="dvo"):
    """Advance `brain`'s cursor over the `comms` topic and return the corr_ids (comm basenames) of the
    NEW request events routed to it. Non-empty => wake `brain`. EXACTLY-ONCE: a second call returns []
    (the cursor), so no comm ever wakes a brain twice — the structural kill of the mtime-refire.

    `reader_machine` is cosmetic (consume() merges every machine-file and cursors per WRITER-file); it
    only has to satisfy the machine-name grammar. The brain's own host is a sensible value."""
    log = E.EventLog(root, reader_machine)
    fired = []

    def handler(ev):
        # A `request` is a summons; done/note/error are not. route_to in consume() already dropped
        # events not addressed to this brain (advancing past them), so anything here is genuinely ours.
        if ev.get("kind") == "request":
            cid = ev.get("corr_id")
            if cid:
                fired.append(cid)

    log.consume(brain, "comms", handler, route_to=brain)
    return fired


def peek_pending(root, brain):
    """Cursor-INDEPENDENT view for diagnostics/parity: the corr_ids currently routed to `brain` on the
    log, and how many sit AFTER its committed cursor (== what the next wake_pending would fire). Reads
    only; advances nothing."""
    log = E.EventLog(root, brain if _valid_machine(brain) else "dvo")
    cursor = log.cursor_get(brain, "comms")
    all_routed, after_cursor = [], []
    seen = log.read("comms")
    committed = {m: int(i) for m, i in (cursor or {}).items()}
    for ev in seen:
        if ev.get("kind") != "request":
            continue
        to = ev.get("to") or []
        if to and brain not in to:
            continue
        cid = ev.get("corr_id")
        all_routed.append(cid)
        if int(ev.get("id", 0)) > committed.get(ev.get("_machine", ""), 0):
            after_cursor.append(cid)
    return {"routed_total": len(all_routed), "pending_after_cursor": after_cursor,
            "pending_count": len(after_cursor)}


def _valid_machine(name):
    import re
    return bool(re.match(r"^[a-z0-9-]+$", name or ""))


# ---- SAGA's consumer: the cohesion sweep (read-only; unresolved corr_id chains) ---------------------
def cohesion_sweep(root, min_age_s=0, now_iso=None):
    """Thread request<->done by corr_id off the `comms` log and return the UNRESOLVED chains: a request
    with no matching done. READ-ONLY (advances no cursor) — Saga observes cohesion, it does not consume.
    `min_age_s` filters to chains whose request is older than that many seconds (an unresolved-for-a-while
    alarm); 0 = all unresolved. `now_iso` overrides 'now' for deterministic tests. Returns a report."""
    log = E.EventLog(root, "saga")  # reader machine irrelevant to read()
    requests = {}   # corr_id -> first request event (corr_id is idempotent, first wins)
    done = set()
    for ev in log.read("comms"):
        cid = ev.get("corr_id")
        if not cid:
            continue
        k = ev.get("kind")
        if k == "request":
            requests.setdefault(cid, ev)
        elif k == "done":
            done.add(cid)

    now = _epoch_of(now_iso) if now_iso else _now_epoch()
    unresolved = []
    for cid, ev in requests.items():
        if cid in done:
            continue
        ts = ev.get("ts")
        age = (now - _epoch_of(ts)) if ts else None
        if min_age_s and (age is None or age < min_age_s):
            continue
        to = ev.get("to") or []
        # An orphan chain routes to no one (a broadcast/directive/-to-mesh with no single completer):
        # it will NEVER get a done, so it is NOT a stuck ask — Saga must not alarm on it. An ADDRESSED
        # chain (routes to >=1 real brain, no done) is the load-bearing signal: a summons that a brain
        # owns but hasn't completed. Split them so Saga alarms on the second, not the first.
        orphan = (not to) or to == [S.NOBODY]
        unresolved.append({"corr_id": cid, "ts": ts, "actor": ev.get("actor"),
                           "to": to, "age_s": None if age is None else int(age), "orphan": orphan})
    unresolved.sort(key=lambda r: r["ts"] or "")
    addressed = [u for u in unresolved if not u["orphan"]]
    orphans = [u for u in unresolved if u["orphan"]]
    return {"unresolved": unresolved, "unresolved_count": len(unresolved),
            "addressed_unresolved": addressed, "addressed_unresolved_count": len(addressed),
            "orphan_unresolved_count": len(orphans),
            "request_count": len(requests), "done_count": len(done)}


def _now_epoch():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).timestamp()


def _epoch_of(iso):
    import datetime
    if not iso:
        return 0.0
    s = iso.replace("Z", "+00:00")
    try:
        return datetime.datetime.fromisoformat(s).timestamp()
    except ValueError:
        return 0.0


# ---- CLI -------------------------------------------------------------------------------------------
def main(argv=None):
    import json
    import sys
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "help"
    root = default_root()

    if cmd == "emit":
        res = emit_once(default_comms_dir(), root, load_model())
        print(json.dumps({"emit": res}, sort_keys=True))
        return 0
    if cmd == "wake":
        if len(argv) < 2:
            print("usage: agos-comms-live wake <brain>", file=sys.stderr)
            return 2
        brain = argv[1]
        fired = wake_pending(root, brain, reader_machine=brain if _valid_machine(brain) else "dvo")
        # Exit 10 == "there is a new summons, wake the brain"; 0 == nothing new (do NOT wake).
        print(json.dumps({"brain": brain, "wake": bool(fired), "corr_ids": fired}, sort_keys=True))
        return 10 if fired else 0
    if cmd == "peek":
        if len(argv) < 2:
            print("usage: agos-comms-live peek <brain>", file=sys.stderr)
            return 2
        print(json.dumps(peek_pending(root, argv[1]), sort_keys=True))
        return 0
    if cmd == "cohesion":
        min_age = int(argv[1]) if len(argv) > 1 else 0
        print(json.dumps(cohesion_sweep(root, min_age_s=min_age), indent=2, sort_keys=True))
        return 0
    print("usage: agos-comms-live [emit | wake <brain> | peek <brain> | cohesion [min_age_s]]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
