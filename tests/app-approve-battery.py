#!/usr/bin/env python3
# tests/app-approve-battery.py — the CONTRACT BATTERY for bin/cap-app-approve, the impl of the
# T2 `app.approve` capability (docs/design/app-approval-confirm.md §3.3 and §5).
#
# The impl is the only writer of the store the app runner trusts on the image. Every arm is
# paired: the correct call writes exactly one entry, and each way of approving something other
# than what the human read writes NOTHING and answers a fixed refusal literal.
#
#   A. Correct arguments -> ok, one entry {name, approved_at, via: confirm}, file 0644.
#      CONTROL: each of the ten arguments changed by one character -> refused, store unchanged.
#   B. Manifest edited after the human read the frame -> sha-mismatch, store unchanged.
#   C. Path: app = the store dir (inside the cap's rw root, which the broker admits) -> app-path;
#      bad name; app/name disagree; app dir a symlink; manifest a symlink; oversize manifest;
#      not JSON; manifest missing an optional key; plain field not a string -> refused.
#   D. What the human reads: a field with U+202E or a C0 control -> unrenderable-value (control:
#      same manifest without it passes); non-ASCII version -> non-ascii-value; one value over
#      MAXPREVIEW -> value-too-long; frame over 4096 -> frame-too-long while the same manifest
#      one character shorter passes; every argument value appears in the rendered frame.
#   E. Store: missing dir -> store-dir-missing (nothing created); corrupt or non-object store ->
#      store-corrupt, file byte-identical; an existing entry is kept when a second is added.
#   F. Request shape: not JSON, arguments not an object, an extra or missing key, a non-string
#      value -> refused before anything is read.
#   G. Root guard: without the test affordance, a non-root run -> not-root (skipped as root).
#   H. No refusal content echoes an argument: every refusal across the battery is exactly
#      "refused: <code>" from the fixed set, and none contains the planted marker.
#   I. Helpers: the packaged layout (impl in bin/, helpers in lib/agent-os-cap/) works; with no
#      helpers the impl exits 3 (infrastructure), not ok.
#   J. Wiring: the registry declares app.approve T2 with exactly the spec's args and roots; the
#      broker maps it TRUSTED; cap-invoke-pkg ships it with both helper copies.
#
# stdlib only. Exits 0 on all-pass, non-zero (AssertionError) on any failure.

SIDE_EFFECTS = []  # scratch tree under mktemp, removed at exit

import atexit, copy, importlib.machinery, importlib.util, json, os, re, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
IMPL = os.path.join(REPO, "bin", "cap-app-approve")
TMP = tempfile.mkdtemp(prefix="app-approve-battery.")
atexit.register(shutil.rmtree, TMP, True)
ROOT = os.path.join(TMP, "root")
APPS = ROOT + "/var/lib/agent-os/apps"
STORE_DIR = ROOT + "/var/lib/agent-os/app-approvals"
STORE = STORE_DIR + "/approvals.json"
def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


R = load("agos_run_b", os.path.join(REPO, "bin", "agos-run"))
C = load("confirm_b", os.path.join(REPO, "bin", "confirm"))
CODES = {"bad-request", "not-root", "arguments", "helpers-missing", "app-path", "manifest-unreadable",
         "manifest-structure", "sha-mismatch", "field-mismatch", "unrenderable-value", "non-ascii-value",
         "value-too-long", "frame-too-long", "store-dir-missing", "store-corrupt", "store-write"}
MARK = "zzmark"
REFUSALS = []


def check(c, msg):
    if not c:
        raise AssertionError(msg)


GOOD = {"name": "daily-summary", "version": "0.1", "entry": ["python3", "app.py"],
        "files": [{"path": "~/notes/" + MARK + ".md", "mode": "r"}], "network": ["example.org"],
        "devices": [], "limits": {"cpu_pct": 25, "mem_mb": 128, "wall_s": 60}, "schedule": "*-*-* 07:00:00"}


def fresh(m=GOOD, name=None):
    shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(STORE_DIR); os.chmod(STORE_DIR, 0o755)
    d = os.path.join(APPS, name or m["name"]); os.makedirs(d)
    with open(os.path.join(d, "manifest.json"), "w") as fh:
        json.dump(m, fh)
    return d


def args_for(m, name=None):
    name = name or m["name"]
    a = {"app": os.path.join(APPS, name), "sha256": R.sha(m)}
    for k in ("name", "version", "schedule"):
        a[k] = m[k]
    for k in ("entry", "files", "network", "limits", "devices"):
        a[k] = R.field_json(m[k])
    return a


