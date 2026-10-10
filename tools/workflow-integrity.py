#!/usr/bin/env python3
"""workflow-integrity — does a PR's workflow tree define a REQUIRED check name twice?

Branch protection requires five contexts (REQUIRED below), each pinned to the GitHub Actions app.
Pinning stops a forged commit status; it does NOT stop a PR that adds a second workflow job with a
required name: that job comes from the same app, and its green can satisfy the requirement while
the real job is red. This repo has one collaborator, so a code-owner review cannot close it (no one
could approve). This check is the interim control (Geist ruling, 2026-10-10):

  FAIL     a required name is defined more than once anywhere in the head's workflow files, or a
           head workflow file cannot be parsed (fail closed: we cannot tell what it defines).
  ANNOUNCE a required job's body differs from base, or a required job is missing from head.
           Announced, not blocked: blocking would block ordinary CI maintenance under one identity.

LIMIT, stated on purpose: a PR can add its own job named `workflow-integrity` and race this one.
This raises the bar against mistakes and injected instructions, not against someone holding the
admin token. The complete control needs a second identity (asked of Dillon).

Head content is read through the contents API as DATA. Nothing from the PR is checked out or run.
The workflow running this file is `pull_request_target`, so this file is always the BASE copy.

Usage:
  workflow-integrity.py --repo OWNER/NAME --base SHA --head SHA [--report FILE]   (needs `gh`)
  workflow-integrity.py --selftest
  workflow-integrity.py --check-tree REPO_DIR
"""
import json, subprocess, sys

import yaml  # a missing pyyaml must fail loudly, never degrade to "no jobs found"

REQUIRED = ("gate", "flake-check", "installer-honesty", "pin-freshness", "vm-tests-all")
WF_DIR = ".github/workflows"


def contexts(text):
    """{check-context-name: job-body} for one workflow file. Raises on unparseable input."""
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise ValueError("top level is not a mapping")
    jobs = doc.get("jobs") or {}
    if not isinstance(jobs, dict):
        raise ValueError("`jobs` is not a mapping")
    out = []
    for jid, body in jobs.items():
        name = body.get("name") if isinstance(body, dict) else None
        out.append((str(name) if isinstance(name, str) else str(jid), body))
    return out


def judge(base_files, head_files):
    """files: {path: text}. Returns (fail_reasons, announcements)."""
    fails, notes = [], []
    defs = {}  # name -> [(path, body)]
    for path, text in sorted(head_files.items()):
        try:
            for name, body in contexts(text):
                defs.setdefault(name, []).append((path, body))
        except Exception as e:  # noqa: BLE001 — any parse failure is a fail-closed verdict
            fails.append(f"{path}: cannot parse ({e.__class__.__name__}: {e}) — cannot tell what it defines")
    base = {}
    for path, text in base_files.items():
        try:
            for name, body in contexts(text):
                base.setdefault(name, []).append((path, body))
        except Exception:  # noqa: BLE001 — a broken base is not this PR's fault; compare what parses
            pass
    norm = lambda b: json.dumps(b, sort_keys=True, default=str)
    for name in REQUIRED:
        h = defs.get(name, [])
        if len(h) > 1:
            fails.append(f"required check `{name}` is defined {len(h)} times: " + ", ".join(p for p, _ in h))
        elif not h:
            notes.append(f"required check `{name}` is not defined in head (merges will block on it)")
        else:
            b = base.get(name, [])
            if len(b) == 1 and (b[0][0] != h[0][0] or norm(b[0][1]) != norm(h[0][1])):
                where = h[0][0] if b[0][0] == h[0][0] else f"{b[0][0]} -> {h[0][0]}"
                notes.append(f"required job `{name}` changed ({where}) — read the diff before merging")
    return fails, notes


def gh_tree(repo, sha):
    ls = subprocess.run(["gh", "api", f"repos/{repo}/contents/{WF_DIR}?ref={sha}"],
                        capture_output=True, text=True)
    if ls.returncode != 0:
        # The directory being GONE at this sha is a real state (a PR deleting every workflow) and
        # must reach judge() as an empty tree, so the "required check missing" notes get posted.
        # Any other API failure still raises: we cannot vouch for a tree we could not read.
        if "HTTP 404" in ls.stderr:
            return {}
        raise RuntimeError(f"gh api listing {WF_DIR}@{sha} failed: {ls.stderr.strip()[:300]}")
    files = {}
    for ent in json.loads(ls.stdout):
        if ent.get("type") == "file" and ent["name"].endswith((".yml", ".yaml")):
            r = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github.raw",
                                f"repos/{repo}/contents/{ent['path']}?ref={sha}"],
                               capture_output=True, text=True, check=True)
            files[ent["path"]] = r.stdout
    return files


