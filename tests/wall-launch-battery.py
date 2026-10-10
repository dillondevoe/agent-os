#!/usr/bin/env python3
# tests/wall-launch-battery.py — the CONTRACT BATTERY for bin/agent-os-wall-launch, the front door
# of every wall instance (docs/design/broker-service.md §2.2).
#
# The launcher is driven over a socketpair exactly as systemd hands it an Accept=yes connection:
# the same socket on fd 0 and fd 1, so SO_PEERCRED reports this process. Fake `mcp` / `broker`
# scripts record what they were given. Every refusal arm also asserts the fakes never ran.
#
#   A. Peer is the configured agent -> mcp gets exactly the one request line, broker's output comes
#      back on the socket, AGENT_OS_PEER_UID/PID are the peer's.
#   B. Peer is a different uid (agent user configured as another account) -> refused, no verdict,
#      nothing spawned.
#   C. Agent account lookup fails, or resolves to uid 0 -> refused, nothing spawned.
#   D. Two lines on one connection -> mcp receives only the first (one request per instance, B8).
#   E. No newline before EOF -> refused, nothing spawned.
#   F. More than 1 MiB before the first newline -> refused, nothing spawned; exactly 1 MiB + "\n" passes.
#   G. Launcher not configured (a constant and its env both empty) -> refused.
#   H. fd 0 not a socket (a pipe: no peer credentials) -> refused.
#   I. The installed copy's three constants exist exactly once each (substitution target for
#      modules/wall-service.nix --replace-fail).
#
# stdlib only, no root, no systemd.

SIDE_EFFECTS = []  # temp dir, removed at exit

import atexit, json, os, pwd, shutil, socket, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LAUNCH = os.path.abspath(os.path.join(HERE, "..", "bin", "agent-os-wall-launch"))
TMP = tempfile.mkdtemp(prefix="wall-launch-battery.")
atexit.register(shutil.rmtree, TMP, True)
ME = pwd.getpwuid(os.getuid()).pw_name
MCP_LOG, BRK_LOG = os.path.join(TMP, "mcp.log"), os.path.join(TMP, "broker.log")

FAKE_MCP = os.path.join(TMP, "mcp")
FAKE_BRK = os.path.join(TMP, "broker")
with open(FAKE_MCP, "w") as fh:
    fh.write("#!%s\nimport sys\nd = sys.stdin.buffer.read()\nopen(%r, 'wb').write(d)\n"
             "sys.stdout.buffer.write(b'PARSED:' + d)\n" % (sys.executable, MCP_LOG))
with open(FAKE_BRK, "w") as fh:
    fh.write("#!%s\nimport json, os, sys\nd = sys.stdin.buffer.read()\nopen(%r, 'wb').write(d)\n"
             "print(json.dumps({'got': d.decode(), 'uid': os.environ.get('AGENT_OS_PEER_UID'), "
             "'pid': os.environ.get('AGENT_OS_PEER_PID')}))\n" % (sys.executable, BRK_LOG))
os.chmod(FAKE_MCP, 0o755); os.chmod(FAKE_BRK, 0o755)


def check(c, msg):
    if not c:
        raise AssertionError(msg)


def run(payload, user=ME, mcp=FAKE_MCP, broker=FAKE_BRK, use_pipe=False):
    for p in (MCP_LOG, BRK_LOG):
        if os.path.exists(p):
            os.unlink(p)
    env = {"PATH": os.environ.get("PATH", ""), "AGENT_OS_WALL_AGENT_USER": user,
           "AGENT_OS_WALL_MCP": mcp, "AGENT_OS_WALL_BROKER": broker}
    if use_pipe:
        r, w = os.pipe()
        os.write(w, payload); os.close(w)
        p = subprocess.run([sys.executable, LAUNCH], stdin=r, capture_output=True, env=env, timeout=30)
        os.close(r)
        return p.returncode, p.stdout, p.stderr.decode()
    client, server = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    proc = subprocess.Popen([sys.executable, LAUNCH], stdin=server.fileno(), stdout=server.fileno(),
                            stderr=subprocess.PIPE, env=env)
    server.close()
    try:
        client.sendall(payload)
        client.shutdown(socket.SHUT_WR)
    except (BrokenPipeError, ConnectionResetError):
        pass                              # the launcher refused before reading everything
    out = b""
    client.settimeout(30)
    while True:
        try:
            c = client.recv(65536)
        except ConnectionResetError:     # a refusal exits with the request unread: RST = no verdict
            break
        if not c:
            break
        out += c
    client.close()
    err = proc.stderr.read().decode()
    proc.wait(30)
    return proc.returncode, out, err


