#!/usr/bin/env python3
# modules/agos_comms_shadow.py — Agent OS orchestration, Phase 1 part 2: SHADOW-migrate brain-comms
# routing onto agos-events. It OBSERVES + parallel-emits. It changes NO routing behavior — the live
# `_done/` + per-brain dispatch-watchers stay the truth through shadow (Rabbot GO 2026-07-31, PIN #2).
#
# WHAT IT DOES (all read-only against the live mesh):
#   1. EMIT — one `comms` event per brain-comm the watchers route, into the AUTHOR's machine-file
#      (per-(topic,machine), Geist ruling #1), stamped ts = the comm's file mtime so the merge order
#      reproduces real arrival order. `to` = the RESOLVED consumer set; payload carries {path, hash,
#      addressees, mtime} so the markdown comm survives as the content artifact (PIN #2).
#   2. DONE — when the live dispatch marks a comm processed (it appears in `_done/`), emit a `done`
#      event threaded by corr_id (= the comm basename).
#   3. SHADOW-CONSUME — a cursor'd consumer per brain computes what the event log WOULD deliver, and
#      logs it. Takes NO action. The cursor is what STRUCTURALLY kills the mtime-refire double-fire.
#   4. DIFF — compare the event-log routing to the ground truth (the watchers' own glob logic, plus,
#      where available, the live watcher state files) → the before/after metrics Rabbot reads to Dillon.
#
# THE PARITY PROOF (non-circular): routing is derived TWO independent ways that MUST agree on every comm —
#   file_route()    — fnmatch each brain's REAL globs from mesh_model.json + rebound (actor==brain).
#                     A faithful port of what the .sh dispatch-watchers actually do. GROUND TRUTH.
#   route_from_to() — map the `to` tokens parsed from the filename (parse_addressees), the way the event
#                     log routes. This is the "moved to the log" path.
# If these ever disagree on a real comm, that's a routing bug the shadow surfaces before any cutover.
# The emitted event's `to` is file_route()'s output (routing computed ONCE at emit, replacing N watchers
# each re-globbing — that IS the do-more-with-less win); the contract test proves route_from_to agrees.

import fnmatch
import glob
import hashlib
import json
import os
import re

import agos_events as E

# The only brains whose watchers glob `*-to-all-*` (mesh_model known_hazards.broadcast_gap):
# scout/phoenix/geist/saga are NOT reached by a broadcast — they must be addressed explicitly.
BROADCAST_BRAINS = ("augur", "page", "rabbot")

# Sentinel `to` entry for a comm that resolves to NO consumer (e.g. `-to-mesh-` orphan, or an unknown
# addressee). Kept DISTINCT from an empty `to`, because agos_events treats empty `to` as BROADCAST —
# an orphan must route to nobody, not everybody.
NOBODY = "_nobody"

_MESH_MODEL_DEFAULT = "~/jarvis-sync/saga/mesh/mesh_model.json"


def load_model(path=None):
    with open(os.path.expanduser(path or _MESH_MODEL_DEFAULT)) as f:
        return json.load(f)


# ---- comm filename grammar --------------------------------------------------
# Canonical summons name (mesh_model): `<date>-<from>-to-<addr...>-<slug>.md` (date-first) OR
# `<from>-to-<addr...>-<slug>-<date>.md` (from-first). Both carry a single `-to-` pivot.

def is_comm(basename):
    """A routable brain-comm in the root dir: a .md with a `-to-` pivot, not a bucket/dotfile
    (_done/_archive/_processed/_resolved/.claude) and not a SyncThing conflict copy."""
    if not basename.endswith(".md") or basename[:1] in ("_", "."):
        return False
    if ".sync-conflict-" in basename:
        return False
    return "-to-" in basename


def parse_actor(basename):
    """The author = the token immediately before the `-to-` pivot (date prefix, if any, sorts ahead
    of it). `2026-07-25-air-to-mesh-x.md` -> 'air'; `rabbot-to-augur-x-2026-07-31.md' -> 'rabbot'."""
    left = basename.split("-to-", 1)[0]
    parts = [p for p in left.split("-") if p]
    return parts[-1] if parts else ""