def selftest():
    ok = lambda jobs: yaml.safe_dump({"on": "push", "jobs": jobs})
    base = {f"{WF_DIR}/a.yml": ok({"gate": {"runs-on": "x"}, "flake-check": {"runs-on": "x"},
                                     "installer-honesty": {"runs-on": "x"}, "pin-freshness": {"runs-on": "x"},
                                     "vm-tests-all": {"runs-on": "x"}})}
    arms = []
    def arm(label, head, want_fail, want_note=None):
        f, n = judge(base, head)
        good = bool(f) == want_fail and (want_note is None or any(want_note in x for x in n))
        arms.append((label, good, f, n))
    # A1 permitting twin: the unchanged tree is GREEN (without it a checker that fails everything passes)
    arm("A1 unchanged tree passes", dict(base), False)
    # A2 the attack: a second file defines `gate`
    arm("A2 duplicate job id in a new file fails", {**base, f"{WF_DIR}/evil.yml": ok({"gate": {"runs-on": "x"}})}, True)
    # A3 the same attack spelled through `name:` instead of the job id
    arm("A3 duplicate via job name: fails", {**base, f"{WF_DIR}/evil.yml": ok({"x1": {"name": "flake-check"}})}, True)
    # A4 flow-style YAML: a line-based parser would miss this; the YAML parser must not
    arm("A4 flow-style duplicate fails", {**base, f"{WF_DIR}/evil.yml": "on: push\njobs: {vm-tests-all: {runs-on: x}}\n"}, True)
    # A5 a new NON-required job is fine (ordinary CI growth must stay green)
    arm("A5 new unrelated job passes", {**base, f"{WF_DIR}/b.yml": ok({"lint": {"runs-on": "x"}})}, False)
    # A6 editing a required job's body is ANNOUNCED, not failed
    edited = {f"{WF_DIR}/a.yml": base[f"{WF_DIR}/a.yml"].replace("gate:\n    runs-on: x", "gate:\n    runs-on: y")}
    arm("A6 required body edit announces, passes", edited, False, "required job `gate` changed")
    # A7 unparseable head file fails closed
    arm("A7 unparseable head fails closed", {**base, f"{WF_DIR}/bad.yml": "jobs: [unclosed\n"}, True)
    # A8 vacuity arm: a head with NO workflow files must not read as clean — every required name is announced missing
    f, n = judge(base, {})
    arms.append(("A8 empty head announces all five missing", not f and len(n) == 5, f, n))
    bad = [a for a in arms if not a[1]]
    for label, good, f, n in arms:
        print(("PASS " if good else "FAIL ") + label + ("" if good else f"  fails={f} notes={n}"))
    print(f"{len(arms) - len(bad)}/{len(arms)}")
    return 1 if bad else 0


def main(argv):
    if argv[1:] == ["--selftest"]:
        return selftest()
    if len(argv) == 3 and argv[1] == "--check-tree":
        # Contract arm, run by the flake check on the real tree: every REQUIRED name is defined
        # exactly once and the tree parses. Catches REQUIRED drifting from the actual jobs.
        import glob, os
        tree = {f: open(f).read() for f in glob.glob(os.path.join(argv[2], WF_DIR, "*.y*ml"))}
        fails, notes = judge(tree, tree)
        for x in fails + notes: print(x)
        print(f"tree: {len(tree)} workflow files, {'OK' if not (fails or notes) else 'DRIFT'}")
        return 1 if (fails or notes or not tree) else 0
    args = dict(zip(argv[1::2], argv[2::2]))
    if not {"--repo", "--base", "--head"} <= args.keys() or len(argv[1:]) % 2:
        print(__doc__, file=sys.stderr); return 2
    fails, notes = judge(gh_tree(args["--repo"], args["--base"]), gh_tree(args["--repo"], args["--head"]))
    lines = [f"- FAIL: {x}" for x in fails] + [f"- note: {x}" for x in notes]
    report = "\n".join(lines) or "- no required check is redefined and no required job changed"
    print(report)
    if "--report" in args and (fails or notes):
        with open(args["--report"], "w") as fh:
            fh.write("**workflow-integrity** (this PR edits `.github/`)\n\n" + report + "\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
