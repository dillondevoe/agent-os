#!/usr/bin/env python3
# tests/agos-net-battery.py — CONTRACT BATTERY for the app network hand: bin/agos-net (host side),
# bin/agos-fetch (sandbox side) and the agos-run wiring (docs/design/app-manifest.md §5).
#
# The claim under test: an app with a `network` list reaches EXACTLY those domains, read-only,
# through the hand, and nothing else; an app without one has no socket at all. Positive arms use
# a local HTTP upstream through the hand's TESTS-ONLY --test-upstream override; every control
# arm asserts the upstream saw NO request.
#
# Acceptance criteria:
#   A. GET to an allowed Host returns the upstream body and status; the hand logs one line.
#   B. CONTROL: a Host not in the list -> 403, upstream untouched. Subdomain of an allowed
#      domain -> 403. Host with a port -> 403. No Host -> 400.
#   C. CONTROL: POST -> 405, upstream untouched. A request body -> 400.
#   D. A 302 from upstream comes back as 302 with Location, NOT followed (upstream saw one
#      request, not two); agos-fetch exits 3 on it.
#   E. Body over MAX_BODY_BYTES is cut at the cap with X-AgentOS-Truncated.
#   F. agos-fetch: GET body to stdout, --head prints headers, -o writes a file, exit 4 on a
#      refused 403, exit 2 with no AGOS_NET_SOCKET.
#   G. agos-run --dry-run: a manifest WITH network binds the socket dir and sets
#      AGOS_NET_SOCKET + AGOS_FETCH, still --unshare-net; WITHOUT network none of those appear.
#   H. REAL RUN (when bwrap works here): under agos-run with network ["allowed.test"], the app
#      can reach the hand's socket (a fetch to a non-listed domain gets the hand's 403, so the
#      socket is live inside the sandbox) and a direct TCP connect from inside fails (no net).
#      The hand is gone after the app exits.
#   I. Without --test-upstream, a Host that resolves to loopback is refused 403 before any
#      connection (the cap-net-fetch deny list is in force).
#
# stdlib only. Exit 0 all-pass; AssertionError otherwise.
SIDE_EFFECTS = []  # scratch dirs under mktemp; local-only sockets and a 127.0.0.1 listener; removed at exit

import http.client, http.server, json, os, shutil, socket, subprocess, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.abspath(os.path.join(HERE, "..", "bin"))
NET = os.path.join(BIN, "agos-net"); FETCH = os.path.join(BIN, "agos-fetch"); RUN = os.path.join(BIN, "agos-run")
TMP = tempfile.mkdtemp(prefix="agos-net-battery.")
SEEN = []


def check(c, msg):
    if not c: raise AssertionError(msg)


