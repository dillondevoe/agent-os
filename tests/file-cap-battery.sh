#!/usr/bin/env bash
# file-cap-battery.sh — property tests for the WP-S2 file.* capability IMPLS (Phase S · Step 7).
#
# Usage: file-cap-battery.sh <cap-file-read> <cap-file-write> <scratch-dir>
# Exits 0 iff EVERY property holds. Run by the flake check (checks.<sys>.file-cap) and standalone.
#
# Under test — DIRECT-INVOKE (bypassing the seam), the DESIGNED test path (mirrors mem-cap-battery.sh):
#   * bin/cap-file-write — T1 impl: writes ONE file under the workspace root, returns stored octets.
#   * bin/cap-file-read  — T0 impl: reads ONE file under the safe-read root, returns exact octets.
# The seam (cap-invoke) builds the impl env as EXACTLY {PATH, AGENT_OS_REGISTRY} — it STRIPS
# AGENT_OS_FILE_SAFE_ROOT / AGENT_OS_FILE_WORKSPACE_ROOT, so a through-the-seam call always uses
# the hardcoded /var/lib/agent-os/{safe-read,workspace} roots, which a non-root check-derivation
# cannot create. The env overrides exist SOLELY for this battery: point the impls at scratch roots
# and exercise the full write->read round-trip + the confinement/fail-closed legs the seam path
# structurally cannot reach in-sandbox.
set -u

READ="${1:?path to bin/cap-file-read required}"
WRITE="${2:?path to bin/cap-file-write required}"
SCRATCH="${3:?scratch dir required}"
PY="${PYTHON:-python3}"

# Canonicalize the scratch root (physical path, no double slashes / trailing slash) BEFORE
# deriving SAFE/WS. A caller-supplied root with a doubled separator (e.g. macOS TMPDIR ends in
# "/", and callers building "$TMPDIR/foo" get ".../T//foo") would otherwise fail the impls' own
# strict path-canonicalization check on every path under it — a real bug in the harness, not the
# impls under test (production paths never have this shape).
mkdir -p "$SCRATCH"
SCRATCH="$(cd "$SCRATCH" && pwd -P)"

SAFE="$SCRATCH/safe-read"    # cap-file-read's scratch root
WS="$SCRATCH/workspace"      # cap-file-write's scratch root
mkdir -p "$SAFE" "$WS"

fail() { echo "file-cap FAIL: $*" >&2; exit 1; }

