#!/usr/bin/env bash
# cap-sandbox-battery.sh — WP-S2 / GATE #5(a): prove the per-cap systemd fs-confinement is REAL.
#
# Usage (MUST run as root on a host with a live SYSTEM systemd):
#   sudo tests/cap-sandbox-battery.sh <bin/cap-invoke> <cap-bin-dir> <registry.json> \
#                                     <cap-sandbox.json> [systemd-run] [bin/cap-net-fetch]
#
# This battery deliberately CANNOT run inside `nix flake check`: a check derivation has no
# systemd, no D-Bus, and no ability to create /var/lib/agent-os. The pure, eval-level half of
# this slice (the derived properties are the right ones) IS in the flake as `checks.cap-sandbox`;
# this script is the behavioural half, and it is the one that actually matters, because the claim
# under test is "the kernel stops the escape", not "the string is present in a list".
#
# It runs against the REAL capability roots (/var/lib/agent-os/{safe-read,workspace}) on purpose.
# The seam strips the impls' AGENT_OS_FILE_*_ROOT test overrides down to {PATH, AGENT_OS_REGISTRY},
# so a through-the-seam call ALWAYS uses the hardcoded production roots — pointing this battery at
# scratch roots would test a path production never takes.
#
# Twelve arms (legs 0-7, 8m, 8b, 8c, 9), in the order that makes the result meaningful:
#   0. NEGATIVE CONTROL — with the sandbox NOT configured, the escaping read SUCCEEDS and returns
#      the canary. Without this leg, legs 1-2 could pass for the wrong reason (e.g. the impl simply
#      erroring). This is the fail-OPEN state Geist's RULING 1 describes, demonstrated, not asserted.
#
#      MEASURED CORRECTION to the brief (carried into the PR): the escape is the symlinked PARENT,
#      not a symlink at the final component. cap-file-read DOES lstat-reject a final-component
#      symlink (bin/cap-file-read: "not a regular file (symlink or special file rejected)"), so
#      `safe-read/evil -> /etc/shadow` is refused by the impl and is NOT the open hole. What the
#      impl cannot close is `safe-read/dir -> /etc` then `safe-read/dir/shadow`: every component
#      but the last is resolved by the kernel during open(), and the lstat only ever examines the
#      last. That is byte-for-byte the same shape as file.write's target-only lstat gap — so the
#      two file caps share ONE residual escape, and one mount namespace closes both. The lstat->
#      open TOCTOU is the same class and is closed by the same boundary (there is no race to win
#      when the target does not exist in the namespace at all).
#   1. Positive read inside the safe root still works under confinement.
#   2. Symlinked-PARENT read (safe-read/dir -> outside, then read safe-read/dir/canary) is
#      BLOCKED, and the canary bytes never appear in the seam output.
#   3. Positive write inside the workspace still works under confinement.
#   4. Symlinked-PARENT write (workspace/sub -> outside) is BLOCKED and nothing lands outside.
#   5. Fail-closed wiring: a capability absent from the policy is DENIED, and an unset
#      AGENT_OS_SYSTEMD_RUN DENIES rather than falling back to an unconfined exec.
#   6. No transient units leak after the run.
#   7. NETWORK, the network=false direction: a cap with no declared stack cannot open a socket
#      under its own derived properties (control arm connects first).
#   8. NETWORK, the network=true direction: does IPAddressDeny actually drop packets? TWO targets,
#      because one could not tell "the mechanism is inert" from "this target is exempt" apart:
#      8b a NON-LOOPBACK denied address (does the kernel layer work at all?) and 8c loopback,
#      which the registry denies on purpose. 8a's control differs from 8b by the deny entries
#      ALONE. This gates lifting `offenders` in modules/cap-invoke-pkg.nix.
#
#      8b/8c DOWNGRADE TO NOT-DEMONSTRATED when the target is one of this host's own addresses
#      (Geist's gate on #260, 2026-09-03). On a single node every candidate 8b can find is
#      host-own by construction, so the connection routes over `lo` and the arm CANNOT pass. A
#      leg that cannot pass is as uninformative as one that cannot fail, and merging it as a hard
#      failure would redden this vm-test on every future PR forever — alarm fatigue, not a
#      finding. The protection it claims already rests in the CLOSED `offenders` list, which this
#      PR does not touch. So: host-own target -> report NOT-DEMONSTRATED loudly, qualify the final
#      line, exit 0. Genuinely-remote target -> the hard failure stays exactly as it was.
#   9. USERSPACE, cap-net-fetch's own resolve-then-check: an IPv4-MAPPED IPv6 literal
#      (`::ffff:127.0.0.1`) must be DENIED. Measured bypass, found by Geist on the Air and
#      reproduced here: `::ffff:127.0.0.1` is not a member of `127.0.0.0/8` under `in`, so
#      pre-fix it walked through `_addr_denied` and dialled host loopback. getaddrinfo does NOT
#      normalize the mapped form away (measured: `[::ffff:7f00:1]` resolves to `::ffff:127.0.0.1`,
#      not `127.0.0.1`), so the arm is not vacuous. It carries its own PRE-FIX CONTROL: the same
#      probe against a copy of the impl with the unwrap removed must be ALLOWED through, or the
#      arm is passing for a reason other than the fix.
set -u

INVOKE="${1:?path to bin/cap-invoke required}"
CAPBIN="${2:?cap bin dir required}"
REGISTRY="${3:?registry.json required}"
POLICY="${4:?cap-sandbox.json required}"
SYSTEMD_RUN="${5:-$(command -v systemd-run || echo /usr/bin/systemd-run)}"
# Leg 9 needs the net.fetch IMPL, which is deliberately NOT in capBinDir: `offenders` in
# modules/cap-invoke-pkg.nix refuses to ship any sandbox.network=true cap until the confinement is
# demonstrated, and this PR does not lift it. So the path is passed in separately rather than
# derived from CAPBIN — deriving it would make leg 9 hard-fail for exactly the reason the gate is
# working correctly.
NETFETCH="${6:-$CAPBIN/cap-net-fetch}"
PY="${PYTHON:-python3}"

