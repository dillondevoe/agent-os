#!/usr/bin/env python3
# agos-web-battery.py — Phase 2 web (agos-web) acceptance harness.
# Verifies the read-only contract: fetch <url> -> {ok,url,title,text,chars,author,date}.
# Two checks, one needs no network:
#   1) http(s)-only guard: fetch 'file://...' -> ok:false "url must be http(s)" (OFFLINE, always runs)
#   2) live fetch of a public URL -> valid JSON with ok bool (either true or false is valid;
#      a network failure surfaces as ok:false, which is still the correct contract)
# Runs SKIP if agos-web isn't on PATH (image not built in this env).
# Run: PYTHONPATH=modules python3 tests/agos-web-battery.py
# MUTATES_SHARED_STATE — Geist's law, 2026-09-05: a box-runnable battery never mutates
# human-shared state. Read as DATA by tests/vm-matrix-contract.py, not as a comment.
MUTATES_SHARED_STATE = False

import subprocess, json, shutil, sys

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

wcli = shutil.which("agos-web")
if not wcli:
    print("  SKIP agos-web-battery: agos-web not on PATH (image not built).")
    sys.exit(0)
print("  using REAL agos-web CLI: " + wcli)

# 1) http(s)-only guard — OFFLINE, always assertable
rc, out, err = run([wcli, "fetch", "file:///etc/hostname"])
try:
    d = json.loads(out)
    check("fetch file:// -> ok:false + http(s) refusal", d.get("ok") is False and "http(s)" in str(d.get("error","")), out[:60])
except Exception as e:
    check("guard parses", False, str(e))

# 2) THE ARM THAT USED TO REACH THE INTERNET, AND WHY IT NO LONGER DOES.
# This was a live fetch of example.com (scheme omitted here on purpose: a future sweep that
# greps tests for non-loopback URLs must not trip on the comment EXPLAINING the absence of one
# — see grep-matches-prose-about-the-literal). Its own comment said the quiet part: "network may
# or may not succeed; both are the contract". Both assertions below — {ok:bool} and the echoed
# url — are satisfied by the FAILURE envelope as much as the success one, so the arm never
# needed the network to discriminate anything. It simply made a live outbound request to a
# third party, from the operator's IP, on every run of the battery, for nothing. Verified on
# the Dell 2026-09-05: it returned ok:true with that host's real body.
#
# This is Augur's class (2026-09-04): AN ARM WHOSE SAFETY RESTS ON A BINARY BEING ABSENT is
# green-by-accident in CI and armed everywhere the image IS built. In CI `agos-web` is missing
# and the battery SKIPs at the top, so the egress never happened where anyone was looking. It
# happened on the Dell. Same shape as frontdoor-kick 12b, which called the real `claude` CLI
# and billed the operator's account, and passed CI only by the binary's absence.
#
# The replacement splits the arm in two and covers MORE than the original, offline:
#   2a drives the failure envelope deterministically (loopback, refused fast, still http(s)
#      so the guard passes it through to curl) — asserting exactly what the old arm asserted;
#   2b drives the SUCCESS path against a page this process serves on loopback — which the old
#      arm only reached by luck of the network, and never asserted anything about.

# 2a) http(s) accepted by the guard, fetch fails, envelope is well-formed. No egress.
#
# The address is REFUSED, not unroutable, and the distinction is the reason it is the right
# choice (Augur, 2026-09-05 — the comment previously credited the property it does not use).
# Nothing listens on loopback port 1, so the connect gets an instant RST and curl returns at
# once. A genuinely UNROUTABLE address is the one that would block until the 20s --max-time
# cap, turning every run of this battery into a 20-second stall. Refused-fast is the property;
# unroutable would have been the bug.
DEAD = "https://127.0.0.1:1/"
rc, out, err = run([wcli, "fetch", DEAD])
try:
    d = json.loads(out)
    check("fetch https (refused, no egress) -> valid JSON {ok:bool}", d.get("ok") in (True, False), out[:60])
    check("fetch https (refused, no egress) -> echoes url field", d.get("url") == DEAD, str(d.get("url")))
    check("...and it is the FAILURE envelope, so 2a is not passing on a lucky success",
          d.get("ok") is False, out[:80])
except Exception as e:
    check("refused fetch parses", False, str(e) + " | out=" + out[:60])

# 2b) SUCCESS path, served from this process on loopback — the coverage the old arm never
# actually asserted. trafilatura needs real prose or it returns "no readable content", so the
# fixture carries a paragraph rather than a stub.
PAGE = b"""<!doctype html><html><head><title>Loopback Fixture Page</title></head><body>
<nav>home about contact</nav>
<article><h1>Loopback Fixture Page</h1>
<p>This paragraph exists so that the readability pass has genuine prose to extract rather than
boilerplate. It is served by the battery itself on the loopback interface, so the success path
of agos-web fetch can be exercised without any request leaving the machine. The extractor should
strip the navigation above and return this body text along with the document title.</p>
</article><footer>copyright nobody</footer></body></html>"""

import http.server, threading, socket
class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)
    def log_message(self, *a): pass

srv = http.server.HTTPServer(("127.0.0.1", 0), _H)
port = srv.server_address[1]
t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
try:
    local = "http://127.0.0.1:%d/" % port
    rc, out, err = run([wcli, "fetch", local])
    try:
        d = json.loads(out)
        check("fetch http (loopback fixture) -> ok:true", d.get("ok") is True, out[:80])
        check("...extracts the title", d.get("title") == "Loopback Fixture Page", str(d.get("title")))
        check("...extracts body prose and counts it", isinstance(d.get("chars"), int) and d.get("chars") > 100,
              str(d.get("chars")))
        check("...and strips the nav boilerplate", "home about contact" not in str(d.get("text","")),
              str(d.get("text",""))[:60])
    except Exception as e:
        check("loopback fetch parses", False, str(e) + " | out=" + out[:60])
finally:
    srv.shutdown(); srv.server_close()

print("agos-web-battery: " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
