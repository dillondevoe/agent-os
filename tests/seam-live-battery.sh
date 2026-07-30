#!/usr/bin/env bash
# seam-live-battery.sh — go-live END-TO-END property test for the wired invoke seam (Phase 2 · Step 7).
#
# Usage: seam-live-battery.sh <broker> <taint> <audit> <cap-invoke> <cap-bin-dir> <real-registry.json> <scratch>
# Exits 0 iff EVERY property holds. Run by the flake check (checks.<sys>.seam-live) and standalone.
#
# WHAT THIS PROVES that the Step-5 broker battery and the Step-7 cap battery do NOT:
#   * broker-battery.sh drives the broker through a SCRIPTED fake seam (never the real cap-invoke),
#     and its wired legs are all UNTRUSTED caps (net.fetch) — so ORIGIN_BY_CAP[capabilities.list]
#     = TRUSTED is untested end-to-end there.
#   * cap-battery.sh drives cap-invoke + the impl DIRECTLY (no broker), so the broker's provenance
#     decision for a real list is untested there.
# This battery wires the REAL broker to the REAL store-pinned cap-invoke DISPATCHER + the REAL
# patchShebangs'd capabilities.list impl (the exact artifacts broker.nix pins in production) and
# drives a real `tools/call capabilities.list` all the way through. It is the regression guard for
# go-live gate #1: a list must (a) return blessed content and (b) leave the session taint CLEAN.
# Drop `capabilities.list` from ORIGIN_BY_CAP (back to the unmapped UNTRUSTED default) and property
# 2 goes RED — exactly the spurious-taint hole the gate closes.
set -u

BROKER="${1:?path to bin/broker required}"
TAINT="${2:?path to bin/taint required}"
AUDIT="${3:?path to bin/audit required}"
CAPINVOKE="${4:?path to the store-pinned cap-invoke wrapper required}"
CAPBINDIR="${5:?path to the store-pinned cap bin dir required}"
REGREAL="${6:?path to the materialized real registry json required}"
SCRATCH="${7:?scratch dir required}"
PY="${PYTHON:-python3}"

fail() { echo "seam-live FAIL: $*" >&2; exit 1; }

# The broker inherits these and forwards them to the seam subprocess (bin/broker `_run` spawns the
# seam with NO env= override), so exporting them here is exactly how broker.nix pins them in prod.
export AGENT_OS_TAINT_DIR="$SCRATCH/taint"
export AGENT_OS_AUDIT_DIR="$SCRATCH/audit"
export AUDIT_BIN="$AUDIT"
export TAINT_BIN="$TAINT"
export AGENT_OS_REGISTRY="$REGREAL"
export AGENT_OS_INVOKE_SEAM="$CAPINVOKE"
export AGENT_OS_CAP_BIN_DIR="$CAPBINDIR"
mkdir -p "$AGENT_OS_TAINT_DIR" "$AGENT_OS_AUDIT_DIR"

# Bless a clean start session (the anti-laundering bit starts CLEAN; a TRUSTED cap must keep it so).
"$PY" "$TAINT" reset --confirm-human --break-glass >/dev/null 2>&1 || fail "could not bless a clean start session"

jf() { printf '%s' "$1" | "$PY" -c 'import sys,json
o=json.load(sys.stdin); v=eval(sys.argv[1])
sys.stdout.write("true" if v is True else "false" if v is False else "None" if v is None else str(v))' "$2"; }

taint_is() { local o; o="$("$PY" "$TAINT" status)" || fail "taint status errored"
  case "$o" in *"taint: $1"*) : ;; *) fail "taint expected '$1', got: $o";; esac; }

# Precondition: a fresh blessed session is clean (so property 2 measures the LIST's effect, nothing
# else). `taint status` prints lowercase `taint: clean` for the clean bit (uppercase `TAINTED` when set).
taint_is clean

# capabilities.list is T0 (ALLOW-AUTO): no confirm seam needed. Drive one real tools/call verdict.
V_LIST='{"ok":true,"method":"tools/call","id":1,"name":"capabilities.list","arguments":{}}'
OUT="$(printf '%s\n' "$V_LIST" | "$PY" "$BROKER" run)" || fail "broker run errored on capabilities.list"

# ── property 1: the wired seam RAN and returned blessed content through the DATA fence ──
[ "$(jf "$OUT" 'o["ok"]')" = "true" ]                              || fail "broker envelope ok!=true: $OUT"
[ "$(jf "$OUT" 'o["result"]["capability_ok"]')" = "true" ]         || fail "capability_ok!=true (seam/impl did not run clean): $OUT"
[ "$(jf "$OUT" 'o["result"]["content_type"]')" = "data" ]         || fail "list result not DATA-fenced: $OUT"
CONTENT="$(jf "$OUT" 'o["result"]["content"]')"                    || fail "no content field: $OUT"
[ -n "$CONTENT" ] && [ "$CONTENT" != "None" ]                     || fail "list returned empty content: $OUT"
# The impl lists the registry; capabilities.list is always present in its own output.
case "$CONTENT" in *capabilities.list*) : ;; *) fail "list content did not include capabilities.list: $CONTENT";; esac

# ── property 2 (GATE #1): a TRUSTED list leaves the session taint CLEAN (no spurious taint) ──
# This is the whole point of ORIGIN_BY_CAP[capabilities.list]=TRUSTED: the broker's provenance
# stage must NOT `taint set` on a list. If the mapping regresses, the unmapped UNTRUSTED default
# taints the session here (status flips to `taint: TAINTED`) and this assert fails.
taint_is clean

echo "seam-live: all properties hold (wired capabilities.list returns blessed content + leaves taint CLEAN)"
