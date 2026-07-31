#!/usr/bin/env python3
# tests/agos-comms-shadow-contract.py — CONTRACT BATTERY for modules/agos_comms_shadow.py.
#
# Proves the shadow migration is faithful BEFORE any cutover:
#   1. PORT PARITY  — for every comm, the two independent route derivations agree:
#                       route_from_to(parse_addressees(b))  ==  file_route(b)   (globs).
#   2. ROUTE TRUTH  — file_route() reproduces the documented watcher behavior on every hazard:
#                       single addressee, broadcast (to-all), the broadcast_gap (scout/phoenix/geist
#                       are NOT reached by to-all), rebound (author never self-summons), the combined
#                       air-<brain> glob, and the to-mesh orphan (routes to nobody).
#   3. EMIT IDEMPOTENT — scan_once twice emits each comm exactly once (re-scan → 0 new).
#   4. DONE THREADING  — a comm moved to _done/ yields exactly one done event, corr_id-threaded.
#   5. EXACTLY-ONCE    — the cursor'd shadow consumer delivers each comm to each addressed brain once;
#                        a re-delivery pass yields 0 new (the mtime-refire double-fire, structurally
#                        killed); an orphan comm is delivered to NO brain.
#   6. MACHINE PARTITION — events land in the AUTHOR's host machine-file and merge in mtime order.
#
# Zero external deps. Exits 0 on all-pass. Locally:
#   PYTHONPATH=modules python3 tests/agos-comms-shadow-contract.py

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "modules"))
sys.path.insert(0, os.path.dirname(__file__))  # nix sandbox: libs copied alongside the test

import agos_events as E          # noqa: E402
import agos_comms_shadow as S    # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


# A minimal but FAITHFUL mesh model (globs copied from mesh_model.json's brains[*].reach).
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
        "saga": {"host": "mini", "reach": {"globs": []}},      # no watcher → never summoned
        "prophet": {"host": "dvo", "reach": {"globs": []}},
    }
}

# (basename, expected summoned-brain set) — one per hazard. mtime order is list order.
CORPUS = [
    ("2026-07-31-air-to-augur-alpha.md", {"augur"}),                    # single addressee
    ("2026-07-31-air-to-all-beta.md", {"rabbot", "page", "augur"}),     # broadcast (gap: no scout/geist)
    ("augur-to-all-gamma-2026-07-31.md", {"rabbot", "page"}),           # broadcast + rebound (augur drops)
    ("2026-07-31-page-to-air-rabbot-delta.md", {"rabbot"}),             # combined air-<brain> glob
    ("2026-07-31-air-to-mesh-epsilon.md", set()),                       # orphan → nobody
    ("2026-07-31-rabbot-to-scout-zeta.md", {"scout"}),                  # narrow, no broadcast
    ("2026-07-31-air-to-geist-eta.md", {"geist"}),                      # narrow addressee
    ("geist-to-rabbot-theta-2026-07-31.md", {"rabbot"}),               # geist authors → not self-summoned
]


def _build_corpus():
    root = tempfile.mkdtemp(prefix="agos-comms-shadow-")
    comms = os.path.join(root, "brain-comms")
    os.makedirs(os.path.join(comms, "_done"))
    t0 = 1_780_000_000
    for i, (base, _) in enumerate(CORPUS):
        p = os.path.join(comms, base)
        with open(p, "w") as f:
            f.write("---\nsubject: %s\n---\nbody %d\n" % (base, i))
        os.utime(p, (t0 + i, t0 + i))  # deterministic ascending mtime = arrival order
    events_root = os.path.join(root, "events-root")
    return comms, events_root


def test_port_parity_and_truth():
    for base, expected in CORPUS:
        actor = S.parse_actor(base)
        by_glob = S.file_route(base, MODEL)
        by_to = S.route_from_to(S.parse_addressees(base), actor, MODEL)
        check(by_glob == by_to,
              "PORT PARITY diverged for %s: glob=%r to=%r" % (base, sorted(by_glob), sorted(by_to)))
        check(by_glob == expected,
              "ROUTE TRUTH wrong for %s: got %r want %r" % (base, sorted(by_glob), sorted(expected)))
    # The broadcast_gap, explicitly: to-all reaches ONLY rabbot/page/augur.
    allroute = S.file_route("2026-07-31-air-to-all-beta.md", MODEL)
    check(not (allroute & {"scout", "phoenix", "geist", "saga"}),
          "broadcast_gap violated: to-all reached %r" % sorted(allroute))
    print("1+2. port parity + route truth (all hazards) — PASS")