[ "$(id -u)" = 0 ] || { echo "cap-sandbox SKIP-FAIL: must run as root (transient SYSTEM units)" >&2; exit 1; }
systemctl is-system-running >/dev/null 2>&1 || \
  systemctl is-system-running 2>&1 | grep -qE 'running|degraded|starting' || \
  { echo "cap-sandbox FAIL: no live system systemd" >&2; exit 1; }
[ -x "$SYSTEMD_RUN" ] || { echo "cap-sandbox FAIL: systemd-run not executable at $SYSTEMD_RUN" >&2; exit 1; }

SAFE=/var/lib/agent-os/safe-read
WS=/var/lib/agent-os/workspace
OUTSIDE=/var/lib/agent-os-cap-sandbox-outside      # deliberately OUTSIDE every declared root
CANARY="$OUTSIDE/canary.txt"
CANARY_BYTES='CAP-SANDBOX-CANARY-8f3a-must-never-be-read'

fail() { echo "cap-sandbox FAIL: $*" >&2; exit 1; }

cleanup() { rm -rf "$OUTSIDE" "$SAFE"/dir "$SAFE"/plain.txt "$WS"/sub "$WS"/good.txt "$WS"/esc.txt 2>/dev/null; }
trap cleanup EXIT

mkdir -p "$SAFE" "$WS" "$OUTSIDE" || fail "cannot create capability roots"
printf '%s\n' "$CANARY_BYTES" > "$CANARY"
chmod 0600 "$CANARY"

# ── harness ───────────────────────────────────────────────────────────────────────────────
req() { "$PY" - "$@" <<'PYEOF'
import json, sys
cap = sys.argv[1]
args = {}
for pair in sys.argv[2:]:
    k, _, v = pair.partition("=")
    args[k] = v
sys.stdout.write(json.dumps({"capability": cap, "arguments": args}))
PYEOF
}

# `eval` here is the same harness helper file-cap-battery.sh uses: the expression is a literal
# written in THIS file (never test data), applied to already-parsed JSON. Kept identical so the
# two file-cap batteries read the same.
jf() { printf '%s' "$1" | "$PY" -c 'import sys,json
o=json.load(sys.stdin); v=eval(sys.argv[1])
sys.stdout.write("true" if v is True else "false" if v is False else "None" if v is None else str(v))' "$2"; }

# Drive the dispatcher exactly as the broker's wrapper would, but with the store pins supplied as
# env (a direct `python3 bin/cap-invoke` call is the established battery affordance — see
# file-cap-battery.sh). Sets $OUT and $RC.
seam() { OUT="$(printf '%s' "$2" | env -i \
    AGENT_OS_REGISTRY="$REGISTRY" \
    AGENT_OS_CAP_BIN_DIR="$CAPBIN" \
    AGENT_OS_CAP_PATH="" \
    AGENT_OS_CAP_TIMEOUT_S=30 \
    ${1:+AGENT_OS_CAP_SANDBOX="$POLICY"} \
    ${1:+AGENT_OS_SYSTEMD_RUN="$SYSTEMD_RUN"} \
    "$PY" "$INVOKE" 2>/dev/null)"; RC=$?; }

# ── 0. NEGATIVE CONTROL: unconfined, the symlink escape WORKS ─────────────────────────────
# This is the fail-open state the confinement exists to close. If this leg ever stops
# reproducing, legs 1-2 stop proving anything and this battery must be re-derived.
ln -sfn "$OUTSIDE" "$SAFE/dir"
seam "" "$(req file.read "path=$SAFE/dir/canary.txt")"
[ "$RC" = 0 ] || fail "0: unconfined control should succeed (the escape is real) — got rc=$RC"
[ "$(jf "$OUT" 'o["ok"]')" = "true" ] || fail "0: unconfined control should read the canary ($OUT)"
case "$OUT" in
  *CAP-SANDBOX-CANARY-8f3a*) : ;;
  *) fail "0: unconfined control did not return canary bytes — control is not exercising the escape" ;;
esac
echo "cap-sandbox 0 OK  (negative control: unconfined symlink escape reproduces — canary read)"

# ── 1. positive read inside the safe root, CONFINED ───────────────────────────────────────
printf 'hello confined world' > "$SAFE/plain.txt"
seam yes "$(req file.read "path=$SAFE/plain.txt")"
[ "$RC" = 0 ] || fail "1: confined in-root read should exit 0, got $RC ($OUT)"
[ "$(jf "$OUT" 'o["ok"]')" = "true" ] || fail "1: confined in-root read should be ok ($OUT)"
[ "$(jf "$OUT" 'o["content"]')" = 'hello confined world' ] || fail "1: content mismatch ($OUT)"
echo "cap-sandbox 1 OK  (confined read inside SAFE_ROOT works — unit launched, impl ran)"

# ── 2. planted-symlink read is BLOCKED ────────────────────────────────────────────────────
seam yes "$(req file.read "path=$SAFE/dir/canary.txt")"
[ "$RC" != 0 ] || fail "2: SYMLINK ESCAPE OPEN — confined read of $SAFE/dir/canary.txt exited 0 ($OUT)"
[ "$(jf "$OUT" 'o["ok"]')" = "false" ] || fail "2: escape leg should report ok=false ($OUT)"
case "$OUT" in
  *CAP-SANDBOX-CANARY-8f3a*) fail "2: CANARY BYTES LEAKED through the seam ($OUT)" ;;
esac
echo "cap-sandbox 2 OK  (symlinked-parent read CLOSED by the mount namespace, no canary bytes)"

# ── 3. positive write inside the workspace, CONFINED ──────────────────────────────────────
rm -f "$WS/good.txt"
seam yes "$(req file.write "path=$WS/good.txt" 'content=written under confinement')"
[ "$RC" = 0 ] || fail "3: confined in-root write should exit 0, got $RC ($OUT)"
[ "$(jf "$OUT" 'o["ok"]')" = "true" ] || fail "3: confined in-root write should be ok ($OUT)"
[ -f "$WS/good.txt" ] || fail "3: file did not land in the workspace"
[ "$(cat "$WS/good.txt")" = 'written under confinement' ] || fail "3: on-disk bytes wrong"
echo "cap-sandbox 3 OK  (confined write inside WORKSPACE_ROOT works, bytes land on the host)"

