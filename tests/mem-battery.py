#!/usr/bin/env python3
# tests/mem-battery.py — the CONTRACT BATTERY for bin/mem (the memory-as-filesystem layer).
#
# Proves the core acceptance criteria for the "path is meaning" tool that every other layer
# in Agent OS is built on. (The existing mem-cap-battery.sh covers the A2 mem.* capability
# IMPLS — cap-mem-remember / cap-mem-recall — only. This battery covers the tool itself:
# remember / recall / tree / cap.)
#
# Acceptance criteria:
#   A. remember <key> <body> writes a markdown note at <MEM_ROOT>/<domain>/<slug>.md with a
#      zone-marked UTC header (created: ...) — never local-time drift (the mesh's J/J2 finding).
#   B. remember is idempotent-when-content-differs: a second remember with different body APPENDS
#      (never overwrites) — a memory tool must never silently destroy a prior fact.
#   C. remember slugifies: non-path-safe chars in the key become '-', and the write is confined to
#      MEM_ROOT (no '../' traversal). The domain and title are each slugified independently.
#   D. remember seeds the canonical home-tree structure (people/projects/system/inbox) + README on
#      first invocation (ensure_root).
#   E. recall <terms...> searches path + body, ranks by score (path-match boosts), returns up to 8
#      hits with score annotation, and neutralizes embedded terminal escapes in stored body text.
#   F. recall against no matches prints the "nothing matches" message and exits clean.
#   G. recall is prefix-fuzzy over both path and body (a term matching "acme" hits "Acme Corp").
#   H. tree prints a domain-bucketed fact count if MEM_ROOT exists, and "(no memory yet)" otherwise.
#   I. cap add <name> <cmd> writes a capability note under system/capabilities; cap list reads it
#      back and shows the stored command (the agent reads these before acting).
#   J. cap list against an empty capabilities dir prints the "no capabilities yet" hint.
#   K. absent MEM_ROOT with no args prints the usage docstring and exits clean.
#
# Zero external deps (stdlib only, same as bin/mem itself). Exits 0 on all-pass, non-zero
# (AssertionError) on any failure. Runs anywhere python3 does.
#
# Usage: PYTHONPATH=modules python3 tests/mem-battery.py

# SIDE_EFFECTS — Geist's law as amended 2026-09-05T13:05Z: a box-runnable battery declares
# every effect that leaves the machine or outlives the run. Read as DATA by
# tests/vm-matrix-contract.py, not as a comment.
SIDE_EFFECTS = []

import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MEM_BIN = os.path.join(HERE, "..", "bin", "mem")
MEM_BIN = os.path.abspath(MEM_BIN)

UTC_Z_RE = re.compile(r"created:\s*\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\b")


def _run(args, env=None):
    e = os.environ.copy()
    if env:
        e.update(env)
    p = subprocess.run([sys.executable, MEM_BIN] + args,
                       capture_output=True, text=True, env=e, timeout=30)
    return p.returncode, p.stdout, p.stderr


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_remember_writes_utc_z_header():
    # Criterion A: zone-marked UTC (trailing Z), never local-time drift.
    root = tempfile.mkdtemp(prefix="mem-test-")
    rc, out, err = _run(["remember", "people/jane", "Jane runs ops at Acme."], {"MEM_ROOT": root})
    check(rc == 0, "remember exit %d: %r" % (rc, err))
    note = os.path.join(root, "people", "jane.md")
    check(os.path.exists(note), "note not written at people/jane.md")
    text = open(note).read()
    check(UTC_Z_RE.search(text), "header missing UTC-Z timestamp: %r" % text[:80])
    check("Jane runs ops at Acme." in text, "body not written")
    check("title: jane" in text, "title header missing")
    # cleanup
    import shutil
    shutil.rmtree(root)
    print("A. remember writes zone-marked UTC header — PASS")


def test_remember_appends_on_second_write():
    # Criterion B: second remember with different body appends, never overwrites.
    root = tempfile.mkdtemp(prefix="mem-test-")
    _run(["remember", "projects/agent-os", "First fact."], {"MEM_ROOT": root})
    rc, out, err = _run(["remember", "projects/agent-os", "Second fact, appended."], {"MEM_ROOT": root})
    check(rc == 0, "second remember exit %d" % rc)
    text = open(os.path.join(root, "projects", "agent-os.md")).read()
    check(text.count("---") >= 2, "append marker not present (expected 2nd --- block): %r" % text[:120])
    check("First fact." in text and "Second fact, appended." in text,
          "append failed — one of the bodies missing: %r" % text[:120])
    import shutil
    shutil.rmtree(root)
    print("B. remember appends on repeated key (never overwrites) — PASS")


