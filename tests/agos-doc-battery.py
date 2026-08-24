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

# info -> valid JSON, ok bool, pages int
rc, out, err = run([dcli, "info", p.name])
try:
    d = json.loads(out)
    check("info -> valid JSON {ok:bool}", d.get("ok") in (True, False), out[:60])
    check("info -> pages is int", isinstance(d.get("pages"), int), str(d.get("pages")))
    check("info -> bytes is int", isinstance(d.get("bytes"), int) and d["bytes"] > 0, str(d.get("bytes")))
except Exception as e:
    check("info parses", False, str(e) + " | out=" + out[:80])

# text -> valid JSON (whole-doc extract)
rc, out, err = run([dcli, "text", p.name])
try:
    d = json.loads(out)
    check("text -> valid JSON {ok:bool}", d.get("ok") in (True, False), out[:60])
except Exception as e:
    check("text parses", False, str(e))

os.unlink(p.name)
print("agos-doc-battery: " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
