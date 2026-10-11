#!/usr/bin/env python3
# tests/agos-ask-battery.py — the CONTRACT BATTERY for modules/agos_ask.py, quick-ask
# (docs/design/surfaces-and-first-login.md §7). A fake model on a loopback port records every request;
# fake launcher / wl-paste / wl-copy / notify-send on PATH record their argv and stdin.
#
#   A. One call, exact shape: keys are {model, stream, think, messages} — NO `tools` — with the
#      configured model, stream and think off, a system and a user message; the answer is printed.
#   B. endpoint(): only a loopback LITERAL is accepted (v4 and v6); a routable address, a name
#      (even `localhost`), and a host with no port are refused, and no request is made.
#   C. A redirect is refused: the second server is never contacted.
#   D. An oversize response is refused.
#   E. Garbage, a missing field, an empty answer, a dead port: (False, reason), never an exception.
#   F. The trace line carries sizes and outcome only — never the question, the context or the answer —
#      and goes to syslog.
#   G. No files: main() opens nothing for writing and leaves cwd and HOME empty.
#   H. Launcher mode: the selection is added as context (capped), the clipboard gets the whole answer,
#      the notification is capped at 500 characters.
#   I. Terminal mode never reads the selection.
#   J. A cancelled prompt makes no request and leaves no trace.
#   K. The question is capped at 4000 characters.
#   L. The repo copy (placeholders unsubstituted) says "not configured" and makes no request; each
#      placeholder appears exactly once.
#   M. A proxy in the environment is ignored.
#
# stdlib only.

SIDE_EFFECTS = []  # loopback sockets and a temp dir, removed at exit

import atexit, builtins, importlib.util, io, json, os, shutil, sys, tempfile, threading, types
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.abspath(os.path.join(HERE, "..", "modules", "agos_ask.py"))
spec = importlib.util.spec_from_file_location("agos_ask", MOD)
A = importlib.util.module_from_spec(spec); spec.loader.exec_module(A)

TMP = tempfile.mkdtemp(prefix="agos-ask-battery.")
atexit.register(shutil.rmtree, TMP, True)
BIN = os.path.join(TMP, "bin"); os.makedirs(BIN)
LOG = os.path.join(TMP, "calls.log")
REAL_PATH = os.environ.get("PATH", "")
passed = []


def check(c, msg):
    if not c:
        raise AssertionError(msg)


def ok(tag):
    passed.append(tag); print("  ok  " + tag)


class Fake:
    """A loopback HTTP server whose reply is set per arm; records request bodies."""
    def __init__(self):
        self.hits, self.reply = [], (200, {}, b"{}")
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                outer.hits.append((self.path, self.rfile.read(n)))
                code, headers, body = outer.reply
                self.send_response(code)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)
            do_GET = do_POST

            def log_message(self, *a):
                pass
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def answer(self, text):
        self.reply = (200, {"Content-Type": "application/json"},
                      json.dumps({"message": {"role": "assistant", "content": text}}).encode())


S, S2 = Fake(), Fake()
atexit.register(S.srv.shutdown); atexit.register(S2.srv.shutdown)


def configure(host=None, model="test-model:1", launcher=""):
    os.environ["AGOS_ASK_HOST"] = host if host is not None else "127.0.0.1:%d" % S.port
    os.environ["AGOS_ASK_MODEL"] = model
    if launcher:
        os.environ["AGOS_ASK_LAUNCHER"] = launcher
    else:
        os.environ.pop("AGOS_ASK_LAUNCHER", None)
    S.hits.clear(); S2.hits.clear()


def fake_tool(name, stdout="", rc=0):
    """A PATH tool that appends its name, argv and stdin to LOG as one JSON line."""
    p = os.path.join(BIN, name)
    with open(p, "w") as f:
        f.write("#!%s\nimport json,sys\n"
                "open(%r,'a').write(json.dumps([%r, sys.argv[1:], sys.stdin.read()])+'\\n')\n"
                "sys.stdout.write(%r); sys.exit(%d)\n" % (sys.executable, LOG, name, stdout, rc))
    os.chmod(p, 0o755)


def tools(**out):
    shutil.rmtree(BIN); os.makedirs(BIN)
    if os.path.exists(LOG):
        os.unlink(LOG)
    for name, v in out.items():
        fake_tool(name.replace("_", "-"), *(v if isinstance(v, tuple) else (v,)))
    os.environ["PATH"] = BIN


def calls():
    if not os.path.exists(LOG):
        return []
    return [json.loads(line) for line in open(LOG)]


class FakeSyslog(types.ModuleType):
    LOG_USER, LOG_INFO = 8, 6

    def __init__(self):
        super().__init__("syslog"); self.lines = []

    def openlog(self, *a):
        pass

    def syslog(self, prio, line):
        self.lines.append(line)


