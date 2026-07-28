#!/usr/bin/env bash
# audit-battery.sh — property tests for bin/audit (Phase 2 · Step 2).
#
# Usage: audit-battery.sh <path-to-audit-bin> <scratch-dir>
# Exits 0 iff EVERY property holds. Run by the flake check (checks.<sys>.audit-log)
# and standalone. Each property maps to a threat-model clause; a failure names it.
set -u

AUDIT="${1:?path to bin/audit required}"
SCRATCH="${2:?scratch dir required}"
PY="${PYTHON:-python3}"

fail() { echo "audit-battery FAIL: $*" >&2; exit 1; }
run()  { echo "$1" | "$PY" "$AUDIT" append >/dev/null; }   # $1 = json record

export AGENT_OS_AUDIT_DIR="$SCRATCH/audit"
mkdir -p "$AGENT_OS_AUDIT_DIR"
LOG="$AGENT_OS_AUDIT_DIR/audit.log"

# 1. append three records — each must exit 0, each must be exactly one line.
for i in 1 2 3; do
  run "{\"cap\":\"file.read\",\"n\":$i}" || fail "append $i exited non-zero"
done
[ "$(grep -c . "$LOG")" = "3" ] || fail "expected 3 log lines, got $(grep -c . "$LOG")"

# 2. a good chain verifies.
"$PY" "$AUDIT" verify >/dev/null || fail "verify on an intact log failed"

# 3. a hostile string value with an embedded newline stays ONE physical line
#    (json escaping — cannot forge a second audit record).
before=$(grep -c . "$LOG")
# %s does NOT process escapes in the argument, so json sees the two-char escape \n
# (a valid JSON escaped newline), which must round-trip to ONE physical line.
printf '%s\n' '{"cap":"x","note":"line1\nline2"}' | "$PY" "$AUDIT" append >/dev/null \
  || fail "append with escaped-newline string exited non-zero"
after=$(grep -c . "$LOG")
[ "$((after - before))" = "1" ] || fail "escaped newline produced $((after-before)) lines, expected 1"
"$PY" "$AUDIT" verify >/dev/null || fail "verify failed after escaped-newline record"
# A RAW control character makes the JSON invalid -> must fail closed (cannot inject a line).
if printf '{"cap":"x","note":"a\nb"}' | "$PY" "$AUDIT" append >/dev/null 2>&1; then
  fail "append accepted a raw control character (should fail-closed)"
fi

# 4. tamper-evidence: edit a PAST record -> verify MUST fail (non-zero).
cp "$LOG" "$LOG.bak"
sed -i '2s/"n":2/"n":999/' "$LOG"
if "$PY" "$AUDIT" verify >/dev/null 2>&1; then fail "verify PASSED on a tampered log"; fi
cp "$LOG.bak" "$LOG"
"$PY" "$AUDIT" verify >/dev/null || fail "verify failed after restoring the backup"

# 5. truncation-evidence: drop the last record -> verify still fine (valid prefix),
#    but dropping a MIDDLE record breaks the chain.
sed -i '2d' "$LOG"
if "$PY" "$AUDIT" verify >/dev/null 2>&1; then fail "verify PASSED after deleting a middle record"; fi
cp "$LOG.bak" "$LOG"

# 6. no-log -> no-execute: an unwritable log -> append exits non-zero.
RO="$SCRATCH/ro/audit"; mkdir -p "$RO"; : > "$RO/audit.log"
chmod 0444 "$RO/audit.log"; chmod 0555 "$RO"
if AGENT_OS_AUDIT_DIR="$RO" bash -c "echo '{\"cap\":\"x\"}' | \"$PY\" \"$AUDIT\" append" >/dev/null 2>&1; then
  chmod -R u+w "$SCRATCH/ro"; fail "append SUCCEEDED on an unwritable log (no-log->no-execute violated)"
fi
chmod -R u+w "$SCRATCH/ro"

# 7. fail-closed on malformed input.
if echo 'not json'  | "$PY" "$AUDIT" append >/dev/null 2>&1; then fail "append accepted non-JSON"; fi
if printf '[]'      | "$PY" "$AUDIT" append >/dev/null 2>&1; then fail "append accepted a non-object"; fi
if printf ''        | "$PY" "$AUDIT" append >/dev/null 2>&1; then fail "append accepted empty stdin"; fi

# 8. reserved fields (seq/prev/ts/hash) cannot be forged by the caller.
echo '{"cap":"x","seq":999,"prev":"deadbeef","ts":"1999","hash":"x"}' | "$PY" "$AUDIT" append >/dev/null \
  || fail "append with reserved fields exited non-zero"
"$PY" "$AUDIT" verify >/dev/null || fail "verify failed after a reserved-field record (forgery not stripped)"

echo "audit-battery: ALL PROPERTIES HOLD"