# ── 4. symlinked-PARENT write is BLOCKED ──────────────────────────────────────────────────
# cap-file-write's lstat check is TARGET-only, so this is the leg the impl cannot close: the
# final component is a fresh name, but its PARENT redirects out of the workspace.
ln -sfn "$OUTSIDE" "$WS/sub"
seam yes "$(req file.write "path=$WS/sub/esc.txt" 'content=should never land')"
[ "$RC" != 0 ] || fail "4: SYMLINKED-PARENT WRITE OPEN — exited 0 ($OUT)"
[ -e "$OUTSIDE/esc.txt" ] && fail "4: bytes landed OUTSIDE the workspace at $OUTSIDE/esc.txt"
echo "cap-sandbox 4 OK  (symlinked-parent write CLOSED, nothing landed outside the workspace)"

# ── 5. fail-closed wiring ─────────────────────────────────────────────────────────────────
# 5a. a capability with NO derived confinement entry must be denied, not run bare.
EMPTY_POLICY="$(mktemp)"; printf '{}' > "$EMPTY_POLICY"
OUT="$(printf '%s' "$(req file.read "path=$SAFE/plain.txt")" | env -i \
  AGENT_OS_REGISTRY="$REGISTRY" AGENT_OS_CAP_BIN_DIR="$CAPBIN" AGENT_OS_CAP_PATH="" \
  AGENT_OS_CAP_SANDBOX="$EMPTY_POLICY" AGENT_OS_SYSTEMD_RUN="$SYSTEMD_RUN" \
  "$PY" "$INVOKE" 2>/dev/null)"; RC=$?
rm -f "$EMPTY_POLICY"
[ "$RC" != 0 ] || fail "5a: cap absent from the policy ran anyway (fail-OPEN) ($OUT)"

# 5b. a configured policy with NO launcher must deny — never fall back to the unconfined exec.
OUT="$(printf '%s' "$(req file.read "path=$SAFE/dir/canary.txt")" | env -i \
  AGENT_OS_REGISTRY="$REGISTRY" AGENT_OS_CAP_BIN_DIR="$CAPBIN" AGENT_OS_CAP_PATH="" \
  AGENT_OS_CAP_SANDBOX="$POLICY" AGENT_OS_SYSTEMD_RUN=/nonexistent/systemd-run \
  "$PY" "$INVOKE" 2>/dev/null)"; RC=$?
[ "$RC" != 0 ] || fail "5b: missing launcher fell back to an UNCONFINED exec ($OUT)"
case "$OUT" in
  *CAP-SANDBOX-CANARY-8f3a*) fail "5b: canary leaked on the missing-launcher path ($OUT)" ;;
esac

# 5c. an unreadable policy is a confinement failure, not a licence to run bare.
OUT="$(printf '%s' "$(req file.read "path=$SAFE/dir/canary.txt")" | env -i \
  AGENT_OS_REGISTRY="$REGISTRY" AGENT_OS_CAP_BIN_DIR="$CAPBIN" AGENT_OS_CAP_PATH="" \
  AGENT_OS_CAP_SANDBOX=/nonexistent/policy.json AGENT_OS_SYSTEMD_RUN="$SYSTEMD_RUN" \
  "$PY" "$INVOKE" 2>/dev/null)"; RC=$?
[ "$RC" != 0 ] || fail "5c: unreadable policy ran the impl anyway ($OUT)"
echo "cap-sandbox 5 OK  (no-entry / no-launcher / unreadable-policy all DENY, never fall back)"

# ── 6. no transient units leak ────────────────────────────────────────────────────────────
# Arm the matcher BEFORE trusting its zero. `wc -l` can never come back empty, so this leg looks
# un-vacuous -- but it can come back vacuously ZERO, and zero is the PASSING value. The glob is a
# bare literal spelled in three unconnected places: bin/cap-invoke:221 (the only creator of these
# units), here, and tests/cap-composed-path.nix. Rename the prefix there and both leak checks go
# permanently green against an empty match set. A decoy under the same glob must be visible first.
cap_units() { systemctl list-units --all --no-legend 'agent-os-cap-*' 2>/dev/null | wc -l; }
"$SYSTEMD_RUN" --unit=agent-os-cap-matcherprobe --property=RemainAfterExit=yes /bin/sh -c true \
  >/dev/null 2>&1 || fail "6: could not create the matcher probe unit; leg 6 cannot be armed"
SEEN="$(cap_units)"
[ "$SEEN" != 0 ] || fail "6: matcher INERT -- a unit created under agent-os-cap-* was invisible to \
the glob, so every previous pass of this leg measured an empty match set, not a clean box"
systemctl stop agent-os-cap-matcherprobe.service >/dev/null 2>&1 || true
systemctl reset-failed agent-os-cap-matcherprobe.service >/dev/null 2>&1 || true

LEAKED="$(cap_units)"
[ "$LEAKED" = 0 ] || fail "6: $LEAKED transient agent-os-cap-* unit(s) leaked (--collect not working)"
echo "cap-sandbox 6 OK  (transient units collected, none left behind; matcher armed, saw $SEEN)"

# ── 7. the NETWORK half of the confinement is real, not just a string ─────────────────────
# Legs 0-6 are all filesystem. The network boundary has had exactly ONE gate: flake.nix asserts
# the DERIVED POLICY CONTAINS the string "PrivateNetwork=yes" for every non-network cap. That is
# the same shape of evidence `ProtectSystem=strict` already defeated once in this very module — a
# property can be present, correct-looking, and cancelled by composition. Nothing had ever
# observed a confined impl failing to open a socket.
#
# The properties come FROM the materialized policy for a SHIPPED cap (file.read,
# sandbox.network=false) — never hand-written here, or this leg would test the string it exists
# to stop trusting.
#
# WHY ONLY THE NETWORK PROPERTIES, and not the whole list: the first version of this leg applied
# the FULL policy and was VACUOUS. The fs confinement (TemporaryFileSystem=/:ro + InaccessiblePaths)
# stops an arbitrary probe binary from executing at all, so the unit exited non-zero for reasons
# having nothing to do with networking — and a non-zero exit was being counted as "network
# blocked". It passed with PrivateNetwork=no. Caught by mutation-testing, which is the only reason
# it is not still sitting here looking like coverage.
#
# The fix is the marker below, not the subsetting: the probe PRINTS before it dials, and both arms
# assert the marker appeared. A probe that never ran can no longer be mistaken for a probe that
# was refused. Subsetting to the net properties is what lets the probe run at all.
NETDIR="$(mktemp -d)"
NETPORT=""
NETPID=""
"$PY" - <<'PYEOF' > "$NETDIR/netport" 2>/dev/null &
import socket, sys, time
s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(8)
sys.stdout.write(str(s.getsockname()[1])); sys.stdout.flush()
time.sleep(60)
PYEOF
NETPID=$!
netcleanup() { kill "$NETPID" 2>/dev/null || true; rm -rf "$NETDIR"; }
# Wait for the port to be published rather than sleeping a guessed interval.
for _ in $(seq 1 50); do
  NETPORT="$(cat "$NETDIR/netport" 2>/dev/null || true)"
  [ -n "$NETPORT" ] && break
  sleep 0.1