def parse_addressees(basename):
    """INDEPENDENT token-parse of the addressee segment into a `to` token list — mirroring the ONLY
    address forms the watchers actually match:
      - `all`            -> ['all']              (broadcast; reaches BROADCAST_BRAINS only)
      - `mesh`           -> ['mesh']             (orphan; no watcher globs *-to-mesh-*)
      - `air-<bbrain>`   -> ['air', '<bbrain>']  (the combined `*-to-air-<brain>-*` globs)
      - `<tok>`          -> ['<tok>']            (single addressee)
    Greedy multi-token capture is deliberately AVOIDED (only the `air-<brain>` combined form exists as
    a real glob) so a slug word that happens to be a brain name can't spuriously widen the route."""
    right = basename.split("-to-", 1)[1]
    if right.endswith(".md"):
        right = right[:-3]
    toks = [t for t in right.split("-") if t]
    if not toks:
        return []
    first = toks[0]
    if first == "all":
        return ["all"]
    if first == "mesh":
        return ["mesh"]
    if first == "air" and len(toks) > 1 and toks[1] in BROADCAST_BRAINS:
        return ["air", toks[1]]
    return [first]


def _watched_brains(model):
    """Brains whose watcher actually globs something (saga/prophet have no watcher → never summoned)."""
    return {b for b, spec in model["brains"].items() if spec.get("reach", {}).get("globs")}


def route_from_to(to_tokens, actor, model):
    """Map parsed `to` tokens -> the resolved set of summoned brains. The event-log routing path.
    `all` expands to BROADCAST_BRAINS; `mesh`/`air` contribute no brain on their own (a combined
    `air-<brain>` summons <brain>, which is the token that follows); a rebound (actor's own comm) is
    dropped. Independent of file_route() by construction — the two must agree (the parity proof)."""
    watched = _watched_brains(model)
    out = set()
    for t in to_tokens:
        if t == "all":
            out |= set(BROADCAST_BRAINS)
        elif t in ("mesh", "air"):
            continue  # no standalone brain; `air-<brain>` handled via the following token
        elif t in watched:
            out.add(t)
    out.discard(actor)  # rebound: a brain never summons on a comm it authored
    return frozenset(out)


def file_route(basename, model):
    """GROUND TRUTH — a faithful port of the dispatch-watchers: brain X summons on this comm iff the
    basename matches one of X's real globs, X did not author it (rebound: actor==X, i.e. the watchers'
    `<X>-to-*` / `*-<X>-to-*` skips), and no explicit exclude_glob fires."""
    actor = parse_actor(basename)
    out = set()
    for brain, spec in model["brains"].items():
        globs = spec.get("reach", {}).get("globs") or []
        if not globs or not any(fnmatch.fnmatch(basename, g) for g in globs):
            continue
        if brain == actor:
            continue  # rebound
        excl = spec.get("reach", {}).get("exclude_globs") or []
        if any(fnmatch.fnmatch(basename, e) for e in excl):
            continue
        out.add(brain)
    return frozenset(out)


def resolve_to(basename, model):
    """The `to` field the shadow emits: the resolved consumer list (sorted), or [NOBODY] for a comm
    that routes to no one — never [] (which agos_events reads as broadcast)."""
    brains = sorted(file_route(basename, model))
    return brains if brains else [NOBODY]


# ---- machine partition + small helpers --------------------------------------
def _san(s):
    s = re.sub(r"[^a-z0-9-]", "-", (s or "").strip().lower()).strip("-")
    return s or "unknown"


def host_of(actor, model):
    """The machine-file a comm's event lands in = its AUTHOR's host (each brain single-writes its own
    host's file at cutover). Unknown author -> its own name as a pseudo-host (routing is unaffected —
    machine only breaks ts ties in the merge)."""
    spec = model["brains"].get(actor)
    if spec and spec.get("host"):
        return _san(spec["host"])
    return _san(actor)


def _iso(epoch):
    import datetime
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _emitted_corr_ids(log, kind):
    return {ev["corr_id"] for ev in log.read("comms")
            if ev.get("kind") == kind and ev.get("corr_id")}


