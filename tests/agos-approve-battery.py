#!/usr/bin/env python3
# tests/agos-approve-battery.py — CONTRACT BATTERY for bin/agos-approve (the owner-side writer
# of ~/.local/state/agent-os/approvals.json, docs/design/app-manifest.md §4).
#
# The approver is a gate on WHO and on WHAT: only a human on a tty outside the agent session
# can approve, and what they approve is bound to the rendered manifest by typing its sha prefix.
# Every arm is paired with a control that must be refused.
#
# Acceptance criteria:
#   A. `show` renders name, version, entry, every files[] path with its mode, the v0 network
#      note, limits, schedule, and the sha256; an invalid manifest is refused naming the rule.
#   B. CONTROL: `approve` with stdin a pipe is refused (exit 3, "not a tty"); file untouched.
#   C. CONTROL: `approve` on a tty but with AGENT_OS_ACTIVE set is refused (exit 3); untouched.
#   D. CONTROL: on a tty, typing the WRONG sha prefix (or y) is refused (exit 4); untouched.
#   E. On a tty, typing the right 8 hex: approvals.json written 0600 in a 0700 dir with
#      {sha: {name, approved_at}}, and `agos-run --dry-run` now passes WITHOUT --approve-for-test.
#   F. Edit the manifest one byte after approval: agos-run refuses (exit 4); re-approve of the
#      edited manifest records a second sha; `list` shows both names.
#   G. `revoke <name>` removes the entries; agos-run refuses again; `revoke` of an unknown key
#      exits 1; `list` says none.
#   H. Corrupt approvals file: `approve` refuses to write (exit 2) and leaves the bytes alone;
#      a sha prefix shorter than 8 never matches on revoke.
#
# stdlib only (pty for the tty arms). Exit 0 all-pass; AssertionError otherwise.
SIDE_EFFECTS = []  # scratch HOME under mktemp, removed at exit; nothing outlives the run

import json, os, pty, select, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
APPROVE = os.path.abspath(os.path.join(HERE, "..", "bin", "agos-approve"))
RUN = os.path.abspath(os.path.join(HERE, "..", "bin", "agos-run"))
TMP = tempfile.mkdtemp(prefix="agos-approve-battery.")
HOME = os.path.join(TMP, "home")
APPS = os.path.join(HOME, ".local/share/agent-os/apps")
APPROVALS = os.path.join(HOME, ".local/state/agent-os/approvals.json")
FAKE = os.path.join(TMP, "fakebin")
ENV = dict(os.environ, HOME=HOME, AGOS_RUN_NO_SYSTEMD="1")
ENV.pop("AGENT_OS_ACTIVE", None)
os.makedirs(FAKE); open(os.path.join(FAKE, "bwrap"), "w").write("#!/bin/sh\nexit 0\n"); os.chmod(os.path.join(FAKE, "bwrap"), 0o755)
ENV["PATH"] = FAKE + os.pathsep + ENV.get("PATH", "")
os.makedirs(os.path.join(HOME, "notes"), exist_ok=True)
open(os.path.join(HOME, "notes/todo.md"), "w").write("todo\n")

GOOD = {"name": "gym-spending", "version": "0.1", "entry": ["sh", "-c", "cat $HOME/notes/todo.md"],
        "files": [{"path": "~/notes/todo.md", "mode": "r"}, {"path": "~/drafts", "mode": "rw"}],
        "network": ["api.example.invalid"], "devices": [], "limits": {"cpu_pct": 25, "mem_mb": 128, "wall_s": 60}, "schedule": ""}


def check(c, msg):
    if not c: raise AssertionError(msg)


def mk(name, manifest):
    d = os.path.join(APPS, name); os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "manifest.json"), "w") as fh: json.dump(manifest, fh)
    return d