def run_main(argv):
    """main() with stdout/stderr captured and a recording syslog. Returns (rc, out, err, syslog lines)."""
    fs = FakeSyslog(); real = sys.modules.get("syslog"); sys.modules["syslog"] = fs
    o, e, so, se = io.StringIO(), io.StringIO(), sys.stdout, sys.stderr
    sys.stdout, sys.stderr = o, e
    try:
        rc = A.main(["agos-ask"] + argv)
    finally:
        sys.stdout, sys.stderr = so, se
        if real is not None:
            sys.modules["syslog"] = real
        else:
            sys.modules.pop("syslog", None)
    return rc, o.getvalue(), e.getvalue(), fs.lines


# A — the one call, exact shape
configure(); tools(); S.answer("  four  ")
rc, out, err, lines = run_main(["what is two plus two"])
check(rc == 0 and out == "four\n" and err == "", "A: rc/out: %r %r %r" % (rc, out, err))
check(len(S.hits) == 1 and S.hits[0][0] == "/api/chat", "A: exactly one POST to /api/chat: %r" % S.hits)
body = json.loads(S.hits[0][1])
check(sorted(body) == ["messages", "model", "stream", "think"], "A: body keys (no tools): %r" % sorted(body))
check(body["model"] == "test-model:1" and body["stream"] is False and body["think"] is False, "A: model/stream/think")
check([m["role"] for m in body["messages"]] == ["system", "user"] and body["messages"][1]["content"] == "what is two plus two",
      "A: messages: %r" % body["messages"])
ok("A one call, exact shape, no tools, answer printed")

# B — only a loopback literal
for good in ("127.0.0.1:11434", "[::1]:11434", "127.0.0.9:80"):
    configure(host=good)
    check(A.endpoint() and A.endpoint().endswith("/api/chat"), "B: %s must be accepted" % good)
for bad in ("10.0.0.5:11434", "localhost:11434", "127.0.0.1", "example.org:80", "0.0.0.0:11434", ":11434", "127.0.0.1:http"):
    configure(host=bad)
    check(A.endpoint() is None, "B: %s must be refused" % bad)
    r = A.ask("q")
    check(r[0] is False and S.hits == [], "B: %s: no request may be made: %r" % (bad, r))
ok("B endpoint(): loopback literals only; a name or a routable address makes no request")

# C — a redirect is refused
configure(); S.reply = (302, {"Location": "http://127.0.0.1:%d/api/chat" % S2.port}, b""); S2.answer("leaked")
r = A.ask("q")
check(r[0] is False and S2.hits == [], "C: redirect followed: %r %r" % (r, S2.hits))
ok("C a redirect is refused; the second server is never contacted")

# D — oversize
configure(); S.answer("x" * (A.MAX_RESPONSE + 10))
r = A.ask("q")
check(r == (False, "the model's answer was too large"), "D: %r" % (r,))
ok("D an oversize response is refused")

# E — garbage, missing field, empty answer, dead port; never raises
configure()
for reply in ((200, {}, b"not json"), (200, {}, b'{"nope": 1}'), (200, {}, b'{"message": {"content": "  "}}'),
              (200, {}, b'{"message": {"content": 7}}'), (500, {}, b"boom")):
    S.reply = reply
    r = A.ask("q")
    check(r[0] is False and isinstance(r[1], str), "E: %r -> %r" % (reply[2], r))
configure(host="127.0.0.1:1")
r = A.ask("q")
check(r == (False, "the local model did not answer"), "E: dead port: %r" % (r,))
ok("E bad replies and a dead port give (False, reason), never an exception")

# F — the trace: sizes and outcome only, to syslog
configure(); tools(); S.answer("ANSWER-TOKEN-zzz")
rc, out, err, lines = run_main(["QUESTION-TOKEN-qqq"])
check(len(lines) == 1, "F: exactly one syslog line: %r" % lines)
check(lines[0] == "agos-ask mode=cli in=18 ctx=0 out=16 ok=1", "F: line: %r" % lines[0])
check("TOKEN" not in lines[0], "F: text in the trace")
configure(host="127.0.0.1:1")
rc, out, err, lines = run_main(["q"])
check(rc == 1 and lines == ["agos-ask mode=cli in=1 ctx=0 out=0 ok=0"] and "did not answer" in err, "F: failure trace: %r %r" % (lines, err))
ok("F one syslog line per use with sizes and outcome, never the text")

# G — no files
configure(); tools(); S.answer("fine")
work, home = os.path.join(TMP, "cwd"), os.path.join(TMP, "home"); os.makedirs(work); os.makedirs(home)
old_cwd, old_home, real_open, opened = os.getcwd(), os.environ.get("HOME"), builtins.open, []


def spy(file, mode="r", *a, **k):
    opened.append((str(file), mode)); return real_open(file, mode, *a, **k)


os.chdir(work); os.environ["HOME"] = home; builtins.open = spy
try:
    rc, out, err, lines = run_main(["q"])
finally:
    builtins.open = real_open; os.chdir(old_cwd)
    if old_home is not None:
        os.environ["HOME"] = old_home
check(rc == 0 and not [o for o in opened if set(o[1]) & set("wax+")], "G: opened for writing: %r" % opened)
check(os.listdir(work) == [] and os.listdir(home) == [], "G: files left behind: %r %r" % (os.listdir(work), os.listdir(home)))
ok("G main() opens nothing for writing and leaves cwd and HOME empty")

