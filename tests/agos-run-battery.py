#!/usr/bin/env python3
# tests/agos-run-battery.py — CONTRACT BATTERY for bin/agos-run (the v0 app sandbox runner).
#
# The runner is a gate: it must say "no" to every manifest the spec forbids and must never run
# anything outside bubblewrap. So every arm is paired: a good manifest passes dry-run, and the
# same manifest with ONE forbidden change is refused, naming the rule.
#
# Acceptance criteria:
#   A. A valid manifest dry-runs: argv binds the app dir rw, each file with its mode, tmpfs over
#      $HOME, --unshare-net present, entry last.
#   B. CONTROL: rw on a credential path (~/.ssh/config) is refused (R5); so is a credential-
#      looking name (~/notes/.env) and a path outside $HOME (R4).
#   C. Unapproved manifest is refused (exit 4) without --approve-for-test; approved sha matches
#      -> runs (dry); edit one byte after approval -> refused (exit 4). Corrupt approvals -> refused.
#   D. network non-empty still yields --unshare-net and prints the DENIED notice; a domain with a
#      scheme is refused (R7).
#   E. limits map to systemd-run -p MemoryMax=/CPUQuota=/RuntimeMaxSec=; out-of-range is R9;
#      AGOS_RUN_NO_SYSTEMD drops the systemd-run prefix only (bwrap stays).
#   F. devices non-empty is R8; unknown key is R12; app dir outside the apps root is R11.
#   G. REAL RUN (only when bwrap works on this host): a file NOT in the manifest is invisible
#      inside the sandbox while a bound file is readable. Skipped (and said so) under nix.
#
# stdlib only. Exit 0 all-pass; AssertionError otherwise.
SIDE_EFFECTS = []  # scratch HOME under mktemp, removed at exit; arm G runs bwrap on-box, nothing outlives the run

import copy, json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RUN = os.path.abspath(os.path.join(HERE, "..", "bin", "agos-run"))
TMP = tempfile.mkdtemp(prefix="agos-run-battery.")
HOME = os.path.join(TMP, "home")
APPS = os.path.join(HOME, ".local/share/agent-os/apps")
APPROVALS = os.path.join(HOME, ".local/state/agent-os/approvals.json")
ENV = dict(os.environ, HOME=HOME, AGOS_RUN_NO_SYSTEMD="")
ENV.pop("AGOS_RUN_NO_SYSTEMD")
FAKE_BWRAP = os.path.join(TMP, "fakebin")


def check(c, msg):
    if not c: raise AssertionError(msg)


def mk(name, manifest):
    d = os.path.join(APPS, name); os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "manifest.json"), "w") as fh: json.dump(manifest, fh)
    return d


def run(appdir, *flags, env=None):
    r = subprocess.run([sys.executable, RUN, appdir, *flags], capture_output=True, text=True, env=env or ENV)
    return r.returncode, r.stdout, r.stderr


def dry(appdir, *flags, env=None):
    rc, out, err = run(appdir, "--dry-run", *flags, env=env)
    return rc, (json.loads(out) if rc == 0 and out.strip() else None), err