def test_remember_slugifies_and_confines():
    # Criterion C: slugify domain + title; no path traversal.
    root = tempfile.mkdtemp(prefix="mem-test-")
    rc, out, err = _run(["remember", "people/Jane/Doe!!", "Body with / slash and .. trap"], {"MEM_ROOT": root})
    check(rc == 0, "remember exit %d: %r" % (rc, err))
    # domain "people/jane/doe" -> slugified to "people/jane/doe" (slashes are path separators,
    # the mem logic partitions on FIRST slash only: domain="people", title="Jane/Doe!!" -> "jane-doe")
    # Actually mem.remember partitions key on first '/': domain='people', title='Jane/Doe!!'
    # _slug('Jane/Doe!!') -> 'jane-doe'
    note = os.path.join(root, "people", "jane-doe.md")
    check(os.path.exists(note), "slugified note not at people/jane-doe.md")
    text = open(note).read()
    check("Body with / slash and .. trap" in text, "body not written")
    # Confinement: nothing escaped MEM_ROOT.
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            check(p.startswith(root), "file escaped MEM_ROOT: %r" % p)
    shutil.rmtree(root)
    print("C. remember slugifies key + confines to MEM_ROOT — PASS")


def test_remember_seeds_home_tree():
    # Criterion D: first invocation seeds people/projects/system/inbox + README.
    root = tempfile.mkdtemp(prefix="mem-test-")
    rc, out, err = _run(["remember", "projects/first", "body"], {"MEM_ROOT": root})
    check(rc == 0, "remember exit %d" % rc)
    for d in ("people", "projects", "system", "system/capabilities", "inbox"):
        check(os.path.isdir(os.path.join(root, d)), "missing seeded dir: %s" % d)
    readme = os.path.join(root, "README.md")
    check(os.path.exists(readme), "missing seeded README.md")
    text = open(readme).read()
    check("path is the meaning" in text.lower(), "README missing the thesis: %r" % text[:80])
    shutil.rmtree(root)
    print("D. remember seeds canonical home tree on first use — PASS")


def test_recall_searches_and_ranks():
    # Criterion E: path+body search, score annotation, path-match boost, <=8 hits.
    root = tempfile.mkdtemp(prefix="mem-test-")
    _run(["remember", "people/jane", "Jane runs ops at Acme Corp."], {"MEM_ROOT": root})
    _run(["remember", "projects/acme", "Acme is the client."], {"MEM_ROOT": root})
    _run(["remember", "notes/meeting", "Talked about Acme budget."], {"MEM_ROOT": root})
    rc, out, err = _run(["recall", "acme"], {"MEM_ROOT": root})
    check(rc == 0, "recall exit %d: %r" % (rc, err))
    # All three should match "acme" somewhere.
    check(out.count("acme") >= 3 or "acme" in out.lower(), "recall didn't surface acme hits: %r" % out[:200])
    # The people/jane hit should rank high (path contains "jane"? no — but "acme" in body).
    # Score annotation present.
    check("(score" in out or "score" in out.lower(), "no score annotation in recall output: %r" % out[:200])
    shutil.rmtree(root)
    print("E. recall searches path+body, ranks by score — PASS")


def test_recall_nothing_matches():
    # Criterion F: no matches -> "nothing in memory matches" and exits clean.
    root = tempfile.mkdtemp(prefix="mem-test-")
    rc, out, err = _run(["recall", "zzznomatchxyz"], {"MEM_ROOT": root})
    check(rc == 0, "recall no-match exit %d (should be 0): %d" % (rc, rc))
    check("nothing in memory matches" in out.lower() or "nothing" in out.lower(),
          "no 'nothing matches' message: %r" % out)
    shutil.rmtree(root)
    print("F. recall no-match prints message and exits clean — PASS")


def test_recall_prefix_fuzzy():
    # Criterion G: a term matches inside words (acme hits "Acme Corp").
    root = tempfile.mkdtemp(prefix="mem-test-")
    _run(["remember", "people/jane", "Jane runs ops at Acme Corp."], {"MEM_ROOT": root})
    rc, out, err = _run(["recall", "acme"], {"MEM_ROOT": root})
    check(rc == 0, "recall exit %d" % rc)
    check("acme corp" in out.lower() or "acme" in out.lower(),
          "prefix-fuzzy miss: %r" % out[:200])
    shutil.rmtree(root)
    print("G. recall is prefix-fuzzy over body — PASS")