class Upstream(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        SEEN.append((self.command, self.headers.get("Host"), self.path))
        if self.path == "/moved":
            self.send_response(302); self.send_header("Location", "https://elsewhere.test/x"); self.send_header("Content-Length", "0"); self.end_headers(); return
        if self.path == "/big":
            body = b"x" * (4 * 1024 * 1024 + 100)
        else:
            body = ("hello from %s%s" % (self.headers.get("Host"), self.path)).encode()
        self.send_response(200); self.send_header("Content-Type", "text/plain"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_HEAD(self):
        SEEN.append((self.command, self.headers.get("Host"), self.path))
        self.send_response(200); self.send_header("Content-Type", "text/plain"); self.send_header("Content-Length", "5"); self.end_headers()
    def do_POST(self):
        SEEN.append((self.command, self.headers.get("Host"), self.path)); self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()


up = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
threading.Thread(target=up.serve_forever, daemon=True).start()
UPSTREAM = "http://127.0.0.1:%d" % up.server_address[1]


def start_hand(sock, allow, test_upstream=UPSTREAM, app="t"):
    args = [sys.executable, NET, "serve", sock, "--app", app, "--allow", allow]
    if test_upstream: args += ["--test-upstream", test_upstream]
    p = subprocess.Popen(args, stderr=subprocess.PIPE, text=True)
    for _ in range(100):
        if os.path.exists(sock): break
        time.sleep(0.05)
    if not os.path.exists(sock):  # message built only on failure: reading a live hand's stderr blocks
        p.kill(); raise AssertionError("hand did not start: %s" % p.stderr.read())
    return p


def raw(sock, method, path, host=None, body=None, extra=None):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.connect(sock)
    req = "%s %s HTTP/1.1\r\n" % (method, path)
    if host is not None: req += "Host: %s\r\n" % host
    for k, v in (extra or {}).items(): req += "%s: %s\r\n" % (k, v)
    if body is not None: req += "Content-Length: %d\r\n" % len(body)
    req += "Connection: close\r\n\r\n"
    s.sendall(req.encode() + (body or b""))
    data = b""
    while True:
        chunk = s.recv(65536)
        if not chunk: break
        data += chunk
    s.close()
    head, _, rest = data.partition(b"\r\n\r\n")
    status = int(head.split(b" ")[1]); hdrs = {}
    for line in head.split(b"\r\n")[1:]:
        k, _, v = line.decode().partition(":"); hdrs[k.strip().lower()] = v.strip()
    return status, hdrs, rest


def fetch(sock, *args):
    env = dict(os.environ); env["AGOS_NET_SOCKET"] = sock
    return subprocess.run([sys.executable, FETCH] + list(args), capture_output=True, text=True, env=env, cwd=TMP)


try:
    sock = os.path.join(TMP, "a.sock")
    hand = start_hand(sock, "allowed.test,other.test")

    # A
    st, h, body = raw(sock, "GET", "/feed?x=1", "allowed.test")
    check(st == 200 and body == b"hello from allowed.test/feed?x=1", "A: %s %r" % (st, body[:60]))
    check(SEEN == [("GET", "allowed.test", "/feed?x=1")], "A: upstream saw %r" % SEEN)
    print("A. allowed GET forwarded with Host and path")

    # B
    SEEN.clear()
    for host, want in (("evil.test", 403), ("sub.allowed.test", 403), ("allowed.test:8443", 403), (None, 400), ("ALLOWED.test", 200)):
        st, _, _ = raw(sock, "GET", "/", host)
        check(st == want, "B: host %r -> %d, want %d" % (host, st, want))
    check(SEEN == [("GET", "allowed.test", "/")], "B: upstream saw %r (only the case-folded allowed one expected)" % SEEN)
    print("B. unlisted / subdomain / port / missing Host refused; upstream untouched")

    # C
    SEEN.clear()
    st, _, _ = raw(sock, "POST", "/", "allowed.test", body=b"{}"); check(st == 405, "C: POST %d" % st)
    st, _, _ = raw(sock, "GET", "/", "allowed.test", body=b"x"); check(st == 400, "C: GET with body %d" % st)
    check(SEEN == [], "C: upstream saw %r" % SEEN)
    print("C. POST and request bodies refused; upstream untouched")

    # D
    SEEN.clear()
    st, h, _ = raw(sock, "GET", "/moved", "allowed.test")
    check(st == 302 and h.get("location") == "https://elsewhere.test/x" and SEEN == [("GET", "allowed.test", "/moved")], "D: %d %r %r" % (st, h.get("location"), SEEN))
    r = fetch(sock, "http://allowed.test/moved"); check(r.returncode == 3 and "redirect not followed" in r.stderr, "D: agos-fetch %d %s" % (r.returncode, r.stderr))
    print("D. redirect returned, not followed")

    # E
    st, h, body = raw(sock, "GET", "/big", "allowed.test")
    check(st == 200 and len(body) == 4 * 1024 * 1024 and h.get("x-agentos-truncated"), "E: %d %d %r" % (st, len(body), h.get("x-agentos-truncated")))
    print("E. body capped with X-AgentOS-Truncated")

    # F
    r = fetch(sock, "http://allowed.test/a"); check(r.returncode == 0 and r.stdout == "hello from allowed.test/a", "F: get %d %r" % (r.returncode, r.stdout))
    r = fetch(sock, "--head", "http://allowed.test/a"); check(r.returncode == 0 and "Content-Type: text/plain" in r.stdout, "F: head %r" % r.stdout)
    r = fetch(sock, "-o", os.path.join(TMP, "out.txt"), "http://other.test/b"); check(r.returncode == 0 and open(os.path.join(TMP, "out.txt")).read() == "hello from other.test/b", "F: -o")
    r = fetch(sock, "http://evil.test/"); check(r.returncode == 4 and "403" in r.stderr, "F: refused %d %s" % (r.returncode, r.stderr))
    env = dict(os.environ); env.pop("AGOS_NET_SOCKET", None)
    r = subprocess.run([sys.executable, FETCH, "http://allowed.test/"], capture_output=True, text=True, env=env)
    check(r.returncode == 2 and "no network" in r.stderr, "F: no socket %d" % r.returncode)
    print("F. agos-fetch get/head/-o/refused/no-socket")

    hand.terminate(); hand.wait(timeout=5)
    log = hand.stderr.read()
    check("GET allowed.test/feed?x=1 -> 200" in log and "evil.test/ -> 403 REFUSED" in log, "A/B: hand log missing lines: %r" % log[:300])

    # G: dry-run shape
    HOME = os.path.join(TMP, "home"); APPS = os.path.join(HOME, ".local/share/agent-os/apps")
    FAKE = os.path.join(TMP, "fakebin"); os.makedirs(FAKE)
    open(os.path.join(FAKE, "bwrap"), "w").write("#!/bin/sh\nexit 0\n"); os.chmod(os.path.join(FAKE, "bwrap"), 0o755)
    ENV = dict(os.environ, HOME=HOME, AGOS_RUN_NO_SYSTEMD="1", PATH=FAKE + os.pathsep + os.environ.get("PATH", ""))
    def mk(name, m):
        d = os.path.join(APPS, name); os.makedirs(d, exist_ok=True); json.dump(m, open(os.path.join(d, "manifest.json"), "w")); return d
    base = {"name": "netapp", "version": "0.1", "entry": ["sh", "-c", "true"], "files": [], "network": ["allowed.test"], "devices": [],
            "limits": {"cpu_pct": 10, "mem_mb": 64, "wall_s": 30}, "schedule": ""}
    d = mk("netapp", base)
    r = subprocess.run([sys.executable, RUN, d, "--dry-run", "--approve-for-test"], capture_output=True, text=True, env=ENV)
    check(r.returncode == 0, "G: dry %s" % r.stderr); j = json.loads(r.stdout); a = j["argv"]
    check("--unshare-net" in a and "AGOS_NET_SOCKET" in a and "AGOS_FETCH" in a and a[a.index("AGOS_FETCH") + 1] == FETCH, "G: net argv %r" % a)
    si = a.index("AGOS_NET_SOCKET"); sockdir = os.path.dirname(a[si + 1])
    check(a[a.index("--bind", a.index("--tmpfs")) + 1] == sockdir or sockdir in a, "G: socket dir not bound: %r" % a)
    check("via agos-net hand" in r.stderr and "DENIED" not in r.stderr, "G: notice %r" % r.stderr)
    d2 = mk("netapp", dict(base, network=[]))
    r = subprocess.run([sys.executable, RUN, d2, "--dry-run", "--approve-for-test"], capture_output=True, text=True, env=ENV)
    a = json.loads(r.stdout)["argv"]
    check("AGOS_NET_SOCKET" not in a and "AGOS_FETCH" not in a and not [x for x in a if x.endswith("/net.sock")], "G: no-network argv leaks the hand: %r" % a)
    print("G. dry-run: socket + env only with a network list; sandbox still --unshare-net")

    # H: real run
    def bwrap_works():
        b = shutil.which("bwrap")
        if not b: return False
        return subprocess.run([b, "--ro-bind", "/", "/", "--unshare-all", "--", "/bin/true"], capture_output=True).returncode == 0
    if bwrap_works():
        script = ('r=$("$AGOS_FETCH" http://evil.test/ 2>&1); echo "fetch-rc=$? $r" | head -c 200; echo; '
                  'python3 -c "import socket;s=socket.socket();s.settimeout(2);s.connect((\'127.0.0.1\',%d))" 2>/dev/null && echo direct-ok || echo direct-fail; '
                  'test -S "$AGOS_NET_SOCKET" && echo sock-present' % up.server_address[1])
        d3 = mk("netapp", dict(base, entry=["sh", "-c", script]))
        envH = dict(ENV, PATH=os.environ.get("PATH", ""))
        r = subprocess.run([sys.executable, RUN, d3, "--approve-for-test"], capture_output=True, text=True, env=envH, timeout=120)
        out = r.stdout
        check("fetch-rc=4" in out and "403" in out and "not in the approved manifest" in out, "H: in-sandbox fetch to unlisted domain: rc=%d out=%r err=%r" % (r.returncode, out, r.stderr[-400:]))
        check("direct-fail" in out and "sock-present" in out, "H: sandbox net shape: %r" % out)
        check("agos-net[netapp]" in r.stderr and "evil.test/ -> 403" in r.stderr, "H: hand log on host: %r" % r.stderr[-400:])
        left = [p for p in os.listdir(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir())) if p.startswith("agos-net.")] if os.path.isdir(os.environ.get("XDG_RUNTIME_DIR", "")) else []
        check(not left, "H: socket dirs left behind: %r" % left)
        print("H. REAL RUN: hand reachable inside the sandbox, direct network is not, hand cleaned up")
    else:
        print("H. real run SKIPPED (bwrap cannot create user namespaces here)")

    # I: deny list without test upstream
    sock2 = os.path.join(TMP, "b.sock")
    hand2 = start_hand(sock2, "localhost", test_upstream=None)
    st, _, body = raw(sock2, "GET", "/", "localhost")
    check(st == 403 and b"denied" in body, "I: %d %r" % (st, body[:80]))
    hand2.terminate(); hand2.wait(timeout=5)
    print("I. a listed domain resolving to loopback is refused before connecting")

    print("agos-net-battery: PASS (9 criteria)")
finally:
    up.shutdown(); shutil.rmtree(TMP, ignore_errors=True)