def sha_of(m):
    import hashlib
    return hashlib.sha256(json.dumps(m, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def approve_cli(*args, env=None, stdin=subprocess.PIPE, inp=None):
    r = subprocess.run([sys.executable, APPROVE, *args], input=inp, stdin=None if inp is not None else stdin,
                       capture_output=True, text=True, env=env or ENV, timeout=60)
    return r.returncode, r.stdout, r.stderr


def approve_tty(appdir, typed, env=None):
    """Run `approve` with stdin/stdout on a real pty; type `typed` when prompted. Returns (rc, transcript)."""
    pid, fd = pty.fork()
    if pid == 0:
        os.environ.clear(); os.environ.update(env or ENV)
        os.execv(sys.executable, [sys.executable, APPROVE, "approve", appdir])
    out = b""; deadline = time.time() + 60; sent = False
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try: chunk = os.read(fd, 4096)
            except OSError: break
            if not chunk: break
            out += chunk
        if not sent and b"type its first 8" in out:
            os.write(fd, (typed + "\n").encode()); sent = True
    _, status = os.waitpid(pid, 0)
    os.close(fd)
    rc = os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else (status >> 8)
    return rc, out.decode(errors="replace")


def runner_dry(appdir):
    r = subprocess.run([sys.executable, RUN, appdir, "--dry-run"], capture_output=True, text=True, env=ENV)
    return r.returncode, r.stderr


def db():
    try:
        with open(APPROVALS) as fh: return json.load(fh)
    except FileNotFoundError: return None


def test_a_show():
    d = mk("gym-spending", GOOD)
    rc, out, err = approve_cli("show", d)
    check(rc == 0, "A show rc=%s %s" % (rc, err))
    for needle in ("gym-spending", "version 0.1", "cat $HOME/notes/todo.md", "r   " + os.path.join(HOME, "notes/todo.md"),
                   "rw  " + os.path.join(HOME, "drafts"), "[will be created]", "api.example.invalid", "DENIED anyway",
                   "cpu 25%", "mem 128 MB", "wall 60 s", "Schedule: none", sha_of(GOOD)[:8], sha_of(GOOD)):
        check(needle in out, "A show missing %r in:\n%s" % (needle, out))
    bad = dict(GOOD, files=[{"path": "~/.ssh/config", "mode": "r"}])
    rc, out, err = approve_cli("show", mk("bad", dict(bad, name="bad")))
    check(rc == 2 and "R5" in err, "A invalid manifest must be refused naming R5: rc=%s %s" % (rc, err))


def test_b_not_a_tty():
    d = mk("gym-spending", GOOD)
    rc, out, err = approve_cli("approve", d, inp=sha_of(GOOD)[:8] + "\n")
    check(rc == 3 and "not a tty" in err, "B pipe must refuse: rc=%s %s" % (rc, err))
    check(db() is None, "B approvals file must not exist")


def test_c_agent_session():
    d = mk("gym-spending", GOOD)
    rc, out = approve_tty(d, sha_of(GOOD)[:8], env=dict(ENV, AGENT_OS_ACTIVE="1"))
    check(rc == 3 and "AGENT_OS_ACTIVE" in out, "C agent session must refuse: rc=%s\n%s" % (rc, out))
    check(db() is None, "C approvals file must not exist")


def test_d_wrong_answer():
    d = mk("gym-spending", GOOD)
    for typed in ("y", "yes", sha_of(GOOD)[:7], "deadbeef"):
        rc, out = approve_tty(d, typed)
        check(rc == 4 and "not approved" in out, "D %r must refuse: rc=%s\n%s" % (typed, rc, out))
    check(db() is None, "D approvals file must not exist")


def test_e_right_answer():
    d = mk("gym-spending", GOOD)
    rc, _ = runner_dry(d)
    check(rc == 4, "E precondition: runner must refuse unapproved (rc=%s)" % rc)
    rc, out = approve_tty(d, sha_of(GOOD)[:8].upper())  # case-insensitive on purpose
    check(rc == 0 and "approved gym-spending" in out, "E approve rc=%s\n%s" % (rc, out))
    j = db(); s = sha_of(GOOD)
    check(j and j.get(s, {}).get("name") == "gym-spending" and j[s].get("approved_at", "").endswith("Z"), "E db shape: %r" % j)
    check((os.stat(APPROVALS).st_mode & 0o777) == 0o600, "E file must be 0600")
    check((os.stat(os.path.dirname(APPROVALS)).st_mode & 0o777) == 0o700, "E dir must be 0700")
    rc, err = runner_dry(d)
    check(rc == 0, "E runner must accept the approved app: rc=%s %s" % (rc, err))
    rc, out = approve_tty(d, sha_of(GOOD)[:8])
    check(rc == 0 and "already approved" in out, "E re-approve is a no-op: %s" % out)


def test_f_edit_after_approval():
    edited = dict(GOOD, version="0.2")
    d = mk("gym-spending", edited)
    rc, err = runner_dry(d)
    check(rc == 4, "F edited manifest must be refused by the runner: rc=%s %s" % (rc, err))
    rc, out = approve_tty(d, sha_of(edited)[:8])
    check(rc == 0, "F re-approve edited: rc=%s\n%s" % (rc, out))
    check(runner_dry(d)[0] == 0, "F runner accepts re-approved")
    j = db(); check(sha_of(GOOD) in j and sha_of(edited) in j, "F both shas recorded")
    rc, out, err = approve_cli("list")
    check(rc == 0 and out.count("gym-spending") == 2 and sha_of(edited)[:8] in out, "F list: %s" % out)


def test_g_revoke():
    d = mk("gym-spending", dict(GOOD, version="0.2"))
    rc, out, err = approve_cli("revoke", "gym-spending")
    check(rc == 0 and out.count("revoked") == 2, "G revoke by name: rc=%s %s %s" % (rc, out, err))
    check(runner_dry(d)[0] == 4, "G runner refuses after revoke")
    check(db() == {}, "G db empty: %r" % db())
    rc, out, err = approve_cli("revoke", "nothing-here")
    check(rc == 1 and "nothing approved" in err, "G unknown revoke: rc=%s %s" % (rc, err))
    rc, out, err = approve_cli("list")
    check(rc == 0 and "no approved apps" in out, "G list empty: %s" % out)


def test_h_corrupt_and_short_prefix():
    d = mk("gym-spending", GOOD)
    with open(APPROVALS, "w") as fh: fh.write("{not json")
    rc, out = approve_tty(d, sha_of(GOOD)[:8])
    check(rc == 2 and "corrupt" in out, "H corrupt file must refuse to write: rc=%s\n%s" % (rc, out))
    check(open(APPROVALS).read() == "{not json", "H corrupt bytes must be left alone")
    os.remove(APPROVALS)
    rc, out = approve_tty(d, sha_of(GOOD)[:8]); check(rc == 0, "H setup approve")
    rc, out, err = approve_cli("revoke", sha_of(GOOD)[:6])
    check(rc == 1, "H a 6-char prefix must not match on revoke: rc=%s %s" % (rc, out))
    rc, out, err = approve_cli("revoke", sha_of(GOOD)[:8])
    check(rc == 0 and "revoked" in out, "H 8-char prefix revokes: %s %s" % (out, err))


TESTS = [("A. show renders every field + sha; invalid manifest refused naming the rule", test_a_show),
         ("B. CONTROL: approve with piped stdin refused (exit 3), file untouched", test_b_not_a_tty),
         ("C. CONTROL: approve inside the agent session (AGENT_OS_ACTIVE) refused, untouched", test_c_agent_session),
         ("D. CONTROL: y / yes / 7 hex / wrong hex refused (exit 4), untouched", test_d_wrong_answer),
         ("E. right sha prefix on a tty: 0600 file in 0700 dir, runner now accepts", test_e_right_answer),
         ("F. edit after approval: runner refuses; re-approve records second sha; list", test_f_edit_after_approval),
         ("G. revoke by name: runner refuses again; unknown key exit 1; list empty", test_g_revoke),
         ("H. corrupt approvals left alone (exit 2); short prefix never matches", test_h_corrupt_and_short_prefix)]

if __name__ == "__main__":
    fails = 0
    for name, fn in TESTS:
        try: fn(); print("%s — PASS" % name)
        except AssertionError as e: fails += 1; print("%s — FAIL: %s" % (name, e))
    shutil.rmtree(TMP, ignore_errors=True)
    print("\nagos-approve contract battery: %s (%d criteria)" % ("ALL PASS" if not fails else "%d FAILED" % fails, len(TESTS)))
    sys.exit(1 if fails else 0)
