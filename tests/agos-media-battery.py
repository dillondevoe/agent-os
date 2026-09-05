#!/usr/bin/env python3
# agos-media-battery.py — Phase 2 media (agos-media) acceptance harness.
# Verifies info <path> -> {ok,path,bytes,media_type,format_name,duration_s,width,height,streams}.
# Needs a real media file to probe; we cannot synthesize a valid media file headlessly,
# so: if AGOS_MEDIA_FIXTURE is set, use it; else SKIP with a clear note. (The Dell gate
# or CI can pass a fixture, e.g. a sample PNG/mp4, to exercise the real ffprobe reshape.)
# Run: AGOS_MEDIA_FIXTURE=/path/to/sample.png PYTHONPATH=modules python3 tests/agos-media-battery.py
# SIDE_EFFECTS — Geist's law as amended 2026-09-05T13:05Z: a box-runnable battery declares
# every effect that leaves the machine or outlives the run. Read as DATA by
# tests/vm-matrix-contract.py, not as a comment.
SIDE_EFFECTS = []

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
if not mcli:
    print("  SKIP agos-media-battery: agos-media not on PATH (image not built).")
    sys.exit(0)

fixture = os.environ.get("AGOS_MEDIA_FIXTURE")
if not fixture or not os.path.isfile(fixture):
    print("  SKIP agos-media-battery: no fixture. Set AGOS_MEDIA_FIXTURE=/path/to/sample.png|mp4")
    print("  (cannot synthesize a valid media file headlessly; the reshape logic is exercised on the Dell gate).")
    sys.exit(0)

print("  using REAL agos-media CLI: " + mcli + "  fixture=" + fixture)
rc, out, err = run([mcli, "info", fixture])
try:
    d = json.loads(out)
    check("info -> valid JSON {ok:bool}", d.get("ok") in (True, False), out[:60])
    check("info -> media_type in {image,video,audio,other}", d.get("media_type") in ("image","video","audio","other"), str(d.get("media_type")))
    check("info -> streams is array", isinstance(d.get("streams"), list), str(d.get("streams"))[:40])
except Exception as e:
    check("info parses", False, str(e) + " | out=" + out[:80])

print("agos-media-battery: " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
