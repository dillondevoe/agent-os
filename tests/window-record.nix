# tests/window-record.nix — THE RECORD of the setup window, on a booted box
# (docs/design/first-login-and-setup-window.md §2.7, slice 3c; the spec's setup-window-record).
#
# The product path with the window on and the wall SIGNING its audit records as `agent`, pinned
# (`agentos.wall.auditSigner`): the window program must sign as the same participant, through the
# same wrapper, or `audit verify` fails for every record after its first. The model server is the
# stand-in of tests/seal-wait.nix (it answers GET /api/tags from a file).
#
#   R1  first boot: the FIRST record of the chain (seq 0) is `window-opened`, signed by the pinned
#       participant, with the fields the spec lists; the chain verifies under the pin. Only the two
#       appending units can write the audit directory; the wait cannot.
#   R2  an nftables reload: `window-rederived`. `elements` is 0 there: the reload emptied the kernel's
#       set and the record is written before the set is put back. It is a measurement.
#   R3  a reboot inside the window: `window-reopened`, boots 2.
#   R4  the model appears: the box seals (the wait does that, and appends nothing). Within a tick the
#       chain holds exactly one `window-sealed`, built from the marker (same time, same boot id). It
#       stays exactly one through further ticks and with the cache removed.
#   R5  a wall request made by the agent after the seal chains onto the window's records, and the
#       whole chain verifies under the pin: one signer, two writing programs.
#   R6  a chain that refuses: a torn tail is planted, the state removed, the box restarted. No record,
#       no window: sealed at once (`refused`), the set never held the uid, one floor of budget
#       charged. The seal's record is OWED; once the log is repaired a tick writes it,
#       with the time of the seal. Then the state is removed again and a reboot is a first boot.
#   `noident`: the identity boot unit fails. The two appending units Require it: no window (the set
#       stays empty), no record of any kind, and the wait seals `not open`.
{ pkgs, baseModules }:

let
  fakeOllama = pkgs.writeText "fake-ollama.py" ''
    import http.server
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/api/tags":
                self.send_response(404); self.end_headers(); return
            try:
                body = open("/run/fake-ollama/tags.json", "rb").read()
            except OSError:
                body = b'{"models": []}'
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a):
            pass
    http.server.ThreadingHTTPServer(("127.0.0.1", 11434), H).serve_forever()
  '';
  common = { lib, ... }: {
    imports = baseModules;
    agentos.cleanRoom.setupWindow.enable = true;
    agentos.cleanRoom.setupWindow.seconds = 600;
    # Not under test, and it would sit two minutes waiting for a registry this VM cannot reach.
    systemd.services.agent-os-pull-model.wantedBy = lib.mkForce [ ];
    systemd.timers.agent-os-pull-model.wantedBy = lib.mkForce [ ];
    systemd.services.ollama.serviceConfig.ExecStart = lib.mkForce "${pkgs.python3}/bin/python3 ${fakeOllama}";
    virtualisation.memorySize = 2048;
    virtualisation.cores = 2;
    environment.systemPackages = [ pkgs.python3 pkgs.jq ];
  };
