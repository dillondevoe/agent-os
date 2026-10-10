#!/usr/bin/env python3
# tests/agent-loop-wall-socket-battery.py — bin/agent-loop's PRODUCTION wall seam: the unix socket
# to the wall service (docs/design/broker-service.md §2.5, build-order PR 1).
#
# The loop is the untrusted side; this proves it can never turn a broken wall into an allow and
# never routes around the socket once it is pinned. A fake wall server answers on a temp socket.
#
#   A. Happy path: the server receives EXACTLY one well-formed JSON-RPC tools/call line followed by
#      EOF (the client half-closes), answers one data_result line, and dispatch returns (True, …).
#   B. A broker deny line -> dispatch returns (False, {"error": …}).
#   C. Server closes without a verdict (refused peer / crashed instance) -> deny.
#   D. Garbage reply -> deny. Non-object JSON -> deny.
#   E. Socket path absent -> deny, and the subprocess pipeline is NOT run (AGENT_OS_BROKER points
#      at a script that drops a marker; the marker must not appear). CONTROL: with the socket unset
#      the same marker script IS run (the fallback exists only when unpinned).
#   F. A server that accepts and never answers -> deny within the (shortened) overall deadline,
#      including one that trickles bytes with no newline (the deadline is overall, not per-recv).
#   G. A reply larger than WALL_REPLY_MAX -> deny (a well-formed allow; control: under the cap, allowed).
#   H. A copy with IMAGE_WALL_SOCKET substituted uses it and ignores AGENT_OS_WALL_SOCKET (pointed at
#      an absent path); the deadline is 190 s (RuntimeMaxSec 180 + 10, spec §2.5).
#
# stdlib only, no model, no real wall.

SIDE_EFFECTS = []  # temp dir + unix sockets under it, removed at exit

import atexit, json, os, shutil, socket, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
TMP = tempfile.mkdtemp(prefix="wall-socket-battery.")
atexit.register(shutil.rmtree, TMP, True)
SOCK = os.path.join(TMP, "wall.sock")
MARK = os.path.join(TMP, "spawned.mark")
FAKE_BROKER = os.path.join(TMP, "fake-broker")
with open(FAKE_BROKER, "w") as fh:
    fh.write("import sys\nopen(%r, 'w').write('x')\nsys.stdin.read()\n" % MARK)
os.environ["AGENT_OS_BROKER"] = FAKE_BROKER
os.environ["AGENT_OS_MCP"] = os.path.join(REPO, "bin", "mcp")
sys.path.insert(0, os.path.join(REPO, "modules"))


def check(c, msg):
    if not c:
        raise AssertionError(msg)


def load_loop(socket_path, compiled=None):
    if socket_path is None:
        os.environ.pop("AGENT_OS_WALL_SOCKET", None)
    else:
        os.environ["AGENT_OS_WALL_SOCKET"] = socket_path
    path = os.path.join(REPO, "bin", "agent-loop")
    mod = type(sys)("agent_loop_ws")
    mod.__file__ = path
    src = open(path, encoding="utf-8").read()
    if compiled is not None:
        check(src.count('IMAGE_WALL_SOCKET = ""') == 1, "agent-loop must carry one substitutable IMAGE_WALL_SOCKET line")
        src = src.replace('IMAGE_WALL_SOCKET = ""', 'IMAGE_WALL_SOCKET = %r' % compiled)
    exec(compile(src, path, "exec"), mod.__dict__)
    return mod


class Server:
    """One-shot fake wall: accepts one connection, records what it read until EOF, then runs
    `behave(conn, data)`."""
    def __init__(self, behave, read_to_eof=True):
        try:
            os.unlink(SOCK)
        except FileNotFoundError:
            pass
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.bind(SOCK); self.s.listen(1)
        self.got, self.behave, self.read_to_eof = None, behave, read_to_eof
        self.t = threading.Thread(target=self.run, daemon=True); self.t.start()

    def run(self):
        conn, _ = self.s.accept()
        data = b""
        if self.read_to_eof:
            while True:
                c = conn.recv(65536)
                if not c:
                    break
                data += c
        self.got = data
        try:
            self.behave(conn, data)
        finally:
            try: conn.close()
            except OSError: pass
            self.s.close()


def reply(line):
    return lambda conn, data: conn.sendall(line)


passed = []
def ok(tag):
    passed.append(tag); print("  ok  " + tag)


DATA = {"ok": True, "id": 1, "result": {"capability_ok": True, "content": "hello", "content_type": "data"}}

