#!/usr/bin/env python3
# tests/agos-comms-live-contract.py — CONTRACT BATTERY for modules/agos_comms_live.py (the CUTOVER layer).
#
# The shadow contract proves the ROUTE DERIVATIONS are faithful. This battery proves the LIVE MECHANICS the
# cutover turns on — the react-on-delta consumer and Saga's cohesion sweep — behave exactly as the
# BUILD-PLAN's cutover criteria require:
#   1. WAKE DELTA      — after emit, wake_pending(brain) returns exactly the NEW summonses routed to brain.
#   2. EXACTLY-ONCE    — a second wake_pending over the SAME log returns [] (the cursor). The mtime-refire
#                        double-fire is structurally impossible, not merely unlikely. THE central win.
#   3. ROUTE FIDELITY  — a broadcast (to-all) wakes only the broadcast brains (scout/geist gap); a rebound
#                        (a brain's own comm) never wakes its author; an un-addressed brain never wakes.
#   4. FRESH DELTA     — a comm that lands AFTER a brain's last wake fires on the next wake (and only then).
#   5. COHESION SWEEP  — Saga sees a request with no done as UNRESOLVED; once its done lands it clears.
#                        Read-only: the sweep advances no cursor (re-runs are stable, don't consume).
#   6. EMIT IDEMPOTENT — re-emitting with no new comm emits 0 (corr_id==basename).
#
# Zero external deps. Exits 0 on all-pass. Locally:
#   PYTHONPATH=modules python3 tests/agos-comms-live-contract.py

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "modules"))
sys.path.insert(0, os.path.dirname(__file__))  # nix sandbox: libs copied alongside the test

import agos_comms_live as L    # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


# The same faithful mesh the shadow battery uses (globs copied from mesh_model.json brains[*].reach).
MODEL = {
    "brains": {
        "rabbot": {"host": "mini", "reach": {"globs": [
            "*-to-rabbot-*.md", "*-to-all-*.md", "*-to-air-rabbot-*.md", "*-to-page-rabbot-*.md"]}},
        "page": {"host": "mini", "reach": {"globs": [
            "*-to-page-*.md", "*-to-all-*.md", "*-to-air-page-*.md"]}},
        "augur": {"host": "dvo", "reach": {"globs": [
            "*-to-augur-*.md", "*-to-all-*.md", "*-to-air-augur-*.md"]}},
        "scout": {"host": "mini", "reach": {"globs": ["*-to-scout-*.md"]}},
        "phoenix": {"host": "mini", "reach": {"globs": ["*-to-phoenix-*.md"]}},
        "geist": {"host": "air", "reach": {"globs": ["*-to-geist-*.md"],
                                           "exclude_globs": ["geist-to-*.md"]}},
        "saga": {"host": "mini", "reach": {"globs": []}},
        "prophet": {"host": "dvo", "reach": {"globs": []}},
    }
}


def _write(comms_dir, name, t):
    p = os.path.join(comms_dir, name)
    with open(p, "w") as f:
        f.write("# " + name + "\n")
    os.utime(p, (t, t))
    return p


def _complete(comms_dir, name):
    """Mark a comm processed the way the live dispatch does — move it into _done/ (keeps a copy of the
    marker there; the emitter's done-pass reads _done/)."""
    done_dir = os.path.join(comms_dir, "_done")
    os.makedirs(done_dir, exist_ok=True)
    dst = os.path.join(done_dir, name)
    with open(dst, "w") as f:
        f.write("done\n")
    return dst


