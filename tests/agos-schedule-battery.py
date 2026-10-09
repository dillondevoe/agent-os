#!/usr/bin/env python3
# tests/agos-schedule-battery.py — CONTRACT BATTERY for bin/agos-schedule (installs an approved
# app's manifest `schedule` as a systemd user timer, docs/design/app-manifest.md §7 item 2).
#
# The installer is a clock in front of agos-run and nothing more. Every arm that must refuse is
# paired with the file-system evidence that nothing was written or activated. systemctl is a
# fake on PATH that records its argv, so the activation calls are asserted, not assumed.
#
# Acceptance criteria:
#   A. CONTROL: a manifest with an empty schedule is refused (exit 6); no units written, no
#      systemctl call.
#   B. CONTROL: a scheduled but UNAPPROVED manifest is refused (exit 4); no units, no call.
#   C. Approved + scheduled: both units exist; ExecStart is `<abs agos-run> <appdir>` and nothing
#      else; OnCalendar equals the manifest schedule; the timer's Unit= names the service;
#      X-AgentOS-Sha equals the manifest sha; systemctl was called `--user daemon-reload` then
#      `--user enable --now agos-app-<name>.timer`, then `restart` (re-arm), in that order; the
#      service pins Environment=PATH so bwrap/python3 are found under a bare user manager.
#   D. `list` shows the app, its schedule, the sha prefix and "ok".
#   E. Edit the manifest after install: `list` reports STALE (manifest changed). Restore it and
#      delete the approval: `list` reports STALE (revoked). Restore the approval: "ok" again.
#   F. `remove <name>`: systemctl `disable --now` was called, both unit files are gone,
#      daemon-reload followed, `list` says none. `remove` of an unknown name exits 1; a name
#      with a slash or dots exits 2 and touches nothing.
#   G. CONTROL: systemctl absent from PATH: install refuses (exit 5) and writes no units.
#   H. CONTROL: a unit file under our name that we did not write (no X-AgentOS-Sha) is never
#      removed by `remove` (exit 7), never listed, and never overwritten by `install` (exit 7,
#      bytes identical afterwards).
#   I. CONTROL: an invalid manifest (credential path, R5) is refused (exit 3) naming the rule.
#   J. Paths with a space, a double quote and a % (HOME and the runner's dir): ExecStart args are
#      double-quoted with " escaped and % doubled, so systemd's splitter and specifier expansion
#      see the real path; the unit parses back to the exact two argv words.
#   K. A re-install whose activation fails restores the previous units byte for byte (the old
#      timer keeps working); a FIRST install whose activation fails leaves no units.
#   L. CONTROL: with systemd-analyze absent, a schedule carrying a newline (which would start a
#      new unit key) or a % is refused (exit 6) and nothing is written.
#
# stdlib only. Exit 0 all-pass; AssertionError otherwise.
SIDE_EFFECTS = []  # scratch HOME/XDG_CONFIG_HOME under mktemp, removed at exit

import hashlib, json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.abspath(os.path.join(HERE, "..", "bin"))
SCHED = os.path.join(BIN, "agos-schedule")
RUNNER = os.path.join(BIN, "agos-run")
TMP = tempfile.mkdtemp(prefix="agos-schedule-battery.")
HOME = os.path.join(TMP, "home")
XDG = os.path.join(HOME, ".config")
UNITS = os.path.join(XDG, "systemd", "user")
APPS = os.path.join(HOME, ".local/share/agent-os/apps")
APPROVALS = os.path.join(HOME, ".local/state/agent-os/approvals.json")
FAKE = os.path.join(TMP, "fakebin")
LOG = os.path.join(TMP, "systemctl.log")
os.makedirs(FAKE)
with open(os.path.join(FAKE, "systemctl"), "w") as fh:
    fh.write('#!/bin/sh\nprintf "%s\\n" "$*" >> "%s"\nexit 0\n' % ("%s", LOG))
