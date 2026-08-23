#!/usr/bin/env bash
# cap-sandbox-battery.sh — WP-S2 / GATE #5(a): prove the per-cap systemd fs-confinement is REAL.
#
# Usage (MUST run as root on a host with a live SYSTEM systemd):
#   sudo tests/cap-sandbox-battery.sh <bin/cap-invoke> <cap-bin-dir> <registry.json> \
#                                     <cap-sandbox.json> [systemd-run]
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
# Five properties, in the order that makes the result meaningful:
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
set -u

INVOKE="${1:?path to bin/cap-invoke required}"
CAPBIN="${2:?cap bin dir required}"
REGISTRY="${3:?registry.json required}"
POLICY="${4:?cap-sandbox.json required}"
SYSTEMD_RUN="${5:-$(command -v systemd-run || echo /usr/bin/systemd-run)}"
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

echo "cap-sandbox: ALL PROPERTIES HOLD"