in
pkgs.testers.runNixOSTest {
  name = "agentos-window-record";

  nodes.box = { ... }: {
    imports = [ common ];
    agentos.wall.auditSigner = "agent";                  # the pin defaults to the signer
  };
  nodes.noident = { lib, ... }: {
    imports = [ common ];
    systemd.services.agent-os-identity-boot.serviceConfig.ExecStart = lib.mkForce "${pkgs.coreutils}/bin/false";
  };

  testScript = ''
    import json

    STATE = "/var/lib/agent-os/seal"
    LOG = "/var/lib/agent-os/audit/audit.log"
    UID = 351
    VERIFY = "AGENT_OS_AUDIT_REQUIRE_SIGNED=agent audit verify"

    def chain(m):
        out = m.execute(f"cat {LOG} 2>/dev/null")[1]
        return [json.loads(l) for l in out.splitlines() if l.strip().startswith("{") and l.strip().endswith("}")]

    def mine(m):
        return [r for r in chain(m) if r.get("src") == "setup-window"]

    def events(m):
        return [r["event"] for r in mine(m)]

    def elements(m):
        out = m.succeed("nft -j list set inet agentos_egress setup_uid")
        return sorted(json.loads(out)["nftables"][-1]["set"].get("elem", []))

    def marker(m):
        return json.loads(m.succeed(f"cat {STATE}/sealed"))

    def boot_open(m):
        m.start()
        m.wait_for_unit("agentos-setup-window.service")
        m.wait_until_succeeds("nft -j list set inet agentos_egress setup_uid | grep -q '\"elem\"'", timeout=30)

    # ── R1 ──────────────────────────────────────────────────────────────────────────────────────
    boot_open(box)
    recs = chain(box)
    assert len(recs) >= 1 and recs[0]["seq"] == 0 and recs[0].get("src") == "setup-window" and recs[0]["event"] == "window-opened", f"R1: the first record of the chain: {recs[:1]}"
    r = recs[0]
    assert r.get("signer") == "agent" and len(r.get("sig", "")) == 128, f"R1: the record is not signed by the wall's participant: {r}"
    boot1 = box.succeed("cat /proc/sys/kernel/random/boot_id").strip()
    top = box.succeed("readlink -f /run/current-system").strip()
    assert (r["schema"], r["boot_id"], r["fetch_uid"], r["toplevel"], r["boots"], r["budget_at_boot"], r["remaining"], r["elements"]) == \
        (1, boot1, UID, top, 1, 600, 540, 0) and len(r["ruleset_sha256"]) == 64, f"R1: fields: {r}"
    assert elements(box) == [UID], "R1: the window is not open"
    box.succeed(VERIFY)
    for unit, may in (("agentos-setup-window", True), ("agentos-setup-window-tick", True), ("agentos-seal", False)):
        rw = box.succeed(f"systemctl show -p ReadWritePaths --value {unit}.service")
        assert ("/var/lib/agent-os/audit" in rw) == may, f"R1: {unit} ReadWritePaths: {rw}"
        req = box.succeed(f"systemctl show -p Requires --value {unit}.service")
        assert ("agent-os-identity-boot.service" in req) == may, f"R1: {unit} Requires: {req}"

    # ── R2 ──────────────────────────────────────────────────────────────────────────────────────
    box.succeed("systemctl reload nftables.service")
    box.wait_until_succeeds("nft -j list set inet agentos_egress setup_uid | grep -q '\"elem\"'", timeout=30)
    assert events(box) == ["window-opened", "window-rederived"], f"R2: {events(box)}"
    assert mine(box)[-1]["elements"] == 0 and mine(box)[-1]["boot_id"] == boot1 and mine(box)[-1]["boots"] == 1, f"R2: {mine(box)[-1]}"

    # ── R3 ──────────────────────────────────────────────────────────────────────────────────────
    box.shutdown()
    boot_open(box)
    boot2 = box.succeed("cat /proc/sys/kernel/random/boot_id").strip()
    assert events(box) == ["window-opened", "window-rederived", "window-reopened"], f"R3: {events(box)}"
    r3 = mine(box)[-1]
    assert r3["boots"] == 2 and r3["boot_id"] == boot2 != boot1 and r3["budget_at_boot"] < 600 and r3["elements"] == 0, f"R3: {r3}"
    box.succeed(VERIFY)

    # ── R4 ──────────────────────────────────────────────────────────────────────────────────────
    whole = json.dumps({"models": [{"name": "qwen3.5:9b", "model": "qwen3.5:9b"}]})
    box.succeed(f"install -d -m 755 /run/fake-ollama && echo '{whole}' > /run/fake-ollama/tags.json")
    box.wait_until_succeeds(f"test -e {STATE}/sealed", timeout=40)
    m4 = marker(box)
    assert m4["reason"] == "complete" and m4["boot_id"] == boot2, f"R4: {m4}"
    box.wait_until_succeeds(f"grep -c window-sealed {LOG} | grep -qx 1", timeout=100)        # the tick, once a minute
    s4 = [r for r in mine(box) if r["event"] == "window-sealed"]
    assert len(s4) == 1 and (s4[0]["reason"], s4[0]["sealed_at"], s4[0]["boot_id"], s4[0]["toplevel"], s4[0]["ruleset_sha256"], s4[0]["elements"], s4[0]["fetch_uid"]) == \
        ("complete", m4["sealed_at"], boot2, m4["toplevel"], m4["ruleset_sha256"], 0, UID), f"R4: the seal's record is not the marker's: {s4} vs {m4}"
    assert s4[0].get("signer") == "agent", "R4: the seal's record is not signed"
    assert json.loads(box.succeed(f"cat {STATE}/recorded.json")) == {"boot_id": boot2, "sealed_at": m4["sealed_at"]}, "R4: the cache"
    box.succeed(f"rm {STATE}/recorded.json")
    box.wait_until_succeeds(f"test -e {STATE}/recorded.json", timeout=100)                    # the next tick scanned the chain
    box.succeed("systemctl start agentos-setup-window-tick.service")
    assert [r["event"] for r in mine(box)].count("window-sealed") == 1, f"R4: the seal was recorded more than once: {events(box)}"
    box.succeed(VERIFY)

    # ── R5 ──────────────────────────────────────────────────────────────────────────────────────
    box.wait_for_unit("getty@tty1.service")
    box.wait_until_succeeds("pgrep -u agent >/dev/null", timeout=60)
    box.wait_for_unit("agent-os-wall.socket")
    box.succeed("mkdir -p /var/lib/agent-os/safe-read && printf 'wall says hi' > /var/lib/agent-os/safe-read/hello.txt")
    loop = box.succeed("command -v agent-loop").strip()
    n5 = len(chain(box))
    out = box.succeed("runuser -u agent -- env HOME=/home/agent python3 -c '"
        "import json,sys;src=open(sys.argv[1]).read();ns={\"__name__\":\"wall_test\",\"__file__\":sys.argv[1]};exec(compile(src,sys.argv[1],\"exec\"),ns);"
        "print(json.dumps(ns[\"dispatch\"](\"file.read\",{\"path\":\"/var/lib/agent-os/safe-read/hello.txt\"})))' " + loop)
    ok, res = json.loads(out.strip().splitlines()[-1])
    assert ok is True and "wall says hi" in json.dumps(res), "R5: the wall request failed: " + out
    after = chain(box)
    assert len(after) > n5 and all(r.get("src") != "setup-window" and r.get("signer") == "agent" for r in after[n5:]), f"R5: the wall's records: {after[n5:]}"
    assert [r["seq"] for r in after] == list(range(len(after))), "R5: the sequence is not one chain"
    box.succeed(VERIFY)

    # ── R6 ──────────────────────────────────────────────────────────────────────────────────────
    n6 = len(mine(box))
    box.succeed(f"rm {STATE}/sealed {STATE}/window.json /run/fake-ollama/tags.json && printf '{{\"torn\":' >> {LOG}")
    box.fail("audit verify")
    box.shutdown()
    box.start()
    box.wait_until_succeeds(f"test -e {STATE}/sealed", timeout=120)
    m6 = marker(box)
    assert m6["reason"] == "refused", f"R6: {m6}"
    assert elements(box) == [], f"R6: the set holds {elements(box)} on a box whose chain refused the record"
    w6 = json.loads(box.succeed(f"cat {STATE}/window.json"))
    assert w6["boots"] == 1 and w6["budget_at_boot"] == 600 and w6["remaining"] >= 600 - 61, f"R6: more than one floor was charged: {w6}"
    box.succeed("journalctl -b -t agos-window --no-pager | grep -q 'no record, no window'")
    box.fail("journalctl -b -t agos-window --no-pager | grep -q 'window opened'")
    assert len(mine(box)) == n6, "R6: a record was written through a torn chain"
    box.succeed("systemctl start agentos-setup-window-tick.service")            # owed, and the tick does not fail for it
    box.succeed("journalctl -b -t agos-window --no-pager | grep -q 'still owed to the audit chain'")
    # the operator's repair: drop the torn tail. Then a tick pays the debt, with the time of the SEAL.
    box.succeed(f"sed -i '$ d' {LOG}")
    box.succeed("audit verify >/dev/null && systemctl start agentos-setup-window-tick.service")
    s6 = [r for r in mine(box) if r["event"] == "window-sealed"]
    assert len(s6) == 2 and (s6[-1]["reason"], s6[-1]["sealed_at"]) == ("refused", m6["sealed_at"]), f"R6: the owed record: {s6[-1:]}"
    box.succeed(VERIFY)
    # ...and with the state removed again, the next boot is a first boot.
    box.succeed(f"rm {STATE}/sealed {STATE}/window.json")
    box.shutdown()
    boot_open(box)
    assert events(box)[-1] == "window-opened" and mine(box)[-1]["boots"] == 1 and elements(box) == [UID], f"R6: after the repair: {events(box)[-2:]}"
    box.succeed(VERIFY)
    box.shutdown()

    # ── noident ─────────────────────────────────────────────────────────────────────────────────
    noident.start()
    noident.wait_until_succeeds("systemctl is-failed --quiet agent-os-identity-boot.service", timeout=120)
    noident.wait_until_succeeds(f"test -e {STATE}/sealed", timeout=120)
    assert marker(noident)["reason"] == "not open", f"noident: {marker(noident)}"
    assert elements(noident) == [], f"noident: the window opened without an identity: {elements(noident)}"
    assert noident.succeed("systemctl show -p ActiveState --value agentos-setup-window.service").strip() != "active", "noident: the window unit ran"
    noident.fail(f"test -e {STATE}/window.json")
    assert chain(noident) == [], f"noident: something was written to the chain: {chain(noident)}"
    noident.fail("systemctl start agentos-setup-window-tick.service")          # it Requires the identity unit too
    assert chain(noident) == [], "noident: the tick wrote a record without an identity"
  '';
}