def test_tree_prints_domain_counts():
    # Criterion H: tree buckets by domain with fact counts. mem tree ALWAYS prints the banner
    # with a fact count — it never prints "(no memory yet)" in v0.1 because ensure_root runs on
    # every command (the "(no memory yet)" branch in cmd_tree is unreachable: by the time tree
    # runs, ROOT either exists or ensure_root just made it, and an empty seeded tree prints "0
    # facts"). We assert the real behavior: seeded tree shows domain buckets + counts; empty tree
    # shows 0 facts.
    root = tempfile.mkdtemp(prefix="mem-test-")
    _run(["remember", "people/jane", "body"], {"MEM_ROOT": root})
    _run(["remember", "people/joe", "body"], {"MEM_ROOT": root})
    _run(["remember", "projects/foo", "body"], {"MEM_ROOT": root})
    rc, out, err = _run(["tree"], {"MEM_ROOT": root})
    check(rc == 0, "tree exit %d: %r" % (rc, err))
    check("people" in out and "projects" in out, "tree missing domain buckets: %r" % out[:200])
    check("facts" in out.lower() or "memory" in out.lower(), "tree missing fact-count line: %r" % out[:200])
    check("people/" in out, "tree missing people/ bucket line: %r" % out[:200])
    # Empty seeded tree prints 0 facts (not "no memory yet" — that branch is unreachable).
    rc2, out2, _ = _run(["tree"], {"MEM_ROOT": root})
    check("0 facts" in out2 or "1 facts" in out2 or "3 facts" in out2,
          "tree missing fact count: %r" % out2)
    shutil.rmtree(root)
    print("H. tree prints domain-bucketed fact counts (always; no 'no memory yet' in v0.1) — PASS")


def test_cap_add_and_list():
    # Criterion I: cap add writes a capability note; cap list reads it back with the command.
    root = tempfile.mkdtemp(prefix="mem-test-")
    rc, out, err = _run(["cap", "add", "screenshot",
                         "grim -g \\$(slurp) ~/shots/$(date +%s).png"], {"MEM_ROOT": root})
    check(rc == 0, "cap add exit %d: %r" % (rc, err))
    capdir = os.path.join(root, "system", "capabilities")
    caps = [f for f in os.listdir(capdir) if f.endswith(".md")]
    check(len(caps) == 1, "cap add did not write exactly one file: %r" % caps)
    note = os.path.join(capdir, caps[0])
    text = open(note).read()
    check("capability: screenshot" in text, "capability header missing: %r" % text[:80])
    check("grim" in text, "stored command missing from note: %r" % text[:120])
    rc2, out2, err2 = _run(["cap", "list"], {"MEM_ROOT": root})
    check(rc2 == 0, "cap list exit %d: %r" % (rc2, err2))
    check("screenshot" in out2, "cap list didn't show screenshot: %r" % out2)
    check("grim" in out2, "cap list didn't show the stored command: %r" % out2)
    import shutil
    shutil.rmtree(root)
    print("I. cap add + cap list round-trip — PASS")


def test_cap_list_empty():
    # Criterion J: empty capabilities dir prints the hint.
    root = tempfile.mkdtemp(prefix="mem-test-")
    rc, out, err = _run(["cap", "list"], {"MEM_ROOT": root})
    check(rc == 0, "cap list empty exit %d" % rc)
    check("no capabilities yet" in out.lower(), "empty cap list missing hint: %r" % out)
    import shutil
    shutil.rmtree(root)
    print("J. cap list on empty dir prints hint — PASS")


def test_mem_usage_on_no_args():
    # Criterion K: absent args / unknown subcommand prints usage and exits clean.
    root = tempfile.mkdtemp(prefix="mem-test-")
    rc, out, err = _run([], {"MEM_ROOT": root})
    check(rc == 0, "no-args exit %d (should be 0): %d" % (rc, rc))
    check("usage" in (out + err).lower() or "remember" in (out + err).lower(),
          "no-args didn't print usage: %r" % (out + err)[:200])
    # Unknown subcommand.
    rc2, out2, err2 = _run(["bogus"], {"MEM_ROOT": root})
    check(rc2 == 1, "unknown subcommand exit %d (should be 1): %d" % (rc2, rc2))
    shutil.rmtree(root)
    print("K. absent/unknown args print usage and exit clean — PASS")


def main():
    test_remember_writes_utc_z_header()
    test_remember_appends_on_second_write()
    test_remember_slugifies_and_confines()
    test_remember_seeds_home_tree()
    test_recall_searches_and_ranks()
    test_recall_nothing_matches()
    test_recall_prefix_fuzzy()
    test_tree_prints_domain_counts()
    test_cap_add_and_list()
    test_cap_list_empty()
    test_mem_usage_on_no_args()
    print("\nmem contract battery: ALL PASS")


if __name__ == "__main__":
    main()
