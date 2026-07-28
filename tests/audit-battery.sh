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
#    Guarded against root: chmod does not bind uid 0, so this leg is only meaningful
#    as an unprivileged user (per feedback_guards_need_their_own_coverage).
if [ "$(id -u)" != 0 ]; then
  RO="$SCRATCH/ro/audit"; mkdir -p "$RO"; : > "$RO/audit.log"
  chmod 0444 "$RO/audit.log"; chmod 0555 "$RO"
  if AGENT_OS_AUDIT_DIR="$RO" bash -c "echo '{\"cap\":\"x\"}' | \"$PY\" \"$AUDIT\" append" >/dev/null 2>&1; then
    chmod -R u+w "$SCRATCH/ro"; fail "append SUCCEEDED on an unwritable log (no-log->no-execute violated)"
  fi
  chmod -R u+w "$SCRATCH/ro"
else
  echo "audit-battery: test-6 (unwritable-log) skipped under root — chmod is unenforced for uid 0" >&2
fi

# 7. fail-closed on malformed input.
if echo 'not json'  | "$PY" "$AUDIT" append >/dev/null 2>&1; then fail "append accepted non-JSON"; fi
if printf '[]'      | "$PY" "$AUDIT" append >/dev/null 2>&1; then fail "append accepted a non-object"; fi
if printf ''        | "$PY" "$AUDIT" append >/dev/null 2>&1; then fail "append accepted empty stdin"; fi

# 8. reserved fields (seq/prev/ts/hash) cannot be forged by the caller.
echo '{"cap":"x","seq":999,"prev":"deadbeef","ts":"1999","hash":"x"}' | "$PY" "$AUDIT" append >/dev/null \
  || fail "append with reserved fields exited non-zero"
"$PY" "$AUDIT" verify >/dev/null || fail "verify failed after a reserved-field record (forgery not stripped)"

# 9. torn-tail (FIX 2): an unterminated last line must be REFUSED, not chained onto.
#    Reproduces a crash between os.write and the newline landing by dropping the final byte.
TT="$SCRATCH/torn/audit"; mkdir -p "$TT"
( export AGENT_OS_AUDIT_DIR="$TT"
  for i in 1 2 3; do echo "{\"cap\":\"c\",\"n\":$i}" | "$PY" "$AUDIT" append >/dev/null || exit 7; done
  sz=$(wc -c < "$TT/audit.log"); truncate -s $((sz - 1)) "$TT/audit.log"   # drop final newline
  if echo '{"cap":"c","n":4}' | "$PY" "$AUDIT" append >/dev/null 2>&1; then exit 8; fi   # must refuse
) || fail "torn-tail was not refused (FIX 2 — silent chain corruption)"

# 10. tail-truncation is a KNOWN, documented gap: the chain ALONE cannot detect a
#     dropped tail, so verify PASSES on the valid prefix (Step-7 chattr +a + broker
#     head-anchoring are what close it; see the docstring corrected in FIX 3).
TR="$SCRATCH/trunc/audit"; mkdir -p "$TR"
( export AGENT_OS_AUDIT_DIR="$TR"
  for i in 1 2 3; do echo "{\"cap\":\"c\",\"n\":$i}" | "$PY" "$AUDIT" append >/dev/null || exit 7; done
  sed -i '3d' "$TR/audit.log"                         # drop the LAST record entirely
  "$PY" "$AUDIT" verify >/dev/null 2>&1 || exit 8     # a valid prefix still verifies OK
) || fail "verify should PASS on a truncated valid prefix (documents the tail-truncation gap)"

# 11. concurrency (flock): parallel appends must not interleave the seq/chain.
CC="$SCRATCH/conc/audit"; mkdir -p "$CC"
( export AGENT_OS_AUDIT_DIR="$CC"
  for i in $(seq 1 12); do echo "{\"cap\":\"c\",\"n\":$i}" | "$PY" "$AUDIT" append >/dev/null & done
  wait
  [ "$(grep -c . "$CC/audit.log")" = "12" ] || exit 8
  "$PY" "$AUDIT" verify >/dev/null 2>&1 || exit 9    # chain intact + seq dense 0..11
) || fail "concurrent appends dropped records or broke the chain (flock)"

# 12. durability (FIX 1 + fsync-both): a real ENOSPC needs a privileged loop-mount the
#     nix sandbox can't do, so inject the same POSIX property portably — monkeypatch os
#     to (a) prove a clean append fsyncs BOTH the file fd and the dir fd, and (b) prove a
#     SHORT write (partial count, no exception) fails closed instead of exiting 0.
DUR="$SCRATCH/dur/audit"; mkdir -p "$DUR"
"$PY" - "$AUDIT" "$DUR" <<'PYEOF' || fail "durability leg failed (FIX 1 short-write and/or fsync-both)"
import sys, os, io, stat, importlib.util, importlib.machinery
audit_path, adir = sys.argv[1], sys.argv[2]
os.environ["AGENT_OS_AUDIT_DIR"] = adir
# bin/audit is extensionless, so spec_from_file_location infers no loader — name one.
loader = importlib.machinery.SourceFileLoader("auditmod", audit_path)
spec = importlib.util.spec_from_loader("auditmod", loader)
m = importlib.util.module_from_spec(spec); loader.exec_module(m)

# (a) a clean append must fsync BOTH a regular file and a directory.
targets = []
real_fsync = os.fsync
def spy_fsync(fd):
    targets.append(stat.S_ISDIR(os.fstat(fd).st_mode)); return real_fsync(fd)
os.fsync = spy_fsync
sys.stdin = io.StringIO('{"cap":"file.read","n":1}')
rc = m.cmd_append([])
os.fsync = real_fsync
assert rc == 0, "clean append did not exit 0 (rc=%r)" % rc
assert (True in targets) and (False in targets), "must fsync BOTH file and dir, saw %r" % targets

# (b) a SHORT write (1 byte durable, rest silently dropped) must fail closed.
real_write = os.write
def short_write(fd, data):
    real_write(fd, data[:1]); return 1
os.write = short_write
sys.stdin = io.StringIO('{"cap":"file.read","n":2}')
rc2 = m.cmd_append([])
os.write = real_write
assert rc2 != 0, "short write exited 0 — no-log->no-execute breach (FIX 1)"
print("durability OK")
PYEOF

echo "audit-battery: ALL PROPERTIES HOLD"
