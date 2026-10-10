# tests/wall-service.nix — the wall as a socket-activated system service, on a booted image
# (docs/design/broker-service.md §4, build-order PR 2).
#
# Drives the PRODUCTION path: the agent account's installed agent-loop (socket path compiled in)
# -> /run/agent-os/wall.sock -> agent-os-wall@.service (root, clean environment) -> the launcher's
# peer check and one-line read -> mcp parse | broker run -> audit / taint / confirm / capability.
#
#   B1  the agent cannot append audit, write taint, or start a system unit itself.
#   B2  a live wall instance's environment is systemd's: a variable planted in the agent's
#       environment is absent; AGENT_OS_PEER_UID is present (positive control: right process).
#   B3  other accounts cannot connect (EACCES); root connecting gets no verdict and no audit record.
#   B4  with a confirm pending, a second agent connection gets EOF with no verdict and no second
#       instance starts; the pending one completes on the typed answer.
#   B5  a T0 file.read through the installed agent-loop's dispatch() returns the file's content.
#   B6  the audit `route` record carries the agent's uid as peer_uid.
#   B8  two lines on one connection -> exactly one verdict and one route record; no newline -> none.
#   +   socket and directory modes; the derived RuntimeMaxSec on the live unit.
{ pkgs, baseModules }:

pkgs.testers.runNixOSTest {
  name = "agentos-wall-service";

  nodes.box = { ... }: {
    imports = baseModules;
    environment.systemPackages = with pkgs; [ bash coreutils python3 systemd util-linux kbd procps ];
    boot.kernel.sysctl."dev.tty.legacy_tiocsti" = 1;
  };

  testScript = ''
    import base64
    import json

    SOCK = "/run/agent-os/wall.sock"
    AUDIT = "/var/lib/agent-os/audit/audit.log"
    AS_AGENT = "runuser -u agent -- env HOME=/home/agent AGENT_OS_PLANTED=zz-planted "

    box.wait_for_unit("multi-user.target")
    box.wait_for_unit("agent-os-wall.socket")
    agent_uid = int(box.succeed("id -u agent").strip())

    def put(path, data):
        b64 = base64.b64encode(data.encode()).decode()
        box.succeed(f"printf %s {b64} | base64 -d > {path}")

    # A tiny socket client, run as any user: sends argv[1] (base64) and prints the reply (base64).
    put("/run/wallc.py",
        "import base64,socket,sys\n"
        "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.settimeout(float(sys.argv[2]) if len(sys.argv)>2 else 200)\n"
        "s.connect('" + SOCK + "');s.sendall(base64.b64decode(sys.argv[1]));s.shutdown(socket.SHUT_WR)\n"
        "b=bytes()\n"
        "try:\n"
        "  while True:\n"
        "    c=s.recv(65536)\n"
        "    if not c: break\n"
        "    b+=c\n"
        "except (ConnectionResetError, TimeoutError, socket.timeout): pass\n"
        "print(base64.b64encode(b).decode())\n")
    box.succeed("chmod 0755 /run/wallc.py")

    def call(user, payload, timeout=200):
        b64 = base64.b64encode(payload).decode()
        prefix = AS_AGENT if user == "agent" else (f"runuser -u {user} -- " if user != "root" else "")
        rc, out = box.execute(prefix + f"python3 /run/wallc.py {b64} {timeout}")
        return rc, (base64.b64decode(out.strip()) if rc == 0 and out.strip() else b"")

    def routes():
        rc, out = box.execute(f"cat {AUDIT}")
        recs = [json.loads(l) for l in out.splitlines() if l.strip()] if rc == 0 else []
        return [r for r in recs if r.get("event") == "route" or r.get("payload", {}).get("event") == "route"]

    def req(name, args, rid=1):
        return (json.dumps({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                            "params": {"name": name, "arguments": args}}) + "\n").encode()

    # ── modes and the live unit
    box.succeed("test \"$(stat -c '%U %G %a' /run/agent-os)\" = 'root root 755'")
    box.succeed(f"test \"$(stat -c '%U %G %a' {SOCK})\" = 'root agent-os-wall 660'")
    box.succeed("systemctl cat agent-os-wall@.service | grep -qx 'RuntimeMaxSec=180'")
    print("modes OK  (/run/agent-os root 0755, socket root:agent-os-wall 0660, RuntimeMaxSec 3min)")

    # ── B1
    box.fail(AS_AGENT + f"sh -c 'echo x >> {AUDIT}'")
    box.fail(AS_AGENT + "sh -c 'echo x > /var/lib/agent-os/taint/session'")
    box.fail(AS_AGENT + "systemd-run --unit=agent-os-cap-evil /bin/true")
    print("B1 OK  (agent cannot append audit, write taint, or start a system unit)")

    # ── B3
    rc, out = box.execute("runuser -u nobody -- python3 /run/wallc.py " + base64.b64encode(req("file.read", {"path": "/x"})).decode())
    assert rc != 0, "nobody must not be able to connect: " + out
    n0 = len(routes())
    rc, out = call("root", req("capabilities.list", {}))
    assert out == b"", "root (not the agent) must get no verdict: %r" % out
    assert len(routes()) == n0, "a refused connection must write no audit record"
    print("B3 OK  (nobody: EACCES; root: no verdict, no audit record)")

    # ── B5 + B6 through the installed agent-loop's dispatch()
    box.succeed("mkdir -p /var/lib/agent-os/safe-read && printf 'wall says hi' > /var/lib/agent-os/safe-read/hello.txt")
    loop = box.succeed("command -v agent-loop").strip()
    out = box.succeed(AS_AGENT + "python3 -c '"
        "import json,sys;src=open(sys.argv[1]).read();ns={\"__name__\":\"wall_test\",\"__file__\":sys.argv[1]};exec(compile(src,sys.argv[1],\"exec\"),ns);"
        "print(json.dumps(ns[\"dispatch\"](\"file.read\",{\"path\":\"/var/lib/agent-os/safe-read/hello.txt\"})))' " + loop)
    ok, res = json.loads(out.strip().splitlines()[-1])
    assert ok is True and "wall says hi" in json.dumps(res), "T0 file.read through agent-loop's socket path failed: " + out
    last = routes()[-1]
    rec = last.get("payload", last)
    assert rec.get("peer_uid") == agent_uid, "route record must carry the agent's uid: %r" % rec
    print("B5 OK  (installed agent-loop -> socket -> wall -> file.read content)")
    print("B6 OK  (route record peer_uid == agent uid)")

    # ── B8
    n0 = len(routes())
    rc, out = call("agent", req("capabilities.list", {}, 7) + req("capabilities.list", {}, 8))
    lines = [l for l in out.splitlines() if l.strip()]
    assert len(lines) == 1 and json.loads(lines[0])["id"] == 7, "two lines -> exactly one verdict (the first): %r" % out
    assert len(routes()) == n0 + 1, "two lines -> exactly one route record"
    rc, out = call("agent", req("capabilities.list", {}).rstrip(b"\n"))
    assert out == b"" and len(routes()) == n0 + 1, "no newline -> no verdict, no record: %r" % out
    print("B8 OK  (one request per instance; no newline -> refused)")

    # ── B2 + B4 with a T2 confirm pending
    box.succeed("mkdir -p /var/lib/agent-os/apps/wdemo && chown agent: /var/lib/agent-os/apps/wdemo")
    put("/var/lib/agent-os/apps/wdemo/manifest.json", json.dumps({
        "name": "wdemo", "version": "0.1", "entry": ["python3", "app.py"], "files": [], "network": [],
        "devices": [], "limits": {"cpu_pct": 25, "mem_mb": 128, "wall_s": 60}, "schedule": ""}))
    box.succeed("chown agent: /var/lib/agent-os/apps/wdemo/manifest.json")
    c = json.loads(box.succeed(AS_AGENT + "agos-approve call /var/lib/agent-os/apps/wdemo"))
    payload = req(c["capability"], c["arguments"], 21)
    box.succeed("chvt 1; printf '\\033c' > /dev/tty2")
    box.succeed("install -o agent -m 0644 /dev/null /run/pending.out")   # the agent cannot create files in /run
    runuser, py = (box.succeed(f"command -v {x}").strip() for x in ("runuser", "python3"))
    box.succeed(f"systemd-run --unit=wall-pending --property=RemainAfterExit=yes "
                f"{runuser} -u agent -- /usr/bin/env AGENT_OS_PLANTED=zz-planted /bin/sh -c "
                f"'{py} /run/wallc.py {base64.b64encode(payload).decode()} > /run/pending.out'")
    box.succeed("chvt 2")
    box.wait_until_succeeds("grep -aoE 'type: +approve [A-Z2-7]{8}' /dev/vcs2", timeout=60)
    insts = box.succeed("systemctl list-units --no-legend --state=active 'agent-os-wall@*' | wc -l").strip()
    assert insts == "1", "exactly one wall instance while the confirm is pending, got " + insts
    bpid = box.succeed("pgrep -f -- '-broker run' | head -1").strip()
    env = box.succeed(f"tr '\\0' '\\n' < /proc/{bpid}/environ")
    assert "AGENT_OS_PEER_UID=%d" % agent_uid in env, "positive control: the broker carries the peer uid: " + env
    assert "zz-planted" not in env and "PYTHONPATH" not in env, "the instance must not carry the caller's environment: " + env
    print("B2 OK  (instance environment is systemd's; peer uid present, planted variable absent)")
    rc, out = call("agent", req("capabilities.list", {}, 22), timeout=20)
    assert out == b"", "a concurrent connection must get no verdict: %r" % out
    insts = box.succeed("systemctl list-units --no-legend --state=active 'agent-os-wall@*' | wc -l").strip()
    assert insts == "1", "no second instance may start, got " + insts
    b64 = base64.b64encode(b"deny\n").decode()
    box.succeed("python3 -c 'import base64,fcntl,os,termios,sys;fd=os.open(\"/dev/tty2\",os.O_RDWR);"
                "[fcntl.ioctl(fd,termios.TIOCSTI,bytes([b])) for b in base64.b64decode(sys.argv[1])]' " + b64)
    box.wait_until_succeeds("test -s /run/pending.out", timeout=60)
    first = base64.b64decode(box.succeed("cat /run/pending.out").strip())
    assert b"getty-denied" in first, "the pending request must complete on the typed answer: %r" % first
    print("B4 OK  (concurrent connection refused, single instance; pending one completed on the answer)")
  '';
}