done
[ -n "$NETPORT" ] || { netcleanup; fail "7: probe listener never published a port — harness broken, not a finding"; }

# The NETWORK properties, taken from the materialized policy for file.read. Absent, empty, or
# carrying no PrivateNetwork at all is a hard fail: an empty property list would make the confined
# arm identical to the control arm and this leg would "pass" by proving nothing.
NETPROPS="$("$PY" - "$POLICY" <<'PYEOF'
import json, sys
pol = json.load(open(sys.argv[1]))
props = pol.get("file.read")
if not props:
    sys.stderr.write("file.read absent from the materialized policy\n"); sys.exit(1)
net = [p for p in props
       if p.split("=")[0] in ("PrivateNetwork", "IPAddressAllow", "IPAddressDeny",
                              "RestrictAddressFamilies")]
if not any(p.startswith("PrivateNetwork") for p in net):
    sys.stderr.write("file.read policy carries no PrivateNetwork property at all\n"); sys.exit(1)
sys.stdout.write(" ".join("--property=" + p for p in net))
PYEOF
)" || { netcleanup; fail "7: could not derive network properties for file.read from $POLICY"; }

# The probe prints RAN before it dials, then exits 0 on connect / 7 on refusal. Written once, run
# twice — the ONLY difference between the arms is $NETPROPS.
NETPROBE_SRC="import socket,sys
print('PROBE-RAN', flush=True)
try:
    socket.create_connection(('127.0.0.1', $NETPORT), timeout=3).close()
except Exception:
    sys.exit(7)"

# 7a. CONTROL ARM FIRST, unconfined. MUST print the marker and connect — otherwise the listener is
# wrong or the port is stale, and 7b's "blocked" would be meaningless.
NETOUT="$("$PY" -c "$NETPROBE_SRC" 2>&1)"; NETRC=$?
case "$NETOUT" in *PROBE-RAN*) : ;; *) netcleanup; fail "7a: control probe never ran ($NETOUT)" ;; esac
[ "$NETRC" = 0 ] || { netcleanup; fail "7a: unconfined control could not reach the listener on 127.0.0.1:$NETPORT (rc=$NETRC) — a blocked confined arm would prove nothing"; }

# 7b. Same probe, under the real derived network properties.
NETOUT="$("$SYSTEMD_RUN" --quiet --pipe --wait --collect $NETPROPS "$PY" -c "$NETPROBE_SRC" 2>&1)"; NETRC=$?
# The marker is the load-bearing assertion. Without it, "the probe was refused" and "the probe
# never executed" are the same observation — which is exactly how the first version of this leg
# passed against a cancelled confinement.
case "$NETOUT" in
  *PROBE-RAN*) : ;;
  *) netcleanup; fail "7b: probe never executed under the derived network properties, so this leg proved NOTHING about the network (rc=$NETRC, out=$NETOUT)" ;;
esac
[ "$NETRC" != 0 ] || { netcleanup; fail "7b: a cap with sandbox.network=false REACHED the network under its own derived policy — PrivateNetwork is in the string list but not in effect"; }
netcleanup
echo "cap-sandbox 7 OK  (network=false cap has NO stack under its real derived properties — control arm connected, confined arm RAN and was refused)"

# ── 8. the network=TRUE half: does IPAddressDeny actually STOP packets? ───────────────────────
# Leg 7 proves a cap declared network=false has no stack. That is the easy direction. This is the
# direction the T2 egress slice rests on — a cap that IS allowed a stack, confined to "public
# internet only" by IPAddressAllow=any plus a per-CIDR IPAddressDeny list (modules/cap-sandbox.nix
# `netProps`, derived from the registry's egressDenyList). Nothing had ever observed a packet being
# dropped by that list.
#
# WHY THIS GATES THE SHIP. modules/cap-invoke-pkg.nix `offenders` refuses to put any
# sandbox.network=true impl in capBinDir. IPAddressDeny is a cgroup-v2 BPF filter, and on a host
# without that support systemd logs a WARNING and the property SILENTLY DOES NOT APPLY. The unit
# starts, `systemctl show` lists it, the materialized policy is unchanged. That fail-OPEN has
# artifacts identical to the enforcing case, so it must be OBSERVED, not assumed.
#
# MEASURED CORRECTION, and it is why this leg has two targets. The first version probed 127.0.0.1
# and 8b FAILED in the VM: the connection went through under the derived deny list. The tempting
# reading — "this kernel has no BPF, the deny list is inert" — is contradicted by leg 7's own
# systemd output in the SAME run, which reported per-unit "incoming IP traffic / outgoing IP
# traffic" byte counts. That accounting is the same cgroup BPF machinery. So BPF is present, and
# loopback specifically was not filtered. A single target could not tell those apart.
#
# The distinction is not a detail, because the registry denies loopback ON PURPOSE
# (capability-registry.nix: "a confirmed fetch to 127.0.0.1:11434 could drive the in-guest model /
# pull weights"). Two targets, two different claims, both load-bearing:
#
#   8b  a NON-LOOPBACK denied address — does the kernel layer work AT ALL?
#   8c  the loopback denied address   — is the registry's stated loopback threat covered by the
#                                       kernel layer, or ONLY by cap-net-fetch's resolve-check?
#
# 8a differs from 8b by the deny entries ALONE — a tighter pair than leg 7's, whose arms differ by
# the whole property set. Every arm asserts the PROBE-RAN marker, per leg 7's scar where a probe
# that never executed read as a probe that was refused.

