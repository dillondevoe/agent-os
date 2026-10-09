#!/usr/bin/env python3
# tests/agos-store-paths-battery.py — where the agos tools read approvals and keep apps
# (docs/design/app-approval-confirm.md §3.1, build-order PR 1: invariants A3 and A5).
#
# On the sealed image the agent account runs the tools, so the approval store must not be
# anything that account can write or redirect. The image substitutes two constants
# (IMAGE_APPROVALS, IMAGE_APPS) into its copies; dev boxes may use AGOS_APPROVALS / AGOS_APPS;
# otherwise the old $HOME defaults hold. Every arm is paired with a control.
#
#   A. Default (no env, no constants): approval in ~/.local/state counts (control for B-D).
#   B. AGOS_APPROVALS set: the $HOME store is NOT consulted (planted approval refused); the env
#      store's approval counts; a relative AGOS_APPROVALS approves nothing.
#   C. Image constants (substituted copy): the image store's approval counts; an approval planted
#      in $HOME AND in an AGOS_APPROVALS store does NOT count; a missing image store approves nothing.
#   D. AGOS_APPS: an app under the env root validates; the same app under the $HOME default root
#      is refused R11 while AGOS_APPS is set; image IMAGE_APPS beats AGOS_APPS.
#   E. R5: an app may not bind the approvals store's directory, the apps root, or an ancestor of
#      either, wherever they are configured; control: a sibling file binds fine.
#   F. agos-approve writes where the runner reads: AGOS_APPROVALS store on a dev box (0600, dir
#      0700); image store 0644 with the provisioned directory left as it was, and a missing image
#      directory is refused, never created.
#   G. agos-schedule: on a dev box the service unit carries Environment=AGOS_APPROVALS/AGOS_APPS
#      when set (control: absent when unset); on the image copy it carries neither and ExecStart
#      is the image runner beside it (A5).
#   H. field_json: ASCII-only, sorted, compact; differs from canonical() on a non-ASCII value.
#   I. agos-build writes into AGOS_APPS (fake backend), not the $HOME default.
#
# stdlib only. Exits 0 on all-pass, non-zero (AssertionError) on any failure.

SIDE_EFFECTS = []  # scratch dirs under mktemp, removed at exit

import importlib.machinery, json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
BIN = os.path.join(REPO, "bin")
TMP = tempfile.mkdtemp(prefix="agos-store-paths-battery.")
HOME = os.path.join(TMP, "home")
FAKE = os.path.join(TMP, "fakebin")
os.makedirs(FAKE)
for tool in ("bwrap", "systemd-run", "systemctl"):
    with open(os.path.join(FAKE, tool), "w") as fh:
        fh.write("#!/bin/sh\nexit 0\n")
    os.chmod(os.path.join(FAKE, tool), 0o755)
os.makedirs(os.path.join(HOME, "notes"))
open(os.path.join(HOME, "notes", "todo.md"), "w").write("todo\n")
BASE_ENV = {k: v for k, v in os.environ.items() if k not in ("AGOS_APPROVALS", "AGOS_APPS")}
BASE_ENV.update(HOME=HOME, XDG_CONFIG_HOME=os.path.join(HOME, ".config"), AGOS_RUN_NO_SYSTEMD="1",
                PATH=FAKE + os.pathsep + os.environ.get("PATH", ""))
DEFAULT_APPS = os.path.join(HOME, ".local/share/agent-os/apps")
DEFAULT_STORE = os.path.join(HOME, ".local/state/agent-os/approvals.json")


def check(c, msg):
    if not c:
        raise AssertionError(msg)


def load(path, name):
    return importlib.machinery.SourceFileLoader(name, path).load_module()


R = load(os.path.join(BIN, "agos-run"), "agos_run_src")

GOOD = {"name": "daily-summary", "version": "0.1", "entry": ["sh", "-c", "cat $HOME/notes/todo.md"],
        "files": [{"path": "~/notes/todo.md", "mode": "r"}], "network": [], "devices": [],
        "limits": {"cpu_pct": 25, "mem_mb": 128, "wall_s": 60}, "schedule": "*-*-* 07:00:00"}


def mk_app(root, m=GOOD):
    d = os.path.join(root, m["name"])
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "manifest.json"), "w") as fh:
        json.dump(m, fh)
    return d