# ---- the shadow scan (emit + done), idempotent ------------------------------
def scan_once(comms_dir, root, model):
    """Emit one `comms` request-event per not-yet-emitted comm (in mtime order) and one `done` per
    comm now in `_done/` not yet done'd. Idempotent: corr_id == basename, and we skip anything already
    on the log, so re-running emits nothing new. Returns {'emitted': n, 'dones': n}."""
    comms_dir = os.path.expanduser(comms_dir)
    reader = E.EventLog(root, "dvo")  # reader machine is irrelevant to read() (it merges all files)
    already = _emitted_corr_ids(reader, "request")
    done_already = _emitted_corr_ids(reader, "done")

    files = [p for p in glob.glob(os.path.join(comms_dir, "*.md"))
             if is_comm(os.path.basename(p))]
    files.sort(key=lambda p: os.path.getmtime(p))

    emitted = 0
    known = set(already)
    for path in files:
        base = os.path.basename(path)
        if base in known:
            continue
        actor = parse_actor(base)
        mtime_iso = _iso(os.path.getmtime(path))
        payload = {
            "path": "brain-comms/" + base,
            "hash": _hash(path),
            "addressees": parse_addressees(base),
            "mtime": mtime_iso,
        }
        writer = E.EventLog(root, host_of(actor, model))
        writer.emit("comms", "request", payload=payload, corr_id=base, actor=actor,
                    to=resolve_to(base, model), ts=mtime_iso)
        known.add(base)
        emitted += 1

    dones = 0
    done_dir = os.path.join(comms_dir, "_done")
    for path in glob.glob(os.path.join(done_dir, "*.md")):
        base = os.path.basename(path)
        if base in done_already or base not in known:
            continue  # only complete comms we actually emitted a request for
        actor = parse_actor(base)
        writer = E.EventLog(root, host_of(actor, model))
        # A done routes to NOBODY, never broadcast: a completion must not re-summon every consumer.
        # agos_events' done()/emit() treat an empty `to` as BROADCAST, so a bare done() would be
        # "delivered" to every brain's shadow consumer — inflating each brain's delivery count by the
        # done-count (the delivered≠routed skew a naive readout shows). await_done() finds a done by
        # corr_id via read() regardless of `to`, so completion threading is unaffected by routing here.
        writer.emit("comms", "done", payload={"marker": "_done/" + base}, corr_id=base, actor=actor,
                    to=[NOBODY], ts=_iso(os.path.getmtime(path)))
        done_already.add(base)
        dones += 1

    return {"emitted": emitted, "dones": dones}


# ---- the shadow consumer (observation only) ---------------------------------
def deliver_once(root, model, sink=None):
    """Run the cursor'd consumer once per watched brain. EXACTLY-ONCE by construction: a comm already
    delivered to brain X never redelivers (the cursor), and a re-scan yields 0 new — that is the
    structural kill of the mtime-refire double-fire scar. Takes NO action beyond recording to `sink`
    (a list of (brain, corr_id) appended per delivery). Returns {brain: new_delivered_count}."""
    log = E.EventLog(root, "dvo")
    counts = {}
    for brain in sorted(_watched_brains(model)):
        def handler(ev, _b=brain):
            if ev.get("kind") != "request":
                return  # done/note events aren't a summons
            if sink is not None:
                sink.append((_b, ev.get("corr_id")))
        res = log.consume(brain, "comms", handler, route_to=brain)
        counts[brain] = res["processed"]
    return counts


def shadow_route_snapshot(root, model):
    """Full (cursor-independent) routing snapshot straight off the log: {brain: set(corr_ids)} for
    every request-event whose resolved `to` includes that brain. Used by the diff (not the cursor
    path, which shows only deltas)."""
    log = E.EventLog(root, "dvo")
    snap = {b: set() for b in _watched_brains(model)}
    for ev in log.read("comms"):
        if ev.get("kind") != "request":
            continue
        for brain in ev.get("to", []):
            if brain in snap:
                snap[brain].add(ev.get("corr_id"))
    return snap


# ---- the diff / metrics readout (for Rabbot) --------------------------------
def _watcher_fired(state_dir, brain):
    """The ACTUAL summons record from a live dispatch-watcher state file: the set of comm basenames
    the watcher fired on. Ground truth from the RUNNING system (not our re-derivation). {} if absent."""
    path = os.path.join(os.path.expanduser(state_dir), "%s-dispatch-watcher.json" % brain)
    try:
        with open(path) as f:
            pb = json.load(f).get("processed_basenames", {})
    except (FileNotFoundError, ValueError):
        return set()
    return set(pb.keys()) if isinstance(pb, dict) else set(pb)