def sha_of(m):
    import hashlib
    return hashlib.sha256(json.dumps(m, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def approve(m):
    os.makedirs(os.path.dirname(APPROVALS), exist_ok=True)
    with open(APPROVALS, "w") as fh: json.dump({sha_of(m): {"name": m["name"], "approved_at": "test"}}, fh)


os.makedirs(os.path.join(HOME, "notes"), exist_ok=True)
os.makedirs(os.path.join(HOME, ".ssh"), exist_ok=True)
open(os.path.join(HOME, "notes/todo.md"), "w").write("todo\n")
open(os.path.join(HOME, "notes/.env"), "w").write("X=1\n")
open(os.path.join(HOME, ".ssh/config"), "w").write("Host x\n")
open(os.path.join(HOME, "secret.txt"), "w").write("not for apps\n")
# a fake bwrap on PATH so dry-run arms do not depend on the host having bubblewrap
os.makedirs(FAKE_BWRAP); open(os.path.join(FAKE_BWRAP, "bwrap"), "w").write("#!/bin/sh\nexit 0\n"); os.chmod(os.path.join(FAKE_BWRAP, "bwrap"), 0o755)
open(os.path.join(FAKE_BWRAP, "systemd-run"), "w").write("#!/bin/sh\nexit 0\n"); os.chmod(os.path.join(FAKE_BWRAP, "systemd-run"), 0o755)
ENV["PATH"] = FAKE_BWRAP + os.pathsep + ENV.get("PATH", "")

GOOD = {"name": "gym-spending", "version": "0.1", "entry": ["sh", "-c", "cat $HOME/notes/todo.md"],
        "files": [{"path": "~/notes/todo.md", "mode": "r"}, {"path": "~/notes", "mode": "rw"}],
        "network": [], "devices": [], "limits": {"cpu_pct": 25, "mem_mb": 128, "wall_s": 60}, "schedule": ""}


def test_a_valid():
    d = mk("gym-spending", GOOD)
    rc, j, err = dry(d, "--approve-for-test")
    check(rc == 0, "A valid dry-run rc=%d err=%s" % (rc, err))
    a = j["argv"]
    check("--unshare-net" in a and "--unshare-all" in a, "A missing unshare")
    check(a[a.index("--tmpfs", a.index(HOME) - 1) + 1] == HOME or ["--tmpfs", HOME] == a[a.index(HOME) - 1:a.index(HOME) + 1], "A no tmpfs over HOME")
    ad = os.path.realpath(d)
    check(["--bind", ad, ad] == a[a.index("--bind"):a.index("--bind") + 3], "A app dir not bound rw")
    todo = os.path.realpath(os.path.join(HOME, "notes/todo.md")); notes = os.path.realpath(os.path.join(HOME, "notes"))
    check(["--ro-bind", todo, todo] == a[a.index(todo) - 1:a.index(todo) + 2], "A todo not ro-bind")
    i = a.index(notes); check(a[i - 1] == "--bind", "A notes not rw bind")
    check(a[-3:] == GOOD["entry"], "A entry not last: %r" % a[-3:])
    check("--approve-for-test bypasses" in err, "A test bypass must warn loudly")
    check(j["sha256"] == sha_of(GOOD), "A sha mismatch")


def test_b_credentials_and_outside():
    for path, rule in (("~/.ssh/config", "R5"), ("~/notes/.env", "R5"), ("/etc/passwd", "R4"), (TMP + "/x", "R4"), ("notes/todo.md", "R4")):
        m = copy.deepcopy(GOOD); m["files"] = [{"path": path, "mode": "rw"}]
        rc, j, err = dry(mk("gym-spending", m), "--approve-for-test")
        check(rc == 3 and rule in err, "B %s should be refused with %s: rc=%d err=%s" % (path, rule, rc, err))
    m = copy.deepcopy(GOOD); m["files"] = [{"path": "~/notes/todo.md", "mode": "x"}]
    rc, _, err = dry(mk("gym-spending", m), "--approve-for-test"); check(rc == 3 and "R6" in err, "B bad mode")


def test_c_approval():
    d = mk("gym-spending", GOOD)
    if os.path.exists(APPROVALS): os.remove(APPROVALS)
    rc, _, err = dry(d); check(rc == 4 and "not approved" in err, "C unapproved must refuse: %d %s" % (rc, err))
    approve(GOOD)
    rc, j, err = dry(d); check(rc == 0 and j["sha256"] == sha_of(GOOD), "C approved should run: %d %s" % (rc, err))
    m = copy.deepcopy(GOOD); m["files"][0]["mode"] = "rw"; mk("gym-spending", m)
    rc, _, err = dry(d); check(rc == 4, "C edited-after-approval must refuse: %d %s" % (rc, err))
    mk("gym-spending", GOOD)
    open(APPROVALS, "w").write("{not json")
    rc, _, _ = dry(d); check(rc == 4, "C corrupt approvals approves nothing")
    open(APPROVALS, "w").write(json.dumps({sha_of(GOOD): {"name": "other-app"}}))
    rc, _, _ = dry(d); check(rc == 4, "C approval bound to another name must not count")
    os.remove(APPROVALS)


def test_d_network():
    m = copy.deepcopy(GOOD); m["network"] = ["news.invalid", "feed.invalid"]  # names only; nothing is contacted
    rc, j, err = dry(mk("gym-spending", m), "--approve-for-test")
    check(rc == 0 and "--unshare-net" in j["argv"], "D network still unshared")
    check("DENIED in v0" in err and "feed.invalid" in err, "D must print the honest notice")
    m["network"] = ["https://feed.invalid"]
    rc, _, err = dry(mk("gym-spending", m), "--approve-for-test"); check(rc == 3 and "R7" in err, "D scheme in domain is R7")


def test_e_limits():
    rc, j, _ = dry(mk("gym-spending", GOOD), "--approve-for-test")
    a = j["argv"]; check(a[0] == "systemd-run" and "MemoryMax=128M" in a and "CPUQuota=25%" in a and "RuntimeMaxSec=60" in a, "E limits -> systemd-run: %r" % a[:12])
    env = dict(ENV, AGOS_RUN_NO_SYSTEMD="1")
    rc, j, _ = dry(mk("gym-spending", GOOD), "--approve-for-test", env=env)
    check(j["argv"][0] == "bwrap", "E no-systemd knob must leave bwrap in place")
    for k, v in (("cpu_pct", 0), ("mem_mb", 100000), ("wall_s", "60"), ("nope", 1)):
        m = copy.deepcopy(GOOD); m["limits"] = {k: v}
        rc, _, err = dry(mk("gym-spending", m), "--approve-for-test"); check(rc == 3 and "R9" in err, "E %s=%r should be R9" % (k, v))
    m = copy.deepcopy(GOOD); del m["limits"]
    rc, j, _ = dry(mk("gym-spending", m), "--approve-for-test"); check(rc == 0 and "MemoryMax=512M" in j["argv"], "E defaults apply when limits absent")


def test_f_shape():
    m = copy.deepcopy(GOOD); m["devices"] = ["/dev/video0"]
    rc, _, err = dry(mk("gym-spending", m), "--approve-for-test"); check(rc == 3 and "R8" in err, "F devices")
    m = copy.deepcopy(GOOD); m["sudo"] = True
    rc, _, err = dry(mk("gym-spending", m), "--approve-for-test"); check(rc == 3 and "R12" in err, "F unknown key")
    m = copy.deepcopy(GOOD); m["name"] = "Gym Spending"
    rc, _, err = dry(mk("gym-spending", m), "--approve-for-test"); check(rc == 3 and "R1" in err, "F bad name")
    m = copy.deepcopy(GOOD); m["schedule"] = "every blue moon"
    rc, _, err = dry(mk("gym-spending", m), "--approve-for-test")
    check(rc == 3 and "R10" in err if shutil.which("systemd-analyze") else rc == 0, "F schedule validation")
    m = copy.deepcopy(GOOD); m["schedule"] = "daily"
    rc, _, err = dry(mk("gym-spending", m), "--approve-for-test"); check(rc == 0, "F 'daily' is valid OnCalendar: %s" % err)
    outside = os.path.join(TMP, "elsewhere", "gym-spending"); os.makedirs(outside, exist_ok=True)
    json.dump(GOOD, open(os.path.join(outside, "manifest.json"), "w"))
    rc, _, err = dry(outside, "--approve-for-test"); check(rc == 3 and "R11" in err, "F app dir outside apps root")
    nob = dict(ENV, PATH="/nonexistent")
    rc, _, err = run(mk("gym-spending", GOOD), "--dry-run", "--approve-for-test", env=nob)
    check(rc == 5 and "bwrap missing" in err, "F no bwrap must refuse, never run unsandboxed: %d %s" % (rc, err))


def bwrap_works():
    if os.environ.get("NIX_BUILD_TOP") or not shutil.which("bwrap"): return False
    r = subprocess.run(["bwrap", "--unshare-all", "--ro-bind", "/", "/", "true"], capture_output=True)
    return r.returncode == 0


def test_g_real_run():
    if not bwrap_works():
        print("   (G skipped: bwrap not usable on this host — dry-run arms only)"); return
    m = copy.deepcopy(GOOD)
    m["entry"] = ["sh", "-c", "cat $HOME/notes/todo.md >/dev/null && echo bound-ok; cat $HOME/secret.txt 2>/dev/null && echo LEAK; "
                              "touch $HOME/notes/todo.md 2>/dev/null && echo rw-on-ro; touch ./made && echo appdir-rw; "
                              "touch $HOME/notes/new 2>/dev/null && echo notes-rw"]
    m["files"] = [{"path": "~/notes/todo.md", "mode": "r"}]
    d = mk("gym-spending", m)
    env = dict(ENV, PATH=os.environ.get("PATH", ""), AGOS_RUN_NO_SYSTEMD="1")
    r = subprocess.run([sys.executable, RUN, d, "--approve-for-test"], capture_output=True, text=True, env=env, timeout=60)
    out = r.stdout
    check("bound-ok" in out, "G bound file must be readable: %r %r" % (out, r.stderr))
    check("LEAK" not in out, "G unbound $HOME file leaked into sandbox")
    check("rw-on-ro" not in out, "G r-mode file was writable")
    check("notes-rw" not in out, "G unbound dir was writable")
    check("appdir-rw" in out, "G app dir must be rw: %r" % out)
    check(os.path.exists(os.path.join(d, "made")), "G app dir write did not land on the host side")


TESTS = [("A. valid manifest dry-run: binds, tmpfs HOME, unshare-net, entry last", test_a_valid),
         ("B. CONTROL: credential path / name / outside $HOME / bad mode refused", test_b_credentials_and_outside),
         ("C. approval: unapproved, approved, edited-after-approval, corrupt, wrong name", test_c_approval),
         ("D. network listed -> still --unshare-net + honest notice; scheme refused", test_d_network),
         ("E. limits -> systemd-run flags; bounds; defaults; no-systemd knob keeps bwrap", test_e_limits),
         ("F. devices/unknown key/name/schedule/app root/no-bwrap refusals", test_f_shape),
         ("G. REAL RUN: unbound file invisible, r is r, app dir rw", test_g_real_run)]

if __name__ == "__main__":
    fails = 0
    for name, fn in TESTS:
        try: fn(); print("%s — PASS" % name)
        except AssertionError as e: fails += 1; print("%s — FAIL: %s" % (name, e))
    shutil.rmtree(TMP, ignore_errors=True)
    print("\nagos-run contract battery: %s (%d criteria)" % ("ALL PASS" if not fails else "%d FAILED" % fails, len(TESTS)))
    sys.exit(1 if fails else 0)