# A NON-LOOPBACK IPv4 on this host that is itself inside a denied CIDR. Hard-fail rather than skip
# if there is none: a silently-absent arm is what makes a battery look like coverage.
#
# KNOWN LIMIT OF THIS TARGET, recorded because the next reader will otherwise re-derive it: the
# address is one of THIS HOST's own, so the listener is self-hosted and Linux routes the connection
# over `lo` even though the address is not 127.x. It is non-loopback by CIDR, not by route. So an
# 8b failure alone cannot separate "the filter is inert" from "the filter does not see loopback
# traffic".
#
# RETRACTED, 2026-09-03 (Geist's #262 ruling, verified here against the systemd source manual):
# earlier revisions of this file asserted that "systemd's IP filtering is documented not to apply
# to the loopback device". THAT SENTENCE WAS NEVER TRUE AND WAS NEVER CITED. systemd.resource-
# control(5) says the opposite on both points: the access lists "are applied to all sockets created
# by processes of this unit", and `localhost` is an explicitly filterable SYMBOLIC NAME expanding to
# `127.0.0.0/8 ::1/128`. There is no loopback exemption in the manual. The fabricated sentence is
# what licensed 8b/8c's NOT-DEMONSTRATED downgrade, so it did not merely sit in a comment: it
# converted a measurement into a shrug. Leg 8m below replaces the claim with an experiment.
#
# MEASURED, 2026-09-03, which is why 8b's message still says "inert": the same experiment was run
# on a second, unrelated host (WSL2, kernel 6.6, cgroup2, as root) against a GENUINELY REMOTE
# target — IPAddressDeny=1.1.1.1/32 then connect to 1.1.1.1:443. The connection was NOT blocked,
# with the no-deny control also connecting. So on two independent environments the deny list did
# not filter, one of them over a route that was certainly not loopback. That is convergent, not
# conclusive: it does not prove the VM's failure had the same cause.
#
# WHAT WOULD SETTLE IT and is not built here: a SECOND NixOS test node, so 8b can probe an
# off-host address in a denied CIDR over a real route. That is a change to the vm-test harness
# (a two-node nixosTest), not to this battery, and it is the honest next step before anyone
# concludes the confinement is or is not real.
# Emits "<addr> <hostown:yes|no>". HOSTOWN is what decides whether an 8b failure is a FINDING or
# merely NOT-DEMONSTRATED, so it is MEASURED, not assumed: the address is compared against this
# host's own set (getaddrinfo of the hostname, plus the kernel-selected source address for each
# probe route — a `getsockname` after a UDP connect returns a local address by definition).
#
# On a single node every candidate is host-own, so this always reports yes and 8b can never pass.
# AGENT_OS_BATTERY_REMOTE_DENIED_ADDR is the seam the two-node nixosTest fills in with a genuinely
# off-host address in a denied CIDR; supplied that way, HOSTOWN is no and the hard failure returns.
# The hook exists so the remote branch is REACHABLE — a branch no harness can enter is untested
# code that reads as coverage, which is the defect this battery is otherwise built to avoid.
NETINFO="$("$PY" - <<'PY8A'
import ipaddress, os, socket, sys
DENY = [ipaddress.ip_network(c) for c in
        ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16")]  # gate-allow
own = set()
try:
    for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        own.add(info[4][0])
except OSError:
    pass
for probe in ("10.0.2.2", "1.1.1.1"):  # qemu user-net gw, then a public addr  # gate-allow
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((probe, 9)); own.add(s.getsockname()[0])
    except OSError:
        pass
    finally:
        s.close()

supplied = os.environ.get("AGENT_OS_BATTERY_REMOTE_DENIED_ADDR", "").strip()
if supplied:
    ip = ipaddress.ip_address(supplied)
    if ip.is_loopback or not any(ip in n for n in DENY):
        sys.stderr.write("supplied remote target %s is loopback or not in a denied CIDR\n" % supplied)
        sys.exit(1)
    sys.stdout.write("%s %s" % (supplied, "yes" if supplied in own else "no"))
    sys.exit(0)

for a in sorted(own):
    ip = ipaddress.ip_address(a)
    if not ip.is_loopback and any(ip in n for n in DENY):
        sys.stdout.write("%s yes" % a); sys.exit(0)
sys.stderr.write("no non-loopback IPv4 inside a denied CIDR (candidates: %r)\n" % sorted(own))
sys.exit(1)
PY8A
)" || fail "8: no non-loopback denied address to probe — this leg cannot run here, and a skipped arm must not read as a pass"
read -r NETADDR NETOWN <<EOF
$NETINFO
EOF
[ -n "$NETADDR" ] && [ -n "$NETOWN" ] || fail "8: target selector did not emit '<addr> <hostown>' (got: $NETINFO)"
case "$NETOWN" in yes|no) : ;; *) fail "8: hostown must be yes or no, got $NETOWN" ;; esac
[ "$NETOWN" = no ] || echo "cap-sandbox 8: NOTE — $NETADDR is one of THIS HOST's own addresses, so the 8b/8c route is loopback and those arms can only report NOT-DEMONSTRATED (set AGENT_OS_BATTERY_REMOTE_DENIED_ADDR from a two-node harness to make them decisive)"

NETDIR="$(mktemp -d)"
NETPID=""
"$PY" - "$NETADDR" <<'PY8B' > "$NETDIR/ports" 2>/dev/null &
import socket, sys, time
a = socket.socket(); a.bind((sys.argv[1], 0)); a.listen(8)
b = socket.socket(); b.bind(("127.0.0.1", 0)); b.listen(8)
sys.stdout.write("%d %d" % (a.getsockname()[1], b.getsockname()[1])); sys.stdout.flush()
time.sleep(120)
PY8B
NETPID=$!
netcleanup() { kill "$NETPID" 2>/dev/null || true; rm -rf "$NETDIR"; }
NETPORT=""; LOPORT=""
for _ in $(seq 1 50); do
  read -r NETPORT LOPORT < "$NETDIR/ports" 2>/dev/null || true
  [ -n "$NETPORT" ] && [ -n "$LOPORT" ] && break
  sleep 0.1
