# tests/app-approve-confirm.nix — app approval through the REAL confirm channel, end to end on a
# booted image (docs/design/app-approval-confirm.md §5, build-order PR 3; invariants A2/A3).
#
# Every other app-approval check stops short of the human: the impl battery fakes the store root,
# the wall checks never reach a T2 decision. This test drives the production path the spec
# describes: the agent asks with `agos-approve call` -> the REAL broker (started by systemd, clean
# environment, as root: the privileged broker the spec's §6 says production still needs) -> the
# REAL confirm wrapper -> telegram is unconfigured, so the getty channel on /dev/tty2 -> the
# operator reads the code off the console (/dev/vcs2) and types the answer into tty2 (TIOCSTI, as
# root) -> the app.approve impl in its derived sandbox -> the root-owned store -> the agent's
# image copy of agos-run.
#
# No test hook is used anywhere on that path: AGENT_OS_CONFIRM_GETTY_IN/OUT are unset by the
# wrapper and dropped by the broker (#317), so the only way to answer is the console itself.
#
# Legs, each asserting the store afterwards:
#   0. Layout (A2): store dir root:root 0755, apps dir agent-owned; the agent cannot write the store.
#   1. Before approval (A3): the image agos-run refuses (4), --approve-for-test does not exist (2),
#      and an approval planted in the agent's own ~/.local/state store does not count.
#   2. APPROVE on tty2 -> the impl writes one 0644 root entry; the image agos-run now accepts.
#   3. DENY on tty2 -> nothing written; still refused.
#   4. The agent edits the manifest WHILE the prompt is up, then the human approves what they saw
#      -> the impl refuses (sha-mismatch); nothing written; refused.
#   5. After all of it the agent still cannot write the store (A2).
{ pkgs, baseModules }:

