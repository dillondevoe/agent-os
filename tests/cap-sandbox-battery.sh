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
LEAKED="$(systemctl list-units --all --no-legend 'agent-os-cap-*' 2>/dev/null | wc -l)"
[ "$LEAKED" = 0 ] || fail "6: $LEAKED transient agent-os-cap-* unit(s) leaked (--collect not working)"
echo "cap-sandbox 6 OK  (transient units collected, none left behind)"

echo "cap-sandbox: ALL PROPERTIES HOLD"