def call(arguments, impl=IMPL, skip_uid=True, raw=None):
    env = {"PATH": os.environ.get("PATH", ""), "AGOS_APPROVE_TEST_ROOT": ROOT}
    if skip_uid:
        env["AGOS_APPROVE_TEST_SKIP_UID"] = "1"
    stdin = raw if raw is not None else json.dumps({"capability": "app.approve", "arguments": arguments})
    # UMask=0077 is what the real capability unit applies (modules/cap-sandbox.nix); without it
    # the 0644 the runner needs would come for free and the impl's fchmod would go untested.
    p = subprocess.run([sys.executable, "-W", "error::DeprecationWarning", impl], input=stdin.encode(), capture_output=True, env=env,
                       preexec_fn=lambda: os.umask(0o077), timeout=30)   # a blocking open fails, not hangs
    try:
        out = json.loads(p.stdout.decode())
    except ValueError:
        out = None
    if out and out.get("ok") is False:
        REFUSALS.append(out.get("content"))
    return p.returncode, out


def store_bytes():
    try:
        return open(STORE, "rb").read()
    except FileNotFoundError:
        return None


def refused(out, code, what):
    check(out is not None and out.get("ok") is False and out.get("content") == "refused: " + code,
          "%s: want refused: %s, got %r" % (what, code, out))


passed = []
def ok(tag):
    passed.append(tag); print("  ok  " + tag)


# A ── correct call, and every argument tampered by one character
fresh()
rc, out = call(args_for(GOOD))
check(rc == 0 and out == {"ok": True, "content": "approved daily-summary %s" % R.sha(GOOD)[:12], "meta": {}},
      "A: correct call must approve: rc=%d %r" % (rc, out))