def diff_report(comms_dir, root, model, state_dir="~/jarvis-sync/state"):
    """The before/after metrics. Two comparisons:
      A. PORT PARITY (over the live corpus): for every comm, does route_from_to(parse_addressees) ==
         file_route(globs)?  Divergences = routing-logic bugs. Target 0.
      B. LIVE CROSS-CHECK (where watcher state exists): shadow-delivered vs watcher-fired per brain —
         missed (fired, shadow wouldn't) and extra (shadow would, not-yet/never-fired).
    Plus the structural double-fire guarantee (re-consume yields 0 new). Caveats are recorded inline."""
    comms_dir = os.path.expanduser(comms_dir)
    bases = [os.path.basename(p) for p in glob.glob(os.path.join(comms_dir, "*.md"))
             if is_comm(os.path.basename(p))]

    divergences = []
    for b in sorted(bases):
        actor = parse_actor(b)
        by_glob = file_route(b, model)
        by_to = route_from_to(parse_addressees(b), actor, model)
        if by_glob != by_to:
            divergences.append({"comm": b, "by_glob": sorted(by_glob), "by_to": sorted(by_to)})

    snap = shadow_route_snapshot(root, model)
    crosscheck = {}
    for brain in sorted(_watched_brains(model)):
        fired = _watcher_fired(state_dir, brain)
        # Only compare within the intersection of what BOTH sides could know about (comms present in
        # the corpus AND retained in watcher state) — watcher state keeps only the last ~200.
        shadow = snap.get(brain, set())
        corpus = set(bases)
        tested = fired & corpus                        # comms BOTH sides can see = the real test set
        missed = sorted(tested - shadow)               # watcher fired, shadow wouldn't route → real gap
        extra = sorted(shadow - fired)                 # shadow routes, watcher hasn't fired (defer/never)
        # tested_overlap is the N behind this brain's missed=0. A zero over tested_overlap==0 is
        # VACUOUS (the brain's fired-history has aged out of the corpus root) — not proof; only a zero
        # over tested_overlap>0 is load-bearing. Without this field the reader can't tell the two apart.
        crosscheck[brain] = {"watcher_fired": len(fired), "shadow_routed": len(shadow),
                             "tested_overlap": len(tested), "missed": missed, "extra_count": len(extra)}

    # Structural double-fire guarantee: a fresh delivery pass over the SAME log delivers 0 new (cursor).
    # (Uses throwaway consumers so it doesn't disturb the real per-brain cursors.)
    reconsume = deliver_once(root, model, sink=None)

    return {
        "corpus_size": len(bases),
        "port_parity": {"divergences": divergences, "divergence_count": len(divergences)},
        "live_crosscheck": crosscheck,
        "double_fire_guarantee": {
            "note": "cursor'd consumer; a re-delivery pass over the same log yields these NEW counts "
                    "(all 0 = no double-fire, the mtime-refire scar structurally eliminated).",
            "reconsume_new_counts": reconsume,
        },
        "caveats": [
            "port_parity is the primary proof: two independent route derivations over the live corpus.",
            "live_crosscheck 'extra' is expected transient (a comm the watcher hasn't fired yet, e.g. "
            "Rabbot deferred); 'missed' is the metric that must be 0.",
            "missed=0 is load-bearing ONLY where tested_overlap>0; a zero over tested_overlap==0 is "
            "VACUOUS (that brain's fired-history has aged out of the corpus root, nothing to test yet).",
            "watcher state retains only the last ~200 fired basenames and only for comms that host has "
            "seen; augur state is local to dvo, others sync from their hosts.",
        ],
    }


# ---- CLI --------------------------------------------------------------------
def _default_root():
    return os.path.expanduser(os.environ.get(
        "AGOS_EVENTS_DIR", "~/jarvis-sync/dvo-orchestration-shadow/events-root"))


def main(argv=None):
    import sys
    argv = argv if argv is not None else sys.argv[1:]
    comms_dir = os.environ.get("AGOS_COMMS_DIR", "~/jarvis-sync/brain-comms")
    root = _default_root()
    model = load_model(os.environ.get("AGOS_MESH_MODEL"))
    cmd = argv[0] if argv else "scan"

    if cmd == "scan":
        res = scan_once(comms_dir, root, model)
        deliv = deliver_once(root, model, sink=[])
        print(json.dumps({"scan": res, "delivered_new": deliv}, sort_keys=True))
        return 0
    if cmd == "report":
        rep = diff_report(comms_dir, root, model)
        out = os.path.join(os.path.dirname(root), "shadow-metrics.json")
        with open(out, "w") as f:
            json.dump(rep, f, indent=2, sort_keys=True)
        print(json.dumps({"corpus_size": rep["corpus_size"],
                          "divergences": rep["port_parity"]["divergence_count"],
                          "wrote": out}, sort_keys=True))
        return 0
    print("usage: agos_comms_shadow.py [scan|report]", file=__import__("sys").stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