def approve_in(store, m=GOOD):
    os.makedirs(os.path.dirname(store), exist_ok=True)
    db = {}
    if os.path.exists(store):
        db = json.load(open(store))
    db[R.sha(m)] = {"name": m["name"], "approved_at": "2026-10-09T00:00:00Z"}
    json.dump(db, open(store, "w"))


def reset():
    for p in (os.path.join(HOME, ".local"), os.path.join(TMP, "envstore"), os.path.join(TMP, "envapps"),
              os.path.join(TMP, "image"), os.path.join(HOME, ".config")):
        shutil.rmtree(p, ignore_errors=True)


def dry(binary, appdir, **env):
    e = dict(BASE_ENV, **env)
    p = subprocess.run([sys.executable, binary, appdir, "--dry-run"], env=e, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def image_bin(store, apps):
    """A copy of bin/ with the two constants substituted, the way the image build will do it."""
    d = os.path.join(TMP, "imagebin")
    shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
    for tool in ("agos-run", "agos-approve", "agos-schedule", "agos-build"):
        shutil.copyfile(os.path.join(BIN, tool), os.path.join(d, tool))   # contents only: store copies are read-only
    src = open(os.path.join(d, "agos-run")).read()
    for k, v in (("IMAGE_APPROVALS", store), ("IMAGE_APPS", apps)):
        line = '%s = ""' % k
        check(src.count(line) == 1, "agos-run must carry exactly one substitutable %s line" % k)
        src = src.replace(line, '%s = %r' % (k, v))
    open(os.path.join(d, "agos-run"), "w").write(src)
    return d


passed = []
def ok(tag):
    passed.append(tag); print("  ok  " + tag)


# A ── defaults
reset()
app = mk_app(DEFAULT_APPS)
rc, out = dry(os.path.join(BIN, "agos-run"), app)
check(rc == 4, "A control: unapproved must be refused (4), got %d: %s" % (rc, out))
approve_in(DEFAULT_STORE)
rc, out = dry(os.path.join(BIN, "agos-run"), app)
check(rc == 0, "A default store approval must count, got %d: %s" % (rc, out))
ok("A default: ~/.local/state store counts")

# B ── AGOS_APPROVALS
envstore = os.path.join(TMP, "envstore", "approvals.json")
rc, out = dry(os.path.join(BIN, "agos-run"), app, AGOS_APPROVALS=envstore)
check(rc == 4, "B: with AGOS_APPROVALS set the $HOME approval must NOT count, got %d" % rc)
approve_in(envstore)
rc, out = dry(os.path.join(BIN, "agos-run"), app, AGOS_APPROVALS=envstore)
check(rc == 0, "B: env store approval must count, got %d: %s" % (rc, out))
rc, out = dry(os.path.join(BIN, "agos-run"), app, AGOS_APPROVALS="relative/approvals.json")
check(rc != 0, "B: a relative AGOS_APPROVALS must approve nothing")
ok("B AGOS_APPROVALS: replaces the $HOME store; relative path approves nothing")

# C ── image constants
imgstore = os.path.join(TMP, "image", "app-approvals", "approvals.json")
imgapps = os.path.join(TMP, "image", "apps")
ib = image_bin(imgstore, imgapps)
iapp = mk_app(imgapps)
approve_in(DEFAULT_STORE); approve_in(envstore)
rc, out = dry(os.path.join(ib, "agos-run"), iapp, AGOS_APPROVALS=envstore)
check(rc == 4, "C: image copy must ignore $HOME and AGOS_APPROVALS approvals, got %d: %s" % (rc, out))
rc, out = dry(os.path.join(ib, "agos-run"), iapp)
check(rc == 4, "C: image copy with no image store approves nothing (no fallback), got %d" % rc)
approve_in(imgstore)
rc, out = dry(os.path.join(ib, "agos-run"), iapp, AGOS_APPROVALS="/nonexistent/x.json")
check(rc == 0, "C: image store approval must count whatever AGOS_APPROVALS says, got %d: %s" % (rc, out))
ok("C image constants: only the image store counts; no fallback")

# D ── apps root
reset()
envapps = os.path.join(TMP, "envapps")
eapp = mk_app(envapps); approve_in(DEFAULT_STORE)
rc, out = dry(os.path.join(BIN, "agos-run"), eapp, AGOS_APPS=envapps)
check(rc == 0, "D: app under AGOS_APPS must validate, got %d: %s" % (rc, out))
happ = mk_app(DEFAULT_APPS)
rc, out = dry(os.path.join(BIN, "agos-run"), happ, AGOS_APPS=envapps)
check(rc == 3 and "R11" in out, "D: app under the $HOME root must be refused R11 while AGOS_APPS is set, got %d: %s" % (rc, out))
rc, out = dry(os.path.join(BIN, "agos-run"), happ)
check(rc == 0, "D control: without AGOS_APPS the $HOME root app validates, got %d: %s" % (rc, out))
ib = image_bin(os.path.join(TMP, "image", "s.json"), os.path.join(TMP, "image", "apps"))
rc, out = dry(os.path.join(ib, "agos-run"), eapp, AGOS_APPS=envapps)
check(rc == 3 and "R11" in out, "D: IMAGE_APPS must beat AGOS_APPS, got %d: %s" % (rc, out))
ok("D apps root: AGOS_APPS replaces the default; IMAGE_APPS beats AGOS_APPS")

# E ── R5 overlaps wherever configured
reset()
storedir = os.path.join(HOME, "approvals-here"); os.makedirs(storedir)
open(os.path.join(storedir, "approvals.json"), "w").write("{}")
appsdir = os.path.join(HOME, "my-apps"); os.makedirs(appsdir)
env = dict(AGOS_APPROVALS=os.path.join(storedir, "approvals.json"), AGOS_APPS=appsdir)
for bad, why in ((storedir, "store dir"), (os.path.join(storedir, "approvals.json"), "store file"),
                 (appsdir, "apps root"), (HOME + "/approvals-here/..", "ancestor")):
    m = dict(GOOD, files=[{"path": bad, "mode": "r"}])
    a = mk_app(appsdir, m)
    rc, out = dry(os.path.join(BIN, "agos-run"), a, AGOS_RUN_NO_SYSTEMD="1", **env)
    check(rc == 3 and ("R5" in out or "R4" in out), "E: binding the %s must be refused, got %d: %s" % (why, rc, out))
m = dict(GOOD); a = mk_app(appsdir, m)
approve_in(env["AGOS_APPROVALS"], m)
rc, out = dry(os.path.join(BIN, "agos-run"), a, **env)
check(rc == 0, "E control: a normal file binds fine with relocated store/apps, got %d: %s" % (rc, out))
ok("E R5: relocated store dir and apps root are never bindable")

# F ── agos-approve writes where the runner reads
reset()
os.environ.update(HOME=HOME)
os.environ.pop("AGOS_APPROVALS", None)
A = load(os.path.join(BIN, "agos-approve"), "agos_approve_src")
os.environ["AGOS_APPROVALS"] = os.path.join(TMP, "envstore", "deep", "approvals.json")
A.write_db(HOME, {"x": {"name": "x"}})
p = os.environ["AGOS_APPROVALS"]
check(os.path.exists(p) and not os.path.exists(DEFAULT_STORE), "F: dev write must land in AGOS_APPROVALS only")
check(os.stat(p).st_mode & 0o777 == 0o600 and os.stat(os.path.dirname(p)).st_mode & 0o777 == 0o700, "F: dev store 0600 in a 0700 dir")
os.environ.pop("AGOS_APPROVALS")
imgstore = os.path.join(TMP, "image", "app-approvals", "approvals.json")
ib = image_bin(imgstore, os.path.join(TMP, "image", "apps"))
AI = load(os.path.join(ib, "agos-approve"), "agos_approve_img")
try:
    AI.write_db(HOME, {"x": {"name": "x"}}); raise AssertionError("F: a missing image store dir must be refused, not created")
except AI.R.ManifestError:
    pass
check(not os.path.exists(os.path.dirname(imgstore)), "F: the image dir must not have been created")
os.makedirs(os.path.dirname(imgstore)); os.chmod(os.path.dirname(imgstore), 0o755)
AI.write_db(HOME, {"x": {"name": "x"}})
check(os.stat(imgstore).st_mode & 0o777 == 0o644, "F: image store file must be 0644 (agent-uid runner reads it)")
check(os.stat(os.path.dirname(imgstore)).st_mode & 0o777 == 0o755, "F: image store dir must be left 0755, not tightened")
ok("F agos-approve: dev 0600/0700 in AGOS_APPROVALS; image 0644, dir untouched, never created")

# G ── agos-schedule unit environment
def install(binary, appdir, **env):
    e = dict(BASE_ENV, **env)
    p = subprocess.run([sys.executable, binary, "install", appdir], env=e, capture_output=True, text=True)
    svc = os.path.join(HOME, ".config/systemd/user/agos-app-daily-summary.service")
    return p.returncode, (open(svc).read() if os.path.exists(svc) else ""), p.stdout + p.stderr

reset()
envstore = os.path.join(TMP, "envstore", "approvals.json"); envapps = os.path.join(TMP, "envapps")
a = mk_app(envapps); approve_in(envstore)
rc, unit, out = install(os.path.join(BIN, "agos-schedule"), a, AGOS_APPROVALS=envstore, AGOS_APPS=envapps)
check(rc == 0, "G: install rc=%d %s" % (rc, out))
check('Environment=AGOS_APPROVALS="%s"' % envstore in unit and 'Environment=AGOS_APPS="%s"' % envapps in unit,
      "G: dev unit must carry the store env:\n" + unit)
reset()
a = mk_app(DEFAULT_APPS); approve_in(DEFAULT_STORE)
rc, unit, out = install(os.path.join(BIN, "agos-schedule"), a)
check(rc == 0 and "AGOS_APPROVALS" not in unit and "AGOS_APPS" not in unit, "G control: no store env when unset:\n" + unit)
reset()
imgstore = os.path.join(TMP, "image", "app-approvals", "approvals.json"); imgapps = os.path.join(TMP, "image", "apps")
ib = image_bin(imgstore, imgapps)
a = mk_app(imgapps); approve_in(imgstore)
rc, unit, out = install(os.path.join(ib, "agos-schedule"), a, AGOS_APPROVALS="/elsewhere.json")
check(rc == 0, "G image: install rc=%d %s" % (rc, out))
check("AGOS_APPROVALS" not in unit and ('ExecStart="%s"' % os.path.join(ib, "agos-run")) in unit,
      "G image: unit must run the image runner beside it and carry no store env:\n" + unit)
ok("G agos-schedule: scheduled runs read the same store (A5)")

# H ── field_json
check(R.field_json({"b": 1, "a": ["é"]}) == '{"a":["\\u00e9"],"b":1}', "H: field_json must be sorted, compact, ASCII")
check(R.field_json(["é"]).encode() != R.canonical(["é"]), "H control: field_json differs from canonical() on non-ASCII")
ok("H field_json: ASCII, sorted, compact; distinct from canonical()")

# I ── agos-build into AGOS_APPS
reset()
envapps = os.path.join(TMP, "envapps")
fake = os.path.join(TMP, "answers.json")
json.dump([{"content": "", "tool_calls": [{"function": {"name": "propose_app", "arguments": {
    "name": "gym-spending", "plan": "1. read notes 2. print", "manifest": {"files": [{"path": "~/notes/todo.md", "mode": "r"}],
    "network": [], "schedule": "", "devices": []},
    "entry": {"filename": "app.py", "source": "print(open('/dev/null').read())\n"}}}}]}], open(fake, "w"))
e = dict(BASE_ENV, AGOS_APPS=envapps, AGOS_BUILD_FAKE=fake)
p = subprocess.run([sys.executable, os.path.join(BIN, "agos-build"), "track my gym spending", "--backend", "fake"],
                   env=e, capture_output=True, text=True)
built = [d for d in os.listdir(envapps)] if os.path.isdir(envapps) else []
check(p.returncode == 0 and built and not os.path.exists(DEFAULT_APPS),
      "I: agos-build must write into AGOS_APPS only (rc=%d built=%s): %s" % (p.returncode, built, (p.stdout + p.stderr)[-400:]))
ok("I agos-build writes into AGOS_APPS")

shutil.rmtree(TMP, ignore_errors=True)
print("agos-store-paths-battery: PASS (%d criteria)" % len(passed))