os.chmod(os.path.join(FAKE, "systemctl"), 0o755)
ENV = dict(os.environ, HOME=HOME, XDG_CONFIG_HOME=XDG, AGOS_RUN_NO_SYSTEMD="1")
ENV["PATH"] = FAKE + os.pathsep + ENV.get("PATH", "")
os.makedirs(os.path.join(HOME, "notes"), exist_ok=True)
open(os.path.join(HOME, "notes/todo.md"), "w").write("todo\n")

SCHEDULE = "*-*-* 07:00:00"
GOOD = {"name": "daily-summary", "version": "0.1", "entry": ["sh", "-c", "cat $HOME/notes/todo.md"],
        "files": [{"path": "~/notes/todo.md", "mode": "r"}], "network": [], "devices": [],
        "limits": {"cpu_pct": 25, "mem_mb": 128, "wall_s": 60}, "schedule": SCHEDULE}


def check(c, msg):
    if not c: raise AssertionError(msg)


def mk(manifest, name=None):
    name = name or manifest["name"]
    d = os.path.join(APPS, name); os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "manifest.json"), "w") as fh: json.dump(manifest, fh)
    return d


def sha_of(m):
    return hashlib.sha256(json.dumps(m, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def approve(m):
    os.makedirs(os.path.dirname(APPROVALS), mode=0o700, exist_ok=True)
    db = {}
    if os.path.exists(APPROVALS):
        db = json.load(open(APPROVALS))
    db[sha_of(m)] = {"name": m["name"], "approved_at": "2026-10-09T00:00:00Z"}
    json.dump(db, open(APPROVALS, "w"))


def run(*args, env=None):
    return subprocess.run([sys.executable, SCHED] + list(args), capture_output=True, text=True, env=env or ENV)


def calls():
    return open(LOG).read().splitlines() if os.path.exists(LOG) else []


def units(name):
    return [p for p in (os.path.join(UNITS, "agos-app-%s.service" % name), os.path.join(UNITS, "agos-app-%s.timer" % name)) if os.path.exists(p)]


def unit_kv(path):
    out = {}
    for line in open(path):
        line = line.strip()
        if "=" in line and not line.startswith(("#", "[")):
            k, v = line.split("=", 1); out[k.strip()] = v.strip()
    return out


try:
    # A: empty schedule
    noschd = dict(GOOD, schedule="")
    d = mk(noschd); approve(noschd)
    r = run("install", d)
    check(r.returncode == 6 and "no schedule" in r.stderr, "A: expected exit 6 no schedule: %d %s" % (r.returncode, r.stderr))
    check(not units("daily-summary") and not calls(), "A: units or systemctl calls on refusal")
    print("A. empty schedule refused, nothing written")

    # B: unapproved
    d = mk(GOOD)  # overwrites manifest; GOOD's sha is not approved yet
    r = run("install", d)
    check(r.returncode == 4 and "not approved" in r.stderr, "B: expected exit 4: %d %s" % (r.returncode, r.stderr))
    check(not units("daily-summary") and not calls(), "B: units or systemctl calls on refusal")
    print("B. unapproved manifest refused, nothing written")

    # C: install
    approve(GOOD)
    r = run("install", d)
    check(r.returncode == 0, "C: install failed: %s" % r.stderr)
    svc, tmr = units("daily-summary")
    check(svc.endswith(".service") and tmr.endswith(".timer"), "C: both units expected: %r" % units("daily-summary"))
    S, T = unit_kv(svc), unit_kv(tmr)
    check(S["ExecStart"] == '"%s" "%s"' % (RUNNER, os.path.realpath(d)), "C: ExecStart %r" % S["ExecStart"])
    check(S.get("Environment", "").startswith('PATH="' + FAKE), "C: PATH not pinned: %r" % S.get("Environment"))
    check(S["Type"] == "oneshot" and S["TimeoutStartSec"] == "120", "C: service shape %r" % S)
    check(T["OnCalendar"] == SCHEDULE and T["Unit"] == "agos-app-daily-summary.service", "C: timer shape %r" % T)
    check(T["X-AgentOS-Sha"] == sha_of(GOOD) == S["X-AgentOS-Sha"], "C: sha not recorded")
    check(calls() == ["--user daemon-reload", "--user enable --now agos-app-daily-summary.timer", "--user restart agos-app-daily-summary.timer"], "C: systemctl calls %r" % calls())
    print("C. units written; ExecStart is agos-run only, quoted; daemon-reload, enable --now, restart")

    # D: list ok
    r = run("list")
    check(r.returncode == 0 and "daily-summary" in r.stdout and SCHEDULE in r.stdout and sha_of(GOOD)[:8] in r.stdout and " ok" in r.stdout, "D: list %r" % r.stdout)
    print("D. list shows app, schedule, sha, ok")

    # E: staleness
    edited = dict(GOOD, version="0.2"); mk(edited)
    r = run("list"); check("STALE: manifest changed" in r.stdout, "E1: %r" % r.stdout)
    mk(GOOD)
    saved = open(APPROVALS).read(); json.dump({}, open(APPROVALS, "w"))
    r = run("list"); check("STALE: approval revoked" in r.stdout, "E2: %r" % r.stdout)
    open(APPROVALS, "w").write(saved)
    r = run("list"); check(" ok" in r.stdout and "STALE" not in r.stdout, "E3: %r" % r.stdout)
    print("E. list flags a changed manifest and a revoked approval as STALE")

    # F: remove
    os.unlink(LOG)
    r = run("remove", "daily-summary")
    check(r.returncode == 0 and not units("daily-summary"), "F: remove %d %s" % (r.returncode, r.stderr))
    check(calls() == ["--user disable --now agos-app-daily-summary.timer", "--user daemon-reload"], "F: calls %r" % calls())
    r = run("list"); check("no app timers" in r.stdout, "F: list after remove %r" % r.stdout)
    r = run("remove", "daily-summary"); check(r.returncode == 1, "F: unknown remove should exit 1: %d" % r.returncode)
    before = sorted(os.listdir(UNITS))
    for bad in ("../x", "a/b", "..", "Daily"):
        r = run("remove", bad); check(r.returncode == 2, "F: bad name %r exit %d" % (bad, r.returncode))
    check(sorted(os.listdir(UNITS)) == before, "F: bad names touched the unit dir")
    print("F. remove disables, deletes, reloads; unknown=1; bad names=2 and untouched")

    # G: no systemctl
    env2 = dict(ENV, PATH=os.path.join(TMP, "empty")); os.makedirs(env2["PATH"], exist_ok=True)
    r = run("install", d, env=env2)
    check(r.returncode == 5 and "systemctl" in r.stderr and not units("daily-summary"), "G: %d %s" % (r.returncode, r.stderr))
    print("G. systemctl absent: refused, nothing written")

    # H: foreign unit under our name
    foreign = os.path.join(UNITS, "agos-app-foreign.timer")
    open(foreign, "w").write("[Timer]\nOnCalendar=daily\n")
    r = run("list"); check("foreign" not in r.stdout, "H: foreign unit listed: %r" % r.stdout)
    r = run("remove", "foreign"); check(r.returncode == 7 and os.path.exists(foreign), "H: foreign unit removed or wrong exit %d" % r.returncode)
    fm = dict(GOOD, name="foreign"); fd = mk(fm); approve(fm)
    r = run("install", fd)
    check(r.returncode == 7 and open(foreign).read() == "[Timer]\nOnCalendar=daily\n" and not os.path.exists(os.path.join(UNITS, "agos-app-foreign.service")),
          "H: install overwrote a foreign unit or wrong exit %d %s" % (r.returncode, r.stderr))
    print("H. a unit not written by agos-schedule is neither listed, removed, nor overwritten")

    # I: invalid manifest
    bad = dict(GOOD, files=[{"path": "~/.ssh/config", "mode": "r"}]); mk(bad); approve(bad)
    r = run("install", d)
    check(r.returncode == 3 and "R5" in r.stderr and not units("daily-summary"), "I: %d %s" % (r.returncode, r.stderr))
    print("I. invalid manifest refused naming the rule")

    # J: quoting. A second HOME with awkward characters, and a copy of bin/ under an awkward dir.
    H2 = os.path.join(TMP, 'odd home 100%"x'); X2 = os.path.join(H2, ".config")
    B2 = os.path.join(TMP, 'odd bin %q', "bin"); os.makedirs(B2)
    for b in ("agos-run", "agos-schedule"):
        shutil.copy(os.path.join(BIN, b), os.path.join(B2, b)); os.chmod(os.path.join(B2, b), 0o755)
    env3 = dict(ENV, HOME=H2, XDG_CONFIG_HOME=X2)
    apps2 = os.path.join(H2, ".local/share/agent-os/apps/daily-summary"); os.makedirs(apps2)
    g2 = dict(GOOD, files=[]); json.dump(g2, open(os.path.join(apps2, "manifest.json"), "w"))
    os.makedirs(os.path.join(H2, ".local/state/agent-os"), mode=0o700)
    json.dump({sha_of(g2): {"name": "daily-summary", "approved_at": "x"}}, open(os.path.join(H2, ".local/state/agent-os/approvals.json"), "w"))
    r = subprocess.run([sys.executable, os.path.join(B2, "agos-schedule"), "install", apps2], capture_output=True, text=True, env=env3)
    check(r.returncode == 0, "J: install under odd paths failed: %s" % r.stderr)
    ex = unit_kv(os.path.join(X2, "systemd/user/agos-app-daily-summary.service"))["ExecStart"]
    # parse it back the way systemd does: double-quoted words, \" escapes, %% -> %
    import re as _re
    words = [w.replace('\\"', '"').replace("%%", "%") for w in _re.findall(r'"((?:[^"\\]|\\.)*)"', ex)]
    check(words == [os.path.join(B2, "agos-run"), os.path.realpath(apps2)], "J: ExecStart words %r from %r" % (words, ex))
    check("%" not in ex.replace("%%", ""), "J: a lone %% survived in %r" % ex)
    print("J. paths with space, quote and % round-trip through ExecStart quoting")

    # K: failed activation. Fake systemctl that fails on enable.
    FAIL = os.path.join(TMP, "failbin"); os.makedirs(FAIL)
    with open(os.path.join(FAIL, "systemctl"), "w") as fh:
        fh.write('#!/bin/sh\nprintf "%s\\n" "$*" >> "%s"\ncase "$*" in *enable*) exit 1;; esac\nexit 0\n' % ("%s", LOG))
    os.chmod(os.path.join(FAIL, "systemctl"), 0o755)
    envK = dict(ENV, PATH=FAIL + os.pathsep + ENV["PATH"])
    mk(GOOD); approve(GOOD)
    r = run("install", d); check(r.returncode == 0, "K: setup install failed %s" % r.stderr)
    old = {p: open(p).read() for p in units("daily-summary")}
    mk(dict(GOOD, schedule="*-*-* 08:00:00")); approve(dict(GOOD, schedule="*-*-* 08:00:00"))
    r = run("install", d, env=envK)
    check(r.returncode == 5 and "restored" in r.stderr, "K: expected 5 + restored: %d %s" % (r.returncode, r.stderr))
    check({p: open(p).read() for p in units("daily-summary")} == old, "K: previous units not restored byte for byte")
    run("remove", "daily-summary")
    mk(GOOD); approve(GOOD)
    r = run("install", d, env=envK)
    check(r.returncode == 5 and not units("daily-summary"), "K: first-install failure left units: %d" % r.returncode)
    print("K. failed re-install restores the previous units; failed first install leaves none")

    # L: raw schedule with systemd-analyze absent (PATH = fake systemctl only)
    envL = dict(ENV, PATH=FAKE)
    for bad in ("*-*-* 07:00:00\nUnit=evil.service", "%H:00"):
        mb = dict(GOOD, schedule=bad); mk(mb); approve(mb)
        r = run("install", d, env=envL)
        check(r.returncode == 6 and not units("daily-summary"), "L: schedule %r: exit %d %s" % (bad, r.returncode, r.stderr))
    print("L. a schedule with a newline or % is refused even without systemd-analyze")

    print("agos-schedule-battery: PASS (12 criteria)")
finally:
    shutil.rmtree(TMP, ignore_errors=True)