# H — launcher mode
long_answer = "A" * 2000
sel = "S" * (A.MAX_CONTEXT + 500)
configure(launcher="fake-launcher --dmenu"); S.answer(long_answer)
tools(fake_launcher="why is the sky blue\n", wl_paste=sel, wl_copy="", notify_send="")
rc, out, err, lines = run_main([])
check(rc == 0 and out == "" and len(S.hits) == 1, "H: rc/out/hits: %r %r %r" % (rc, out, len(S.hits)))
user = json.loads(S.hits[0][1])["messages"][1]["content"]
check(user == "Context (selected text):\n%s\n\nQuestion: why is the sky blue" % ("S" * A.MAX_CONTEXT), "H: context/cap: %r" % user[:80])
c = calls()
check([x[0] for x in c] == ["fake-launcher", "wl-paste", "notify-send", "wl-copy", "notify-send"], "H: tool order: %r" % [x[0] for x in c])
check(c[0][1] == ["--dmenu"] and c[1][1] == ["--primary", "--no-newline"], "H: launcher/wl-paste argv: %r %r" % (c[0][1], c[1][1]))
check(c[3][2] == long_answer, "H: the clipboard must get the whole answer")
check(c[4][1][-2] == "ask: why is the sky blue" and c[4][1][-1] == "A" * A.NOTIFY_CHARS and c[4][1][-3] == "--",
      "H: notification title/body cap/`--`: %r" % (c[4][1][:5],))
check(lines == ["agos-ask mode=gui in=19 ctx=%d out=2000 ok=1" % A.MAX_CONTEXT], "H: trace: %r" % lines)
ok("H launcher mode: capped selection as context, whole answer to the clipboard, capped notification")

# I — terminal mode never reads the selection
configure(); S.answer("x")
tools(wl_paste="SECRET", wl_copy="", notify_send="")
rc, out, err, lines = run_main(["q"])
check(rc == 0 and calls() == [], "I: terminal mode ran a desktop tool: %r" % calls())
check(json.loads(S.hits[0][1])["messages"][1]["content"] == "q", "I: selection leaked into a terminal question")
ok("I terminal mode reads no selection and runs no desktop tool")

# J — a cancelled prompt
configure(launcher="fake-launcher"); S.answer("x")
tools(fake_launcher=("", 1), wl_paste="sel", notify_send="")
rc, out, err, lines = run_main([])
check(rc == 0 and S.hits == [] and lines == [] and [x[0] for x in calls()] == ["fake-launcher"], "J: %r %r %r" % (rc, lines, calls()))
tools(fake_launcher="   \n", wl_paste="sel", notify_send="")
rc, out, err, lines = run_main([])
check(rc == 0 and S.hits == [] and lines == [], "J: an empty prompt asked anyway")
tools(wl_paste="sel", notify_send="")          # no launcher on PATH at all
rc, out, err, lines = run_main([])
check(rc == 0 and S.hits == [] and lines == [], "J: no launcher on PATH asked anyway")
ok("J a cancelled, empty or missing prompt makes no request and leaves no trace")

# K — the question cap
configure(); tools(); S.answer("x")
rc, out, err, lines = run_main(["q" * 9000])
check(len(json.loads(S.hits[0][1])["messages"][1]["content"]) == A.MAX_QUESTION and lines[0].startswith("agos-ask mode=cli in=4000 "),
      "K: cap: %r" % lines)
ok("K the question is capped at 4000 characters")

# L — the repo copy is unconfigured; each placeholder appears once
for k in ("AGOS_ASK_HOST", "AGOS_ASK_MODEL", "AGOS_ASK_LAUNCHER"):
    os.environ.pop(k, None)
S.hits.clear()
check(A.endpoint() is None and A.ask("q") == (False, "quick-ask is not configured on this box") and S.hits == [], "L: unconfigured copy asked")
src = open(MOD).read()
for ph in ('MODEL_DEFAULT = "@AGOS_ASK_MODEL@"', 'HOST_DEFAULT = "@AGOS_ASK_HOST@"', 'LAUNCHER_DEFAULT = "@AGOS_ASK_LAUNCHER@"'):
    check(src.count(ph) == 1, "L: substitution target not present exactly once: %s" % ph)
os.environ["AGOS_ASK_HOST"] = "127.0.0.1:%d" % S.port       # host but no model
check(A.ask("q")[0] is False and S.hits == [], "L: no model configured but a request was made")
ok("L the unsubstituted copy is 'not configured' and makes no request")

# M — a proxy in the environment is ignored
configure(); tools(); S.answer("direct")
os.environ["http_proxy"] = os.environ["HTTP_PROXY"] = "http://127.0.0.1:1"
try:
    r = A.ask("q")
finally:
    os.environ.pop("http_proxy"); os.environ.pop("HTTP_PROXY")
check(r == (True, "direct"), "M: the proxy was used: %r" % (r,))
ok("M a proxy in the environment is ignored")

os.environ["PATH"] = REAL_PATH
print("agos-ask-battery: PASS (%d criteria)" % len(passed))