done
[ -n "$NETPORT" ] && [ -n "$LOPORT" ] || { netcleanup; fail "8: probe listeners never published ports — harness broken, not a finding"; }

# net.fetch's derived network properties. Five hard failures rather than skips, because each would
# let 8b pass while proving nothing.
NETPROPS="$("$PY" - "$POLICY" "$NETADDR" <<'PY8C'
import ipaddress, json, sys
pol = json.load(open(sys.argv[1])); target = ipaddress.ip_address(sys.argv[2])
props = pol.get("net.fetch")
if not props:
    sys.stderr.write("net.fetch absent from the materialized policy\n"); sys.exit(1)
net = [p for p in props
       if p.split("=")[0] in ("PrivateNetwork", "IPAddressAllow", "IPAddressDeny",
                              "RestrictAddressFamilies")]
if "IPAddressAllow=any" not in net:
    sys.stderr.write("no IPAddressAllow=any — not a network=true cap\n"); sys.exit(1)
if any(p.startswith("PrivateNetwork=yes") for p in net):
    sys.stderr.write("PrivateNetwork=yes present — 8b would block for the wrong reason\n"); sys.exit(1)
denies = [ipaddress.ip_network(p.split("=", 1)[1]) for p in net if p.startswith("IPAddressDeny=")]
if not any(target in d for d in denies):
    sys.stderr.write("policy does not deny %s, the 8b target\n" % target); sys.exit(1)
if not any(ipaddress.ip_address("127.0.0.1") in d for d in denies):
    sys.stderr.write("policy does not deny 127.0.0.1, the 8c target\n"); sys.exit(1)
sys.stdout.write(" ".join("--property=" + p for p in net))
PY8C
)" || { netcleanup; fail "8: could not derive network properties for net.fetch from $POLICY"; }

probe_src() { printf "import socket,sys\nprint('PROBE-RAN', flush=True)\ntry:\n    socket.create_connection(('%s', %s), timeout=3).close()\nexcept Exception:\n    sys.exit(7)\n" "$1" "$2"; }

# 8a. CONTROL ARM FIRST: a unit WITH a stack and NO denies. Must run and connect, or 8b's refusal
# would be attributable to systemd-run, the unit env, or the listener.
NETOUT="$("$SYSTEMD_RUN" --quiet --pipe --wait --collect --property=IPAddressAllow=any \
            "$PY" -c "$(probe_src "$NETADDR" "$NETPORT")" 2>&1)"; NETRC=$?
case "$NETOUT" in *PROBE-RAN*) : ;; *) netcleanup; fail "8a: control probe never ran ($NETOUT)" ;; esac
[ "$NETRC" = 0 ] || { netcleanup; fail "8a: a unit with IPAddressAllow=any and no denies could NOT reach $NETADDR:$NETPORT (rc=$NETRC) — 8b would prove nothing"; }

# 8m. MECHANISM, ON LOOPBACK, BEFORE ANY DERIVED LIST IS INVOLVED. This arm exists because the
# thing 8b/8c most needed was never measured: does cgroup IP filtering do ANYTHING to a loopback
# connection on this kernel? Every earlier revision answered that from a sentence about loopback
# exemption that was fabricated -- see the RETRACTED note above -- and then downgraded two arms on
# it. So: probe 127.0.0.1 under a deny list written HERE, not derived from policy, so a failure
# cannot be blamed on rendering.
#
# Three sub-arms, and the control is first for the usual reason:
#   8m-control  IPAddressAllow=any        must CONNECT   (else the harness, not the filter, is why)
#   8m-any      IPAddressDeny=any         must be REFUSED
#   8m-local    IPAddressDeny=localhost   must be REFUSED  (the manual's own symbolic name for
#                                                            127.0.0.0/8 ::1/128)
# `IPAddressDeny=any` is the blunt instrument on purpose: if even THAT does not stop a loopback
# connection, the filter is inert on this host and no deny list of any shape will help. `localhost`
# is then the named-range form the manual documents, which is what the shipping policy's 127.0.0.0/8
# entry is meant to be doing.
MECH=unknown
NETOUT="$("$SYSTEMD_RUN" --quiet --pipe --wait --collect --property=IPAddressAllow=any \
            "$PY" -c "$(probe_src 127.0.0.1 "$LOPORT")" 2>&1)"; NETRC=$?
case "$NETOUT" in *PROBE-RAN*) : ;; *) netcleanup; fail "8m-control: loopback control probe never ran ($NETOUT)" ;; esac
[ "$NETRC" = 0 ] || { netcleanup; fail "8m-control: a unit with IPAddressAllow=any could NOT reach 127.0.0.1:$LOPORT (rc=$NETRC) -- the loopback listener or the runner is broken, so 8m/8c would prove nothing"; }

# Record what systemd actually applied, rather than what was asked for. A property silently
# dropped by an old systemd, or a BPF filter that failed to install, is indistinguishable from an
# inert filter at the socket -- and those are different findings with different fixes.
SHOWUNIT="agentos-8m-show-$$"
"$SYSTEMD_RUN" --quiet --unit="$SHOWUNIT" --property=IPAddressDeny=any --property=IPAddressAllow=localhost sleep 3 >/dev/null 2>&1 || true
echo "cap-sandbox 8m: applied properties -> $(systemctl show -p IPAddressDeny -p IPAddressAllow "$SHOWUNIT.service" 2>&1 | tr '\n' ' ')"
echo "cap-sandbox 8m: filter/BPF journal lines -> $(journalctl -u "$SHOWUNIT.service" --no-pager -n 50 2>/dev/null | grep -iE 'bpf|ip filter|firewall|EPERM' | tr '\n' ' ' | sed 's/^$/(none)/')"
systemctl stop "$SHOWUNIT.service" >/dev/null 2>&1 || true

NETOUT="$("$SYSTEMD_RUN" --quiet --pipe --wait --collect --property=IPAddressDeny=any \
            "$PY" -c "$(probe_src 127.0.0.1 "$LOPORT")" 2>&1)"; NETRC=$?