# Extract a value from a JSON line by python expression over `o`.
jf() { printf '%s' "$1" | "$PY" -c 'import sys,json
o=json.load(sys.stdin); v=eval(sys.argv[1])
sys.stdout.write("true" if v is True else "false" if v is False else "None" if v is None else str(v))' "$2"; }

# Emit a well-formed seam request. args are key=value pairs, value taken verbatim as a STRING.
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

# Invoke an impl directly with a scratch root. Sets $OUT and $RC.
rd() { OUT="$(printf '%s' "$2" | env AGENT_OS_FILE_SAFE_ROOT="$1" "$PY" "$READ" 2>/dev/null)"; RC=$?; }
wr() { OUT="$(printf '%s' "$2" | env AGENT_OS_FILE_WORKSPACE_ROOT="$1" "$PY" "$WRITE" 2>/dev/null)"; RC=$?; }

# ── 1. write round-trip + byte-identity ────────────────────────────────────
C1='hello file world — no trailing newline here'
P1="$WS/notes.txt"
wr "$WS" "$(req file.write "path=$P1" "content=$C1")"
[ "$RC" = 0 ] || fail "1: write should exit 0, got $RC ($OUT)"
[ "$(jf "$OUT" 'o["ok"]')" = "true" ] || fail "1: write ok should be true ($OUT)"
[ "$(jf "$OUT" 'o["content"]')" = "$C1" ] || fail "1: returned content != input"
[ "$(jf "$OUT" 'o["meta"]["path"]')" = "$P1" ] || fail "1: meta.path != requested path"
DISK="$(cat "$P1")"
[ "$DISK" = "$C1" ] || fail "1: on-disk bytes != seam content"

# ── 2. read round-trip on a file planted directly under the safe root ─────
C2='second file — planted directly, not via write'
P2="$SAFE/plain.txt"
printf '%s' "$C2" > "$P2"
rd "$SAFE" "$(req file.read "path=$P2")"
[ "$RC" = 0 ] || fail "2: read should exit 0, got $RC ($OUT)"
[ "$(jf "$OUT" 'o["ok"]')" = "true" ] || fail "2: read ok should be true"
[ "$(jf "$OUT" 'o["content"]')" = "$C2" ] || fail "2: read content != planted content"
[ "$(jf "$OUT" 'o["meta"]["path"]')" = "$P2" ] || fail "2: meta.path != requested path"

# ── 3. write->read round-trip through the SAME root (read root = write root here) ──
C3='round trip through both caps'
P3="$SAFE/roundtrip.txt"
wr "$SAFE" "$(req file.write "path=$P3" "content=$C3")"
[ "$RC" = 0 ] || fail "3: write should exit 0, got $RC"
rd "$SAFE" "$(req file.read "path=$P3")"
[ "$RC" = 0 ] || fail "3: read-after-write should exit 0, got $RC"
[ "$(jf "$OUT" 'o["content"]')" = "$C3" ] || fail "3: read-after-write content mismatch"

# ── 4. write confinement fence: path outside the workspace root fails closed ──
OUTSIDE="$SCRATCH/outside.txt"
wr "$WS" "$(req file.write "path=$OUTSIDE" "content=x")"
[ "$RC" = 3 ] || fail "4: write outside root must exit 3, got $RC"
[ "$(jf "$OUT" 'o["ok"]')" = "false" ] || fail "4: write outside root ok must be false"
[ ! -e "$OUTSIDE" ] || fail "4: write outside root actually wrote a file"

# ── 5. read confinement fence: path outside the safe-read root fails closed ──
OUTSIDE2="$SCRATCH/secret.txt"
printf 'do not read me' > "$OUTSIDE2"
rd "$SAFE" "$(req file.read "path=$OUTSIDE2")"
[ "$RC" = 3 ] || fail "5: read outside root must exit 3, got $RC"
[ "$(jf "$OUT" 'o["ok"]')" = "false" ] || fail "5: read outside root ok must be false"

# ── 6. non-canonical path fence: '..' / relative / trailing-slash reject ──
for p in "$WS/../escape.txt" "relative.txt" "$WS/trailing/"; do
  wr "$WS" "$(req file.write "path=$p" "content=x")"
  [ "$RC" = 3 ] || fail "6: non-canonical write path '$p' must exit 3, got $RC"
  rd "$SAFE" "$(req file.read "path=$p")"
  [ "$RC" = 3 ] || fail "6: non-canonical read path '$p' must exit 3, got $RC"
done

# ── 7. read on a missing file fails closed (not a crash, not ok:true) ─────
rd "$SAFE" "$(req file.read "path=$SAFE/nonexistent.txt")"
[ "$RC" = 3 ] || fail "7: read of missing file must exit 3, got $RC"
[ "$(jf "$OUT" 'o["ok"]')" = "false" ] || fail "7: read of missing file ok must be false"

# ── 8. read refuses a symlink even if it resolves inside the root ─────────
TARGET="$SAFE/real.txt"
LINK="$SAFE/link.txt"
printf 'real content' > "$TARGET"
ln -sf "$TARGET" "$LINK"
rd "$SAFE" "$(req file.read "path=$LINK")"
[ "$RC" = 3 ] || fail "8: read of a symlink must exit 3 (rejected), got $RC"
rm -f "$LINK"

# ── 9. write refuses to clobber an existing symlink target ────────────────
TARGET2="$WS/real2.txt"
LINK2="$WS/link2.txt"
printf 'original' > "$TARGET2"
ln -sf "$TARGET2" "$LINK2"
wr "$WS" "$(req file.write "path=$LINK2" "content=clobber")"
[ "$RC" = 3 ] || fail "9: write over a symlink must exit 3 (rejected), got $RC"
[ "$(cat "$TARGET2")" = "original" ] || fail "9: symlink write leaked through to the real target"
rm -f "$LINK2"

# ── 10. content-type / arg-schema fence: malformed args fail closed ───────
OUT="$(printf '%s' '{"capability":"file.write","arguments":{"path":"'"$WS"'/x.txt","content":123}}' \
       | env AGENT_OS_FILE_WORKSPACE_ROOT="$WS" "$PY" "$WRITE" 2>/dev/null)"; RC=$?
[ "$RC" = 3 ] || fail "10: non-string content must exit 3, got $RC"
OUT="$(printf '%s' '{"capability":"file.read","arguments":{"path":123}}' \
       | env AGENT_OS_FILE_SAFE_ROOT="$SAFE" "$PY" "$READ" 2>/dev/null)"; RC=$?
[ "$RC" = 3 ] || fail "10: non-string path (read) must exit 3, got $RC"

echo "file-cap: all properties hold"
exit 0