def test_emit_idempotent():
    comms, root = _build_corpus()
    r1 = S.scan_once(comms, root, MODEL)
    check(r1["emitted"] == len(CORPUS), "first scan emitted %d, want %d" % (r1["emitted"], len(CORPUS)))
    r2 = S.scan_once(comms, root, MODEL)
    check(r2["emitted"] == 0, "re-scan emitted %d, want 0 (not idempotent)" % r2["emitted"])
    # corr_ids are the basenames, unique, one request each.
    reqs = [ev for ev in E.EventLog(root, "dvo").read("comms") if ev["kind"] == "request"]
    corrs = [ev["corr_id"] for ev in reqs]
    check(len(corrs) == len(set(corrs)) == len(CORPUS), "duplicate/again requests: %r" % corrs)
    # `to` never empty: an orphan carries the NOBODY sentinel, not [] (which would broadcast).
    orphan = [ev for ev in reqs if ev["corr_id"] == "2026-07-31-air-to-mesh-epsilon.md"][0]
    check(orphan["to"] == [S.NOBODY], "orphan `to` wrong: %r" % orphan["to"])
    # payload survives (PIN #2): path + hash present.
    check(orphan["payload"]["path"].startswith("brain-comms/") and orphan["payload"]["hash"],
          "payload path/hash missing: %r" % orphan["payload"])
    print("3. emit idempotent + orphan sentinel + payload survives — PASS")


def test_done_threading():
    comms, root = _build_corpus()
    S.scan_once(comms, root, MODEL)
    base = "2026-07-31-air-to-augur-alpha.md"
    # Simulate the live dispatch marking it processed: it appears in _done/.
    with open(os.path.join(comms, "_done", base), "w") as f:
        f.write("done copy\n")
    r = S.scan_once(comms, root, MODEL)
    check(r["dones"] == 1, "expected 1 done emitted, got %d" % r["dones"])
    log = E.EventLog(root, "dvo")
    d = log.await_done(base, "comms", timeout=1.0)
    check(d is not None and d["corr_id"] == base and d["kind"] == "done", "done not threaded: %r" % d)
    r2 = S.scan_once(comms, root, MODEL)
    check(r2["dones"] == 0, "done re-emitted on re-scan: %d" % r2["dones"])
    print("4. done threading (corr_id) + idempotent — PASS")


def test_exactly_once_delivery():
    comms, root = _build_corpus()
    S.scan_once(comms, root, MODEL)
    sink1 = []
    S.deliver_once(root, MODEL, sink=sink1)
    delivered = {}
    for brain, corr in sink1:
        delivered.setdefault(brain, set()).add(corr)
    # Each brain got exactly its addressed comms.
    for brain in S._watched_brains(MODEL):
        want = {base for base, exp in CORPUS if brain in exp}
        got = delivered.get(brain, set())
        check(got == want, "delivery wrong for %s: got %r want %r" % (brain, sorted(got), sorted(want)))
    # The orphan reached NO brain.
    orphan = "2026-07-31-air-to-mesh-epsilon.md"
    check(all(orphan not in s for s in delivered.values()), "orphan was delivered to someone")
    # Re-deliver: 0 new (cursor kills the double-fire).
    sink2 = []
    counts = S.deliver_once(root, MODEL, sink=sink2)
    check(sink2 == [] and all(c == 0 for c in counts.values()),
          "double-fire: re-delivery produced %r / %r" % (sink2, counts))
    print("5. exactly-once delivery + orphan→nobody + no double-fire — PASS")


def test_machine_partition_and_order():
    comms, root = _build_corpus()
    S.scan_once(comms, root, MODEL)
    events_dir = os.path.join(root, "events")
    machines = sorted(f.split(".")[-2] for f in os.listdir(events_dir) if f.endswith(".jsonl"))
    # Authors air/page/rabbot(mini), augur(dvo), geist(air) → host files mini/dvo/air (+ air for air-authored).
    check("mini" in machines and "dvo" in machines and "air" in machines,
          "author-host partition missing expected machines: %r" % machines)
    # The merged read is in mtime (arrival) order — our corpus mtimes ascend with list order.
    reqs = [ev for ev in E.EventLog(root, "dvo").read("comms") if ev["kind"] == "request"]
    order = [ev["corr_id"] for ev in reqs]
    check(order == [b for b, _ in CORPUS], "merge order not arrival(mtime) order: %r" % order)
    print("6. author-host machine partition + arrival-order merge — PASS")


def main():
    test_port_parity_and_truth()
    test_emit_idempotent()
    test_done_threading()
    test_exactly_once_delivery()
    test_machine_partition_and_order()
    print("\nagos-comms-shadow contract battery: ALL PASS")


if __name__ == "__main__":
    main()
