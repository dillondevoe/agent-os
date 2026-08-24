#!/usr/bin/env python3
# agos-media-battery.py — Phase 2 media (agos-media) acceptance harness.
# Verifies info <path> -> {ok,path,bytes,media_type,format_name,duration_s,width,height,streams}.
# Needs a real media file to probe; we cannot synthesize a valid media file headlessly,
# so: if AGOS_MEDIA_FIXTURE is set, use it; else SKIP with a clear note. (The Dell gate
# or CI can pass a fixture, e.g. a sample PNG/mp4, to exercise the real ffprobe reshape.)
# Run: AGOS_MEDIA_FIXTURE=/path/to/sample.png PYTHONPATH=modules python3 tests/agos-media-battery.py
import subprocess, json, shutil, sys, os

EX = 0
def check(name, cond, detail=""):
    global EX
    print(("  PASS " if cond else "  FAIL ") + name + (("  — " + detail) if detail else ""))
    if not cond: EX = 1

def run(cmd):
    try:
        o = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return o.returncode, o.stdout.strip(), o.stderr.strip()
    except FileNotFoundError:
        return 127, "", "not found: " + " ".join(cmd)
    except Exception as e:
        return 1, "", str(e)

mcli = shutil.which("agos-media")
if not mcli and os.environ.get("AGENT_OS_STRICT") == "1":
    # Wired into flake.nix as `agos-media-contract`, where the CLI IS on PATH. In that
    # lane an absent tool is a BUILD FAILURE, not a reason to pass: a battery that
    # exits 0 when its subject is missing reports a green about nothing.
    print("  FAIL agos-media-battery: AGENT_OS_STRICT=1 and `agos-media` is not on PATH.")
    sys.exit(1)
if not mcli:
    print("  SKIP agos-media-battery: agos-media not on PATH (image not built).")
    sys.exit(0)

# ---------------------------------------------------------------------------
# ABSENT BACKEND. Shipped 619d3a9 fixed this hand's worst degrade: with ffprobe merely
# NOT INSTALLED it answered {"ok":false,"error":"not a readable media file"} about a
# perfectly valid file it never opened -- a confident, specific, FALSE diagnosis of the
# subject. tests/backend-absence-contract.py fences the SHAPE (a guard exists before the
# call), but static cannot see an exit code: the same morning the guard carried into
# agos-cal was `|| return 0` inside a top-level `case` arm, which returns rc=2 -- the code
# reserved for USAGE errors -- with the static contract fully satisfied. Only a run caught
# that, and the run was ad-hoc.
#
# DELIBERATELY PLACED ABOVE THE FIXTURE SKIP. Every other arm in this file needs a real
# media file, so a lane without AGOS_MEDIA_FIXTURE exits 0 here having asserted NOTHING --
# a skip that reads as coverage, which is the shape this repo keeps finding. This arm needs
# no media and no ffprobe: the subject is never reached and the override names a binary that
# cannot exist, so it asserts the same thing on every host.
import tempfile as _tf
_probe = _tf.NamedTemporaryFile("wb", suffix=".mp4", delete=False)
_probe.write(b"\x00\x00\x00\x18ftypmp42"); _probe.close()
denv = dict(os.environ, AGOS_MEDIA_FFPROBE="no-such-ffprobe-binary-xyz")
o = subprocess.run([mcli, "info", _probe.name], capture_output=True, text=True, timeout=30, env=denv)
drc, dout = o.returncode, o.stdout.strip()
# exit 2 is USAGE. An uninstalled backend is not the caller holding the tool wrong.
check("absent backend: `info` exits 0, not 2 (it is not a usage error)", drc == 0,
      "rc=%s out=%s" % (drc, dout[:60]))
try:
    db = json.loads(dout)
except Exception as e:
    db = None
    check("absent backend: `info` -> parseable JSON on STDOUT", False, str(e) + " | " + dout[:60])
if isinstance(db, dict):
    check("absent backend: `info` -> ok:false + a reason",
          db.get("ok") is False and db.get("error"), dout[:80])
    # THE LOAD-BEARING ONE. The pre-fix code ALSO answered ok:false with a reason -- the
    # reason was just a lie about the subject. What changed is WHO gets blamed.
    _err = str(db.get("error", "")).lower()
    check("absent backend: the reason blames the BACKEND, not the subject",
          "absent" in _err and "readable media" not in _err, dout[:80])
os.unlink(_probe.name)

fixture = os.environ.get("AGOS_MEDIA_FIXTURE")
if not fixture or not os.path.isfile(fixture):
    print("  SKIP agos-media-battery: no fixture. Set AGOS_MEDIA_FIXTURE=/path/to/sample.png|mp4")
    print("  (set it, or run the wired `agos-media-contract` lane, which synthesizes one with ffmpeg).")
    sys.exit(0)

print("  using REAL agos-media CLI: " + mcli + "  fixture=" + fixture)

# The fixture's own facts, read from the filesystem rather than from the hand. Every arm
# below reads stdout or the exit code — the hand's account of itself — so an implementation
# that reported plausible numbers for a file it never opened passed all of them.
import hashlib
fixture_bytes = os.path.getsize(fixture)
fixture_sha = hashlib.sha256(open(fixture, "rb").read()).hexdigest()

rc, out, err = run([mcli, "info", fixture])
# Exit code, not just stdout. The hands have ONE uniform contract — `exit 2` is reserved
# for usage errors, and EVERY degraded path is `return 0` with an {ok:false} body — so any
# arm that is not probing usage must see rc 0. Until 2026-08-24 the only rc assertions on
# this surface were `rc == 2` ones: the batteries checked that bad input fails loudly and
# never once that good input succeeds quietly. That is exactly the gap `agos-notes list`
# lived in — valid JSON on stdout, rc 1 underneath, green lane.
check("`info` exits 0", rc == 0, "rc=%s err=%s" % (rc, err[:60]))
try:
    d = json.loads(out)
    check("info -> valid JSON {ok:bool}", d.get("ok") in (True, False), out[:60])
    check("info -> media_type in {image,video,audio,other}", d.get("media_type") in ("image","video","audio","other"), str(d.get("media_type")))
    check("info -> streams is array", isinstance(d.get("streams"), list), str(d.get("streams"))[:40])
    # Not "bytes is an int" — the RIGHT int, checked against the file itself.
    check("info -> bytes matches the file on disk", d.get("bytes") == fixture_bytes,
          "hand=%s disk=%s" % (d.get("bytes"), fixture_bytes))
except Exception as e:
    check("info parses", False, str(e) + " | out=" + out[:80])

# agos-media info is a READ-only inspection; that was prose until 2026-08-24.
check("read-only: the fixture is byte-identical afterwards",
      hashlib.sha256(open(fixture, "rb").read()).hexdigest() == fixture_sha, fixture_sha[:16])

# The degrade path, on all three channels — it had no arm of any kind. Contract: {ok:false}
# with a reason on STDOUT, rc 0, and stderr silent (ffprobe's own complaint is swallowed by
# design, and an arm reading only stdout cannot tell "swallowed" from "leaked").
rc, out, err = run([mcli, "info", "/nonexistent-agos-media-fixture.png"])
check("`info <missing>` exits 0", rc == 0, "rc=%s err=%s" % (rc, err[:60]))
check("info <missing> -> says nothing on stderr", err == "", repr(err[:80]))
try:
    md = json.loads(out)
    check("info <missing> -> ok:false + a reason on STDOUT",
          md.get("ok") is False and md.get("error"), out[:60])
except Exception as e:
    check("info-missing parses", False, str(e))

print("agos-media-battery: " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