case "$NETOUT" in *PROBE-RAN*) : ;; *) netcleanup; fail "8m-any: probe never executed under IPAddressDeny=any, so this arm measured nothing (rc=$NETRC, out=$NETOUT)" ;; esac
if [ "$NETRC" = 0 ]; then
  MECH=inert
  echo "cap-sandbox 8m-any: MEASURED INERT -- a loopback connection succeeded under IPAddressDeny=any, the broadest deny systemd accepts, with the control arm connecting too. cgroup IP filtering does not cover loopback traffic on this host. This is the FINDING, not a harness limit: the registry's 127.0.0.1:11434 denial cannot be resting on this layer."
else
  MECH=works
  echo "cap-sandbox 8m-any OK (a loopback connection WAS refused under IPAddressDeny=any -- the mechanism reaches loopback traffic on this host)"
fi

NETOUT="$("$SYSTEMD_RUN" --quiet --pipe --wait --collect --property=IPAddressDeny=localhost \
            "$PY" -c "$(probe_src 127.0.0.1 "$LOPORT")" 2>&1)"; NETRC=$?
case "$NETOUT" in *PROBE-RAN*) : ;; *) netcleanup; fail "8m-local: probe never executed under IPAddressDeny=localhost, so this arm measured nothing (rc=$NETRC, out=$NETOUT)" ;; esac
if [ "$NETRC" = 0 ]; then
  if [ "$MECH" = works ]; then
    netcleanup
    fail "8m-local: IPAddressDeny=localhost did NOT stop a loopback connection, even though IPAddressDeny=any DID stop the same probe moments earlier. The mechanism works here; the SYMBOLIC NAME the manual documents for 127.0.0.0/8 is what failed. That is a real finding about the named range, not a route artifact."
  fi
  echo "cap-sandbox 8m-local: connection also succeeded under IPAddressDeny=localhost, consistent with 8m-any's inert result (no new information -- both are explained by the filter not covering loopback)"
else
  echo "cap-sandbox 8m-local OK (IPAddressDeny=localhost refused the loopback probe -- the manual's named range behaves as documented)"
fi

# 8b. Same probe, same runner, same target, plus the derived denies and nothing else.
NETOUT="$("$SYSTEMD_RUN" --quiet --pipe --wait --collect $NETPROPS \
            "$PY" -c "$(probe_src "$NETADDR" "$NETPORT")" 2>&1)"; NETRC=$?
case "$NETOUT" in
  *PROBE-RAN*) : ;;
  *) netcleanup; fail "8b: probe never executed under the derived properties, so this leg proved NOTHING (rc=$NETRC, out=$NETOUT)" ;;
esac
NOTDEMO=0
if [ "$NETRC" = 0 ]; then
  if [ "$NETOWN" = yes ]; then
    # NOT a finding, and NOT a pass. The target is host-own, so the connection routed over `lo`,
    # and leg 8m -- not a sentence about the manual -- is what says whether the filter covers that
    # route on this host. Reported loudly, suite continues, exit code unaffected.
    NOTDEMO=1
    echo "cap-sandbox 8b NOT-DEMONSTRATED: the probe reached $NETADDR under the derived IPAddressDeny list, but that address is one of THIS HOST's own, so the route is loopback (see leg 8m for whether the filter covers that route here). This arm CANNOT pass on a single node — it is not evidence the filter is inert, and it is not evidence the filter works. Settle it with a two-node nixosTest (AGENT_OS_BATTERY_REMOTE_DENIED_ADDR). Until then the shipping gate in modules/cap-invoke-pkg.nix STAYS: do NOT lift \`offenders\`."
  else
    netcleanup
    fail "8b: IPAddressDeny did NOT stop a connection to $NETADDR, an address the policy denies, over a route that is NOT this host's own. That is a real finding, not a route artifact: the kernel layer is inert here. The shipping gate in modules/cap-invoke-pkg.nix STAYS and slice 1 switches to the netns+proxy shape."
  fi
else
  echo "cap-sandbox 8b OK (network=true: control arm with a stack reached $NETADDR; the SAME probe under the derived IPAddressDeny list RAN and was refused — the only delta is the deny entries)"
fi

# 8c. LOOPBACK, denied deliberately by the registry (the in-guest model on 127.0.0.1:11434). Its own
# arm because it is a DIFFERENT claim from 8b: 8b says the mechanism works, 8c says whether it
# reaches this target.
NETOUT="$("$SYSTEMD_RUN" --quiet --pipe --wait --collect $NETPROPS \
            "$PY" -c "$(probe_src 127.0.0.1 "$LOPORT")" 2>&1)"; NETRC=$?
case "$NETOUT" in
  *PROBE-RAN*) : ;;
  *) netcleanup; fail "8c: loopback probe never executed, so this arm measured nothing (rc=$NETRC, out=$NETOUT)" ;;
esac
netcleanup
if [ "$NETRC" != 0 ]; then
  echo "cap-sandbox 8c OK (loopback is ALSO filtered — the registry's 127.0.0.1:11434 threat is covered by the kernel layer, not only by cap-net-fetch's resolve-check)"
elif [ "$MECH" = inert ]; then
  # 8c's claim INTERPRETS ITSELF AGAINST A WORKING MECHANISM — "the derived list misses loopback"
  # is only sayable once something has shown the filter reaches loopback at all. That is leg 8m's
  # job, and it is keyed here rather than on 8b's downgrade: 8b is about a DIFFERENT route, and
  # keying one arm's meaning on another arm's inability to run is how the fabricated exemption
  # sentence stayed load-bearing for as long as it did.
  echo "cap-sandbox 8c NOT-DEMONSTRATED (as to the deny LIST): the loopback probe reached 127.0.0.1 under the derived deny list, but leg 8m measured the filter INERT on loopback here — so this arm cannot separate 'the derived list is wrong' from 'no list would have worked'. The registry denies loopback on purpose, so on this host that threat rests ONLY on cap-net-fetch's resolve-then-check (one userspace layer). The shipping gate stays."
elif [ "$MECH" = works ]; then
  netcleanup
  fail "8c: IPAddressDeny did NOT stop a connection to 127.0.0.1, even though leg 8m refused the SAME probe on the SAME route under IPAddressDeny=any. The mechanism reaches loopback here and the DERIVED list did not -- a rendering or application bug in the policy the capability actually runs under, not a route artifact and not a kernel limit. capability-registry.nix denies loopback ON PURPOSE (a fetch to 127.0.0.1:11434 could drive the in-guest model). Rule on this before lifting the shipping gate."