db = json.load(open(STORE))
check(list(db) == [R.sha(GOOD)] and db[R.sha(GOOD)]["name"] == "daily-summary" and db[R.sha(GOOD)]["via"] == "confirm"
      and re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", db[R.sha(GOOD)]["approved_at"]), "A: entry shape %r" % db)
check(os.stat(STORE).st_mode & 0o777 == 0o644, "A: store must be 0644 (the agent-uid runner reads it)")
check(R.approved(GOOD, "/nonexistent") is False, "A: sanity — the runner's default lookup is elsewhere")
for k in sorted(args_for(GOOD)):
    fresh()
    a = args_for(GOOD); a[k] = a[k][:-1] + ("x" if a[k][-1:] != "x" else "y")
    rc, out = call(a)
    check(out and out.get("ok") is False and store_bytes() is None,
          "A control: tampering %s must refuse and write nothing, got %r" % (k, out))
ok("A correct call approves (0644, via confirm); any one argument tampered writes nothing")

# B ── edited between the frame and the impl
d = fresh(); a = args_for(GOOD)
edited = dict(GOOD, network=["example.org", "evil.example"])
json.dump(edited, open(os.path.join(d, "manifest.json"), "w"))
rc, out = call(a)
refused(out, "sha-mismatch", "B"); check(store_bytes() is None, "B: nothing written")
rc, out = call(dict(args_for(edited), network=a["network"]))
check(out and out.get("ok") is False and store_bytes() is None, "B: the new sha with the old field still refuses")
ok("B manifest edited during the prompt -> nothing approved")

# C ── paths and structure
fresh(); a = args_for(GOOD); a["app"] = STORE_DIR
rc, out = call(a); refused(out, "app-path", "C store dir as app")
fresh(); rc, out = call(dict(args_for(GOOD), name="Bad_Name")); refused(out, "app-path", "C bad name")
other = dict(GOOD, name="other-app"); fresh(other)
rc, out = call(dict(args_for(other), app=os.path.join(APPS, "daily-summary"))); refused(out, "app-path", "C app/name disagree")
fresh(); real = os.path.join(APPS, "daily-summary"); shutil.move(real, real + "-real"); os.symlink(real + "-real", real)
rc, out = call(args_for(GOOD)); refused(out, "manifest-unreadable", "C app dir symlink")
d = fresh(); mf = os.path.join(d, "manifest.json"); os.rename(mf, mf + ".real"); os.symlink(mf + ".real", mf)
rc, out = call(args_for(GOOD)); refused(out, "manifest-unreadable", "C manifest symlink")
d = fresh(); mf = os.path.join(d, "manifest.json"); os.unlink(mf); os.mkfifo(mf)
rc, out = call(args_for(GOOD)); refused(out, "manifest-unreadable", "C manifest FIFO (must not block)")
big = dict(GOOD, version="1" * (64 * 1024)); fresh(big)
rc, out = call(args_for(big)); refused(out, "manifest-unreadable", "C oversize manifest")
d = fresh(); open(os.path.join(d, "manifest.json"), "w").write("{not json")
rc, out = call(args_for(GOOD)); refused(out, "manifest-unreadable", "C not JSON")
short = {k: v for k, v in GOOD.items() if k != "limits"}; fresh(short)
a = args_for(dict(short, limits={})); a["sha256"] = R.sha(short)
rc, out = call(a); refused(out, "manifest-structure", "C missing optional key")
odd = dict(GOOD, version=1); fresh(odd); a = args_for(dict(odd, version="1")); a["sha256"] = R.sha(odd)
rc, out = call(a); refused(out, "manifest-structure", "C non-string plain field")
check(store_bytes() is None, "C: nothing written on any path refusal")
ok("C path and structure refusals (store dir, names, symlinks, size, JSON, eight keys)")

# D ── what the human reads
for bad, code in (("0.1\u202e", "unrenderable-value"), ("0.1\x08", "unrenderable-value"), ("0.1\u00e9", "non-ascii-value")):
    m = dict(GOOD, version=bad); fresh(m)
    rc, out = call(args_for(m)); refused(out, code, "D %r" % bad)
fresh(); rc, out = call(args_for(GOOD)); check(out and out["ok"], "D control: the clean version passes")
long = dict(GOOD, files=[{"path": "~/notes/f%03d.md" % i, "mode": "r"} for i in range(30)]); fresh(long)
check(len(R.field_json(long["files"])) > C.MAXPREVIEW, "D setup: files must exceed MAXPREVIEW")
rc, out = call(args_for(long)); refused(out, "value-too-long", "D one value over MAXPREVIEW")

def frame_len(m):
    return len(C.render_frame({"capability": "app.approve", "tier": "T2", "provenance": "TAINTED",
                               "typed_args": args_for(m), "destination": None},
                              first_time=True, for_getty=True, confirm_code="X" * 8))
# Every field under MAXPREVIEW (so no truncation), the frame a little under 4096; the version
# field then walks it across the limit (it renders twice, so each character adds two).
base = dict(GOOD, files=[{"path": "~/notes/n%02d.md" % i, "mode": "r"} for i in range(13)],
            network=["d%02d.example.org" % i for i in range(27)], entry=["python3", "a" * 490 + ".py"])
check(all(len(v) <= C.MAXPREVIEW for v in args_for(base).values()), "D setup: base fields must not truncate")
L = 1
while L < C.MAXPREVIEW and frame_len(dict(base, version="v" * (L + 1))) <= 4096:   # values past 512 truncate
    L += 1
check(L > 1 and L < C.MAXPREVIEW, "D setup: the boundary version length must be reachable (L=%d)" % L)
fits, over = dict(base, version="v" * L), dict(base, version="v" * (L + 1))
check(frame_len(fits) <= 4096 < frame_len(over), "D setup: boundary must straddle 4096")
fresh(over); rc, out = call(args_for(over)); refused(out, "frame-too-long", "D frame over 4096")
fresh(fits); rc, out = call(args_for(fits)); check(out and out["ok"], "D control: one character shorter passes: %r" % out)
fr = C.render_frame({"capability": "app.approve", "tier": "T2", "provenance": "TAINTED", "typed_args": args_for(GOOD),
                     "destination": None}, first_time=True, for_getty=True, confirm_code="X" * 8)
check(all(v in fr for v in args_for(GOOD).values()), "D: every argument value must appear in the frame the human reads")
ok("D render rules: scrub-invariant, ASCII, MAXPREVIEW, whole frame <= 4096 at the boundary; frame shows every field")

# E ── store handling
shutil.rmtree(ROOT, ignore_errors=True); d = os.path.join(APPS, "daily-summary"); os.makedirs(d)
json.dump(GOOD, open(os.path.join(d, "manifest.json"), "w"))
rc, out = call(args_for(GOOD)); refused(out, "store-dir-missing", "E missing dir")
check(not os.path.exists(STORE_DIR), "E: the store dir must not be created")
for content in (b"{corrupt", b"[1, 2]"):
    fresh(); open(STORE, "wb").write(content)
    rc, out = call(args_for(GOOD)); refused(out, "store-corrupt", "E %r" % content)
    check(store_bytes() == content, "E: a refused store must be byte-identical")
fresh(); open(STORE, "w").write(json.dumps({"abc": {"name": "kept", "approved_at": "x"}}))
rc, out = call(args_for(GOOD))
check(out and out["ok"] and set(json.load(open(STORE))) == {"abc", R.sha(GOOD)}, "E: existing entries are kept")
ok("E store: missing dir not created; corrupt/non-object byte-identical; existing entries kept")

# F ── request shape
fresh()
rc, out = call(None, raw="{nope"); refused(out, "bad-request", "F not JSON")
rc, out = call(None, raw=json.dumps({"capability": "app.approve", "arguments": [1]})); refused(out, "bad-request", "F args list")
rc, out = call(dict(args_for(GOOD), extra="x")); refused(out, "arguments", "F extra key")
a = args_for(GOOD); del a["devices"]; rc, out = call(a); refused(out, "arguments", "F missing key")
rc, out = call(dict(args_for(GOOD), version=1)); refused(out, "arguments", "F non-string")
check(store_bytes() is None, "F: nothing written")
ok("F request shape refused before anything is read")

# G ── root guard
if os.geteuid() == 0:
    print("   (G skipped: battery running as root)")
else:
    fresh(); rc, out = call(args_for(GOOD), skip_uid=False); refused(out, "not-root", "G non-root")
    check(store_bytes() is None, "G: nothing written")
ok("G root guard")

# H ── refusal content never echoes arguments
check(REFUSALS, "H setup: refusals recorded")
for c in REFUSALS:
    check(isinstance(c, str) and c.startswith("refused: ") and c[len("refused: "):] in CODES and MARK not in c,
          "H: refusal content must be a fixed literal, got %r" % c)
ok("H %d refusals, all fixed literals, none echoing an argument" % len(REFUSALS))

# I ── helper layout
pkg = os.path.join(TMP, "pkg"); os.makedirs(os.path.join(pkg, "bin")); os.makedirs(os.path.join(pkg, "lib", "agent-os-cap"))
shutil.copyfile(IMPL, os.path.join(pkg, "bin", "cap-app-approve"))
fresh(); rc, out = call(args_for(GOOD), impl=os.path.join(pkg, "bin", "cap-app-approve"))
check(rc == 3 and out == {"ok": False, "content": "refused: helpers-missing", "meta": {}} and store_bytes() is None,
      "I: no helpers -> exit 3, nothing written: rc=%d %r" % (rc, out))
for h in ("agos-run", "confirm"):
    shutil.copyfile(os.path.join(REPO, "bin", h), os.path.join(pkg, "lib", "agent-os-cap", h))
rc, out = call(args_for(GOOD), impl=os.path.join(pkg, "bin", "cap-app-approve"))
check(rc == 0 and out and out["ok"], "I: packaged layout (lib/agent-os-cap) must work: rc=%d %r" % (rc, out))
ok("I helpers: packaged layout works; missing helpers exit 3")

# J ── wiring (textual: the nix side is proven by the registry/cap-sandbox flake checks)
reg = open(os.path.join(REPO, "modules", "capability-registry.nix")).read()
blk = reg[reg.index('"app.approve" = mkCap {'):]; blk = blk[:blk.index("\n    };") + 7]
check('tier = "T2"' in blk and 'impl = "cap-app-approve"' in blk, "J: registry tier/impl")
for k in ("app = \"path\"", "sha256 = \"string\"") + tuple('%s = "string"' % f for f in
          ("name", "version", "entry", "files", "network", "schedule", "limits", "devices")):
    check(k in blk, "J: registry args must declare %s" % k)
check('readOnlyPaths  = [ "/var/lib/agent-os/apps" ]' in blk and 'readWritePaths = [ "/var/lib/agent-os/app-approvals" ]' in blk,
      "J: registry roots")
check('exclusivePaths = { "/var/lib/agent-os/app-approvals" = "app.approve"; }' in reg, "J: exclusivePaths entry")
B = load("broker_b", os.path.join(REPO, "bin", "broker"))
check(B.ORIGIN_BY_CAP.get("app.approve") == B.TRUSTED, "J: broker must map app.approve TRUSTED")
pkgnix = open(os.path.join(REPO, "modules", "cap-invoke-pkg.nix")).read()
check('"app.approve" ]' in pkgnix and "lib/agent-os-cap/agos-run" in pkgnix and "lib/agent-os-cap/confirm" in pkgnix,
      "J: cap-invoke-pkg must ship app.approve with both helper copies")
ok("J wiring: registry decl, exclusivePaths, broker TRUSTED, packaged helpers")

print("app-approve-battery: PASS (%d criteria)" % len(passed))