def main():
    tmp = tempfile.mkdtemp(prefix="agos-live-")
    comms = os.path.join(tmp, "brain-comms")
    os.makedirs(os.path.join(comms, "_done"))
    root = os.path.join(tmp, "root")
    t0 = 1_800_000_000

    # --- 1+2+3: emit two single-addressee comms; wake fires exactly the routed brain, once ---------
    _write(comms, "2026-08-01-air-to-augur-alpha.md", t0)
    _write(comms, "2026-08-01-air-to-rabbot-beta.md", t0 + 1)
    res = L.emit_once(comms, root, MODEL)
    check(res == {"emitted": 2, "dones": 0}, "emit_once should emit 2 requests, 0 dones: %r" % (res,))

    augur1 = L.wake_pending(root, "augur")
    check(augur1 == ["2026-08-01-air-to-augur-alpha.md"],
          "augur wakes on exactly its own summons: %r" % (augur1,))
    rabbot1 = L.wake_pending(root, "rabbot")
    check(rabbot1 == ["2026-08-01-air-to-rabbot-beta.md"],
          "rabbot wakes on exactly its own summons: %r" % (rabbot1,))
    # page/scout addressed by neither → no wake.
    check(L.wake_pending(root, "page") == [], "page must not wake on augur/rabbot comms")
    check(L.wake_pending(root, "scout") == [], "scout must not wake on augur/rabbot comms")

    # EXACTLY-ONCE: a second pass over the same log yields nothing (the cursor). No double-fire.
    check(L.wake_pending(root, "augur") == [], "augur re-wake must be empty (cursor / no double-fire)")
    check(L.wake_pending(root, "rabbot") == [], "rabbot re-wake must be empty (cursor / no double-fire)")

    # --- 3: broadcast wakes only the broadcast brains (scout/geist/phoenix gap); rebound drops ------
    _write(comms, "2026-08-01-air-to-all-gamma.md", t0 + 2)          # -> rabbot,page,augur
    _write(comms, "augur-to-rabbot-selfnote-2026-08-01.md", t0 + 3)  # augur-authored -> rabbot only (rebound)
    res2 = L.emit_once(comms, root, MODEL)
    check(res2 == {"emitted": 2, "dones": 0}, "second emit should add 2 requests: %r" % (res2,))

    # augur already consumed alpha; the FRESH delta is only the broadcast (its own selfnote is a rebound).
    augur2 = L.wake_pending(root, "augur")
    check(augur2 == ["2026-08-01-air-to-all-gamma.md"],
          "augur's fresh delta = the broadcast only (rebound dropped): %r" % (augur2,))
    # rabbot's fresh delta = broadcast + augur's note (rebound is dropped for the AUTHOR, not the target).
    rabbot2 = sorted(L.wake_pending(root, "rabbot"))
    check(rabbot2 == ["2026-08-01-air-to-all-gamma.md", "augur-to-rabbot-selfnote-2026-08-01.md"],
          "rabbot's fresh delta = broadcast + the note addressed to it: %r" % (rabbot2,))
    # scout/geist/phoenix are NOT reached by to-all (the documented broadcast_gap).
    check(L.wake_pending(root, "scout") == [], "scout not reached by to-all (broadcast gap)")
    check(L.wake_pending(root, "geist") == [], "geist not reached by to-all (broadcast gap)")
    check(L.wake_pending(root, "phoenix") == [], "phoenix not reached by to-all (broadcast gap)")

    # --- 4: a comm that lands AFTER the last wake fires on the NEXT wake, and only then -------------
    check(L.wake_pending(root, "augur") == [], "augur quiet before a new comm arrives")
    _write(comms, "2026-08-01-mini-to-augur-delta.md", t0 + 4)
    L.emit_once(comms, root, MODEL)
    augur3 = L.wake_pending(root, "augur")
    check(augur3 == ["2026-08-01-mini-to-augur-delta.md"],
          "augur wakes on the newly-arrived comm: %r" % (augur3,))

    # --- 6: emit idempotency — nothing new on disk => 0 emitted -------------------------------------
    check(L.emit_once(comms, root, MODEL) == {"emitted": 0, "dones": 0},
          "re-emit with no new comm must emit nothing")

    # --- 5: cohesion sweep — unresolved request, then resolved when its done lands ------------------
    swept = L.cohesion_sweep(root)
    unresolved_ids = {u["corr_id"] for u in swept["unresolved"]}
    # Every comm we wrote is a request; none has a done yet.
    check("2026-08-01-air-to-augur-alpha.md" in unresolved_ids, "alpha should be unresolved (no done)")
    check("2026-08-01-air-to-all-gamma.md" in unresolved_ids, "gamma should be unresolved (no done)")
    check(swept["done_count"] == 0, "no dones emitted yet: %r" % (swept["done_count"],))
    n_before = swept["unresolved_count"]

    # Complete alpha the way the live mesh does (into _done/), then let the emitter's done-pass see it.
    _complete(comms, "2026-08-01-air-to-augur-alpha.md")
    dres = L.emit_once(comms, root, MODEL)
    check(dres["dones"] == 1, "the done-pass should emit exactly one done: %r" % (dres,))

    swept2 = L.cohesion_sweep(root)
    unresolved2 = {u["corr_id"] for u in swept2["unresolved"]}
    check("2026-08-01-air-to-augur-alpha.md" not in unresolved2, "alpha resolves once its done lands")
    check(swept2["unresolved_count"] == n_before - 1,
          "exactly one chain resolved: %d -> %d" % (n_before, swept2["unresolved_count"]))

    # ADDRESSED vs ORPHAN split: every remaining chain here routes to a real brain (none to _nobody),
    # so Saga's load-bearing 'addressed_unresolved' == the full unresolved set, orphans == 0.
    check(swept2["addressed_unresolved_count"] == swept2["unresolved_count"],
          "all remaining chains are addressed (no orphans in this corpus): %r" % (swept2,))
    check(swept2["orphan_unresolved_count"] == 0, "no orphan (_nobody) chains in this corpus")

    # cohesion is READ-ONLY: it consumed no events, so a brain's wake cursor is untouched by sweeping.
    check(L.wake_pending(root, "augur") == [], "cohesion_sweep must not advance any wake cursor")

    # min_age filter: with a huge floor, nothing is 'old enough' → empty; with 0, all unresolved show.
    check(L.cohesion_sweep(root, min_age_s=10**12)["unresolved_count"] == 0,
          "min_age far in the future filters everything out")
    check(L.cohesion_sweep(root, min_age_s=0)["unresolved_count"] == swept2["unresolved_count"],
          "min_age 0 returns all unresolved")

    print("agos-comms-live-contract: ALL PASS (%d unresolved after 1 done; exactly-once wake verified)"
          % (swept2["unresolved_count"],))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