# A
L = load_loop(SOCK)
srv = Server(reply((json.dumps(DATA) + "\n").encode()))
r = L.dispatch("file.read", {"path": "/x"})
srv.t.join(5)
check(r[0] is True and r[1].get("content") == "hello", "A: data_result must dispatch ok: %r" % (r,))
lines = [l for l in srv.got.split(b"\n") if l.strip()]
check(len(lines) == 1, "A: exactly one request line, got %r" % srv.got)
req = json.loads(lines[0])
check(req["jsonrpc"] == "2.0" and req["method"] == "tools/call" and req["params"] == {"name": "file.read", "arguments": {"path": "/x"}},
      "A: well-formed tools/call: %r" % req)
check(not os.path.exists(MARK), "A: no subprocess wall may run when the socket is pinned")
ok("A one request line + EOF; data_result -> (True, result)")

# B
srv = Server(reply(b'{"ok": false, "id": 2, "error": {"code": -32000, "message": "denied"}}\n'))
r = L.dispatch("file.read", {"path": "/x"}); srv.t.join(5)
check(r[0] is False and "error" in r[1], "B: deny must be (False, error): %r" % (r,))
ok("B broker deny -> (False, error)")

# C, D
for name, beh in (("C closed without verdict", lambda c, d: None), ("D garbage", reply(b"not json\n")),
                  ("D non-object", reply(b"[1, 2]\n"))):
    srv = Server(beh)
    r = L.dispatch("file.read", {"path": "/x"}); srv.t.join(5)
    check(r[0] is False, "%s must deny: %r" % (name, r))
ok("C/D closed, garbage, non-object -> deny")

# E
os.unlink(SOCK) if os.path.exists(SOCK) else None
L2 = load_loop(os.path.join(TMP, "absent.sock"))
r = L2.dispatch("file.read", {"path": "/x"})
check(r[0] is False and not os.path.exists(MARK), "E: absent socket must deny without spawning a wall: %r" % (r,))
L3 = load_loop(None)
L3.dispatch("file.read", {"path": "/x"})
check(os.path.exists(MARK), "E control: with the socket unset the subprocess wall does run")
os.unlink(MARK)
ok("E absent socket -> deny, no fallback; control: unpinned uses the subprocess wall")

# F
L = load_loop(SOCK); L.WALL_SOCKET_TIMEOUT_S = 2
srv = Server(lambda c, d: time.sleep(4))
t0 = time.monotonic(); r = L.dispatch("file.read", {"path": "/x"}); dt = time.monotonic() - t0
check(r[0] is False and dt < 3.5, "F: silent server must deny within the deadline (%.1fs): %r" % (dt, r))
def trickle(conn, data):
    for _ in range(8):
        try: conn.sendall(b"x")
        except OSError: return
        time.sleep(0.5)
srv = Server(trickle)
t0 = time.monotonic(); r = L.dispatch("file.read", {"path": "/x"}); dt = time.monotonic() - t0
check(r[0] is False and dt < 3.5, "F: a trickling server must still hit the overall deadline (%.1fs)" % dt)
time.sleep(2.5)
ok("F silent and trickling servers -> deny within the overall deadline")

# G
L = load_loop(SOCK); L.WALL_REPLY_MAX = 1024
big = dict(DATA, result=dict(DATA["result"], content="x" * 4096))   # a VALID allow, only too big
srv = Server(reply((json.dumps(big) + "\n").encode()))
r = L.dispatch("file.read", {"path": "/x"}); srv.t.join(5)
check(r[0] is False, "G: oversize reply must deny even when it is a well-formed allow: %r" % (r[0],))
L.WALL_REPLY_MAX = 1 << 20
srv = Server(reply((json.dumps(big) + "\n").encode()))
r = L.dispatch("file.read", {"path": "/x"}); srv.t.join(5)
check(r[0] is True, "G control: the same reply under the cap is allowed")
ok("G oversize reply -> deny")

# H
L = load_loop(os.path.join(TMP, "absent.sock"), compiled=SOCK)
check(L.WALL_SOCKET == SOCK and L.WALL_SOCKET_TIMEOUT_S == 190, "H: compiled path wins; deadline 190")
srv = Server(reply((json.dumps(DATA) + "\n").encode()))
r = L.dispatch("file.read", {"path": "/x"}); srv.t.join(5)
check(r[0] is True, "H: the compiled socket is the one used, whatever the env says: %r" % (r,))
ok("H compiled IMAGE_WALL_SOCKET beats the environment; 190 s deadline")

print("agent-loop-wall-socket-battery: PASS (%d criteria)" % len(passed))