else
  # Unreachable while leg 8m runs, which is the point of asserting it rather than assuming it: if a
  # future edit moves or skips 8m, this arm must refuse to interpret itself rather than fall back on
  # whichever sentence happens to be sitting here.
  netcleanup
  fail "8c: the loopback probe reached 127.0.0.1 under the derived deny list, but leg 8m did not run (MECH=$MECH), so nothing here can say whether the filter covers loopback on this host. Refusing to classify: this is exactly the state in which the fabricated loopback-exemption sentence was previously supplied as the answer."
fi

# 9. USERSPACE: cap-net-fetch must DENY an IPv4-mapped IPv6 literal. This is the layer 1 half —
# legs 8b/8c are the kernel half — and it is measured through the impl's REAL entry point (stdin
# JSON on the seam contract), not by calling a private function.
#
# The arm is only meaningful with its PRE-FIX CONTROL, so both run: a copy of the impl with the
# unwrap line removed must ALLOW the same input through. Without that, leg 9 would pass on any impl
# that fails every fetch for any reason at all — including a broken one.
CNF="$NETFETCH"
[ -x "$CNF" ] || fail "9: net.fetch impl not found or not executable at '$CNF' — this arm cannot run, and a skipped arm must not read as a pass. Pass its path as argument 6 (capBinDir does not carry it while \`offenders\` is closed)."

# `[::ffff:7f00:1]` over `[::ffff:127.0.0.1]` deliberately: it is the form a hostile AAAA record
# takes, and it proves the check does not merely string-match the dotted tail. MEASURED: getaddrinfo
# resolves it to `::ffff:127.0.0.1` and does NOT flatten it to `127.0.0.1`, so the mapped form is
# what reaches the membership test.
MAPPED_URL='http://[::ffff:7f00:1]:11434/'
# The rejection this arm is about, by its exact stderr text. MEASURED, and it is why the control
# below cannot key on the exit code: pre-fix the address check PASSES and the dial then fails with
# "fetch failed (Connection refused)" — a DIFFERENT rejection producing the SAME ok=false/exit 3.
# An arm that only checked "it refused" would pass on the broken impl.
DENY_MSG='host resolves to a denied'
mapped_out() { printf '{"capability":"net.fetch","arguments":{"url":"%s","method":"GET"}}' "$MAPPED_URL" | "$1" 2>&1; }

OUT="$(mapped_out "$CNF")"; RC=$?
[ "$RC" = 3 ] || fail "9: cap-net-fetch exited $RC (want 3) on the mapped-loopback URL $MAPPED_URL — output: $OUT"
case "$OUT" in
  *'"ok":false'*) : ;;
  *) fail "9: cap-net-fetch did not report ok=false on $MAPPED_URL ($OUT)" ;;
esac
case "$OUT" in
  *"$DENY_MSG"*) : ;;
  *) fail "9: cap-net-fetch refused $MAPPED_URL but NOT at the address check (wanted '$DENY_MSG'). Some other guard rejected it, so this arm did not measure the mapped-IPv6 denial: $OUT" ;;
esac

# 9-CONTROL (pre-fix): with the unwrap neutered, the SAME input must get PAST the address check.
# It will still fail — nothing is listening — so the discriminator is WHICH rejection, not whether
# one happened.
PREFIX_DIR="$(mktemp -d)"
# NEUTERED, not deleted: the unwrap is the body of an `if`, so removing the line leaves a dangling
# block and the control dies with IndentationError — which exits non-zero and would have read as
# "the pre-fix impl refused it too". A control arm that crashes must not be mistakable for a
# control arm that measured something.
sed 's/ip = ip\.ipv4_mapped.*/pass  # 9-control: unwrap disabled/' "$CNF" > "$PREFIX_DIR/cap-net-fetch"
chmod +x "$PREFIX_DIR/cap-net-fetch"
cmp -s "$CNF" "$PREFIX_DIR/cap-net-fetch" && { rm -rf "$PREFIX_DIR"; fail "9-control: the unwrap line was not found to neuter, so the control arm is a COPY of the fixed impl and proves nothing"; }
"$PY" -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$PREFIX_DIR/cap-net-fetch" \
  || { rm -rf "$PREFIX_DIR"; fail "9-control: the neutered impl does not parse, so its refusal would be a crash rather than a policy decision"; }
PREOUT="$(mapped_out "$PREFIX_DIR/cap-net-fetch")"
rm -rf "$PREFIX_DIR"
case "$PREOUT" in
  *"$DENY_MSG"*)
    fail "9-control: the PRE-FIX impl ALSO refused $MAPPED_URL at the address check. Leg 9 cannot attribute its pass to the ipv4_mapped unwrap — either the neuter did not take effect or another guard denies this address first. Fix the control before trusting the arm." ;;
esac
echo "cap-sandbox 9 OK (cap-net-fetch denies $MAPPED_URL AT THE ADDRESS CHECK; with the ipv4_mapped unwrap neutered the same input gets past it and fails later at the dial instead — the pass is attributable to the fix, not to the fetch merely failing)"

# NOT COVERED HERE, stated so it is not mistaken for coverage: that PUBLIC egress still SUCCEEDS
# under net.fetch's properties (i.e. the deny list did not degenerate into deny-everything). That
# needs a reachable off-box endpoint and would make this battery non-hermetic on a sealed host.
# Nor is NAT64 (`64:ff9b::/96`) covered: it embeds the same IPv4 space and still passes both layers.
# The registry `egressDenyList` does not carry it either, so it moves as one change to both lists.
if [ "$NOTDEMO" = 1 ]; then
  echo "cap-sandbox: 12 arms ran, 10 HOLD — legs 8b and 8c are NOT-DEMONSTRATED on this single node (host-own target, loopback route). The suite is GREEN on what it can measure and SILENT on what it cannot; it is NOT a clearance to lift \`offenders\`."
else
  echo "cap-sandbox: ALL PROPERTIES HOLD (12 arms: legs 0-7, 8m, 8b, 8c, 9; leg 0 negative control, 7a/8a/8m-control positive controls, 9-control pre-fix arm)"
fi