pkgs.testers.runNixOSTest {
  name = "agentos-app-approve-confirm";

  nodes.box = { ... }: {
    imports = baseModules;
    # Harness only (verdict files, the TIOCSTI injector, runuser). Everything under test is the
    # store-pinned production artefact baseModules installs.
    environment.systemPackages = with pkgs; [ bash coreutils python3 systemd util-linux ];
    # Root keeps CAP_SYS_ADMIN, which TIOCSTI needs on a tty that is not its controlling one; the
    # legacy knob is set as well so the injector does not depend on the kernel's default.
    boot.kernel.sysctl."dev.tty.legacy_tiocsti" = 1;
  };

  testScript = ''
    import base64
    import json
    import re

    APPS = "/var/lib/agent-os/apps"
    STORE_DIR = "/var/lib/agent-os/app-approvals"
    STORE = STORE_DIR + "/approvals.json"
    AS_AGENT = "runuser -u agent -- env HOME=/home/agent "

    box.wait_for_unit("multi-user.target")
    box.succeed(
        "state=$(systemctl is-system-running --wait || true); "
        "case \"$state\" in running|degraded) exit 0 ;; *) exit 1 ;; esac"
    )

    def put(path, data, owner=None):
        b64 = base64.b64encode(data.encode()).decode()
        box.succeed(f"printf %s {b64} | base64 -d > {path}")
        if owner:
            box.succeed(f"chown {owner} {path}")

    def manifest(name, version="0.1"):
        return {"name": name, "version": version, "entry": ["python3", "app.py"], "files": [],
                "network": [], "devices": [], "limits": {"cpu_pct": 25, "mem_mb": 128, "wall_s": 60},
                "schedule": ""}

    def make_app(name):
        d = f"{APPS}/{name}"
        box.succeed(AS_AGENT + f"mkdir -p {d}")
        put(f"{d}/manifest.json", json.dumps(manifest(name)), "agent:")
        put(f"{d}/app.py", "print('hello')\n", "agent:")
        return d

    def run_rc(d, *flags):
        return box.execute(AS_AGENT + f"agos-run {d} --dry-run " + " ".join(flags))[0]

    def store():
        rc, out = box.execute(f"cat {STORE}")
        return json.loads(out) if rc == 0 and out.strip() else {}

    # ── 0. layout (A2)
    box.succeed(f"test \"$(stat -c '%U %G %a' {STORE_DIR})\" = 'root root 755'")
    box.succeed(f"test \"$(stat -c '%U' {APPS})\" = 'agent'")
    box.fail(AS_AGENT + f"sh -c 'echo {{}} > {STORE}'")
    print("leg 0 OK  (store dir root 0755, apps dir agent-owned, agent cannot write the store)")

    # ── 1. before approval (A3)
    demo = make_app("demo")
    call = json.loads(box.succeed(AS_AGENT + f"agos-approve call {demo}"))
    sha = call["arguments"]["sha256"]
    assert call["capability"] == "app.approve" and call["arguments"]["app"] == demo, call
    assert run_rc(demo) == 4, "image agos-run must refuse an unapproved app"
    assert run_rc(demo, "--approve-for-test") == 2, "--approve-for-test must not exist in the image"
    box.succeed(AS_AGENT + "mkdir -p /home/agent/.local/state/agent-os")
    put("/home/agent/.local/state/agent-os/approvals.json",
        json.dumps({sha: {"name": "demo", "approved_at": "x"}}), "agent:")
    assert run_rc(demo) == 4, "an approval in the agent's own $HOME store must not count on the image"
    print("leg 1 OK  (refused; no --approve-for-test; the agent's own store is ignored)")

    # ── the confirm round trip
    broker = box.succeed("command -v broker").strip()
    box.succeed("mkdir -p /run/aat")
    seq = [0]

    def ask(d):
        """Start the real broker (systemd service: clean env) on one app.approve call; return
        (unit, out path, code shown on tty2)."""
        seq[0] += 1
        n = seq[0]
        c = json.loads(box.succeed(AS_AGENT + f"agos-approve call {d}"))
        verdict = {"ok": True, "method": "tools/call", "id": n, "name": c["capability"], "arguments": c["arguments"]}
        put(f"/run/aat/v{n}.json", json.dumps(verdict))
        box.succeed("printf '\\033c' > /dev/tty2")   # clear the console so the code read is this one
        unit = f"aat-broker-{n}"
        box.succeed(
            f"systemd-run --unit={unit} --property=RemainAfterExit=yes "
            f"/bin/sh -c 'exec {broker} run < /run/aat/v{n}.json > /run/aat/o{n}.json 2>/dev/null'"
        )
        box.wait_until_succeeds("grep -aoE 'approve [A-Z2-7]{8}' /dev/vcs2", timeout=60)
        screen = box.succeed("cat /dev/vcs2")
        assert "CAPABILITY: app.approve" in screen and "TIER: T2" in screen, screen
        code = re.findall(r"type: +approve ([A-Z2-7]{8})", screen)[-1]
        return unit, f"/run/aat/o{n}.json", code

    def answer(text):
        b64 = base64.b64encode((text + "\n").encode()).decode()
        box.succeed(
            "python3 -c 'import base64,fcntl,os,termios,sys;"
            "fd=os.open(\"/dev/tty2\",os.O_RDWR);"
            "[fcntl.ioctl(fd,termios.TIOCSTI,bytes([b])) for b in base64.b64decode(sys.argv[1])]' " + b64
        )

    def result(unit, out):
        box.wait_until_succeeds(f"test -s {out}", timeout=150)
        box.wait_until_succeeds(f"! systemctl is-active --quiet {unit} || systemctl show -p SubState {unit} | grep -q exited", timeout=30)
        return json.loads(box.succeed(f"cat {out}").strip().splitlines()[-1])

    # ── 2. approve
    unit, out, code = ask(demo)
    answer(f"approve {code}")
    r = result(unit, out)
    print("approve result: " + json.dumps(r))
    assert r.get("ok") is True and "approved demo " + sha[:12] in json.dumps(r), r
    s = store()
    assert s.get(sha, {}).get("name") == "demo" and s[sha].get("via") == "confirm", s
    box.succeed(f"test \"$(stat -c '%U %G %a' {STORE})\" = 'root root 644'")
    assert run_rc(demo) == 0, "the image agos-run must accept the approved app"
    print("leg 2 OK  (approved on tty2 -> one 0644 root entry -> agos-run accepts)")

    # ── 3. deny
    demo2 = make_app("demo2")
    sha2 = json.loads(box.succeed(AS_AGENT + f"agos-approve call {demo2}"))["arguments"]["sha256"]
    unit, out, code = ask(demo2)
    answer("deny")
    r = result(unit, out)
    print("deny result: " + json.dumps(r))
    assert sha2 not in store() and run_rc(demo2) == 4, "a denied app must not be approved"
    assert "approved demo2" not in json.dumps(r), r
    print("leg 3 OK  (denied on tty2 -> nothing written -> refused)")

    # ── 4. edited while the prompt is up
    demo3 = make_app("demo3")
    sha3 = json.loads(box.succeed(AS_AGENT + f"agos-approve call {demo3}"))["arguments"]["sha256"]
    unit, out, code = ask(demo3)
    put(f"{APPS}/demo3/manifest.json", json.dumps(manifest("demo3", version="0.2")), "agent:")
    answer(f"approve {code}")
    r = result(unit, out)
    print("edited-during-prompt result: " + json.dumps(r))
    assert "sha-mismatch" in json.dumps(r), r
    s = store()
    assert sha3 not in s and not any(v.get("name") == "demo3" for v in s.values()), s
    assert run_rc(demo3) == 4, "an app edited during the prompt must stay unapproved"
    print("leg 4 OK  (manifest edited during the prompt -> impl refused -> nothing written)")

    # ── 5. A2 again
    box.fail(AS_AGENT + f"sh -c 'echo {{}} > {STORE}'")
    box.fail(AS_AGENT + f"rm -f {STORE}")
    print("leg 5 OK  (the agent still cannot write or remove the store)")
  '';
}
