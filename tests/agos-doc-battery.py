#!/usr/bin/env python3
# agos-doc-battery.py — Phase 2 document reader (agos-doc) acceptance harness.
# Verifies info/text over a PDF. We synthesize a minimal valid PDF in-temp (no external
# fixture needed); if pdfinfo isn't present the CLI is absent and we SKIP. The contract
# is: info <path> -> {ok,path,pages,title,author,pdf_version,page_size,bytes}.
# Run: PYTHONPATH=modules python3 tests/agos-doc-battery.py
import subprocess, json, shutil, sys, tempfile, os

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

dcli = shutil.which("agos-doc")
if not dcli and os.environ.get("AGENT_OS_STRICT") == "1":
    # Wired into flake.nix as `agos-doc-contract`, where the CLI IS on PATH. In that
    # lane an absent tool is a BUILD FAILURE, not a reason to pass: a battery that
    # exits 0 when its subject is missing reports a green about nothing.
    print("  FAIL agos-doc-battery: AGENT_OS_STRICT=1 and `agos-doc` is not on PATH.")
    sys.exit(1)
if not dcli:
    print("  SKIP agos-doc-battery: agos-doc not on PATH (image not built / poppler absent).")
    sys.exit(0)
print("  using REAL agos-doc CLI: " + dcli)

# Minimal valid single-page PDF (hand-built; pdfinfo reads Pages + bytes).
pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n" \
      b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n" \
      b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n" \
      b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n" \
      b"0000000058 00000 n \n0000000110 00000 n \ntrailer<</Size 4/Root 1 0 R>>\n" \
      b"startxref\n178\n%%EOF\n"
p = tempfile.NamedTemporaryFile("wb", suffix=".pdf", delete=False)
p.write(pdf); p.close()

# The fixture's exact bytes. agos-doc is a READ-ONLY hand — `info` and `text` inspect a
# document and must not alter it — and until 2026-08-24 that was prose. Every arm below
# reads stdout or the exit code, both of which are the hand's own account of itself; a
# converter that rewrote the PDF in place would pass all of them. The inverse of the
# agos-notes case fixed the same day: there the side effect MUST happen, here it must NOT.
import hashlib
before_sha = hashlib.sha256(open(p.name, "rb").read()).hexdigest()

# info -> valid JSON, ok bool, pages int
rc, out, err = run([dcli, "info", p.name])
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
    # SHAPE, NOT VALUE — and the value is knowable here, which is what makes the shape
    # check insufficient rather than merely weak. The fixture PDF is hand-built a few lines
    # up by this very file: it declares `/Count 1`, so its page count is 1 by construction,
    # and its size on disk is one os.path.getsize away. Asserting "is an int" and "> 0"
    # accepts a hand that reported 7 pages and someone else's byte count. Same correction as
    # agos-media's `bytes` arm: not "is an int", the RIGHT int, checked against the file.
    check("info -> pages is exactly the 1 page the fixture declares",
          d.get("pages") == 1, str(d.get("pages")))
    check("info -> bytes matches the file on disk",
          d.get("bytes") == os.path.getsize(p.name),
          "hand=%s disk=%s" % (d.get("bytes"), os.path.getsize(p.name)))
except Exception as e:
    check("info parses", False, str(e) + " | out=" + out[:80])

# text -> valid JSON (whole-doc extract)
rc, out, err = run([dcli, "text", p.name])
check("`text` exits 0", rc == 0, "rc=%s err=%s" % (rc, err[:60]))
try:
    d = json.loads(out)
    check("text -> valid JSON {ok:bool}", d.get("ok") in (True, False), out[:60])
except Exception as e:
    check("text parses", False, str(e))

# The degrade path, on all three channels. `info` on a file that is not there is the most
# likely thing a caller hits, and the battery had no arm for it: contract says {ok:false} on
# STDOUT, rc 0, stderr silent — pdfinfo's own complaint is swallowed by design, and an arm
# that reads only stdout cannot tell "swallowed" from "leaked".
rc, out, err = run([dcli, "info", "/nonexistent-agos-doc-fixture.pdf"])
check("`info <missing>` exits 0", rc == 0, "rc=%s err=%s" % (rc, err[:60]))
check("info <missing> -> says nothing on stderr", err == "", repr(err[:80]))
try:
    md = json.loads(out)
    check("info <missing> -> ok:false + a reason on STDOUT",
          md.get("ok") is False and md.get("error"), out[:60])
except Exception as e:
    check("info-missing parses", False, str(e))

check("read-only: the fixture PDF is byte-identical afterwards",
      hashlib.sha256(open(p.name, "rb").read()).hexdigest() == before_sha, before_sha[:16])

os.unlink(p.name)
print("agos-doc-battery: " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