def spawned():
    return os.path.exists(MCP_LOG) or os.path.exists(BRK_LOG)


passed = []
def ok(tag):
    passed.append(tag); print("  ok  " + tag)


REQ = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"file.read","arguments":{}}}\n'

# A
rc, out, err = run(REQ)
check(rc == 0, "A: rc=%d err=%s" % (rc, err))
check(open(MCP_LOG, "rb").read() == REQ, "A: mcp must get exactly the one request line")
reply = json.loads(out.decode().strip())
check(reply["got"] == "PARSED:" + REQ.decode(), "A: broker output must come back on the socket: %r" % reply)
check(reply["uid"] == str(os.getuid()) and reply["pid"] == str(os.getpid()), "A: peer uid/pid exported: %r" % reply)
ok("A agent peer -> one line to mcp, broker reply on the socket, peer uid/pid exported")

# B
other = next((u.pw_name for u in pwd.getpwall() if u.pw_uid not in (0, os.getuid())), None)
check(other, "B setup: need another account")
rc, out, err = run(REQ, user=other)
check(rc != 0 and out == b"" and "is not the agent" in err and not spawned(), "B: wrong peer must be refused: rc=%d %r %s" % (rc, out, err))
ok("B a peer that is not the agent -> no verdict, nothing spawned")

# C
for user, why in (("no-such-account-zz", "lookup failed"), ("root", "uid 0")):
    rc, out, err = run(REQ, user=user)
    check(rc != 0 and out == b"" and why in err and not spawned(), "C %s: rc=%d %s" % (user, rc, err))
ok("C failed lookup / uid 0 -> refused, nothing spawned")

# D
rc, out, err = run(REQ + b'{"second":"request"}\n')
check(rc == 0 and open(MCP_LOG, "rb").read() == REQ, "D: only the first line may reach mcp: %r" % open(MCP_LOG, "rb").read())
ok("D two lines -> mcp receives only the first")

# E
rc, out, err = run(REQ.rstrip(b"\n"))
check(rc != 0 and out == b"" and "no newline" in err and not spawned(), "E: rc=%d %s" % (rc, err))
ok("E no newline before EOF -> refused, nothing spawned")

# F
big = b"x" * (1024 * 1024 + 10) + b"\n"
rc, out, err = run(big)
check(rc != 0 and out == b"" and "over 1 MiB" in err and not spawned(), "F: rc=%d %s" % (rc, err))
edge = b"y" * (1024 * 1024) + b"\n"
rc, out, err = run(edge)
check(rc == 0 and open(MCP_LOG, "rb").read() == edge, "F control: exactly 1 MiB + newline passes (rc=%d %s)" % (rc, err))
ok("F over 1 MiB refused; exactly 1 MiB passes")

# G
rc, out, err = run(REQ, mcp="")
check(rc != 0 and "not configured" in err and not spawned(), "G: rc=%d %s" % (rc, err))
ok("G unconfigured launcher -> refused")

# H
rc, out, err = run(REQ, use_pipe=True)
check(rc != 0 and "no peer credentials" in err and not spawned(), "H: rc=%d %s" % (rc, err))
ok("H fd 0 not a socket -> refused")

# I
src = open(LAUNCH).read()
for c in ('WALL_AGENT_USER = ""', 'WALL_MCP = ""', 'WALL_BROKER = ""'):
    check(src.count(c) == 1, "I: %s must appear exactly once (substitution target)" % c)
ok("I substitution targets present exactly once")

print("wall-launch-battery: PASS (%d criteria)" % len(passed))
