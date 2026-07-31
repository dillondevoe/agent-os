#!/usr/bin/env bash
# mem-cap-battery.sh — property tests for the A2 mem.* capability IMPLS (Phase 2 · Step 7 / A2).
#
# Usage: mem-cap-battery.sh <cap-mem-remember> <cap-mem-recall> <scratch-dir>
# Exits 0 iff EVERY property holds. Run by the flake check (checks.<sys>.mem-cap) and standalone.
# Each property maps to a seam-contract / PIN clause named in the impls; a failure names it.
#
# Under test — DIRECT-INVOKE (bypassing the seam), the DESIGNED test path:
#   * bin/cap-mem-remember — T1 impl: writes ONE session-namespace entry, returns stored octets.
#   * bin/cap-mem-recall   — T0 impl: fuzzy MANY-hit read; returns per-entry exact octets.
# The seam (cap-invoke) builds the impl env as EXACTLY {PATH, AGENT_OS_REGISTRY} — it STRIPS
# AGENT_OS_MEM_ROOT, so a through-the-seam write always lands at the hardcoded /var/lib/agent-os/mem,
# which a non-root check-derivation cannot create. AGENT_OS_MEM_ROOT exists SOLELY for this battery
# (cap-mem-remember:41 says so): point the impls at a scratch root and exercise the full
# remember->recall round-trip + the security legs the seam path structurally cannot reach in-sandbox.
# The seam's env-strip + dispatch of the READ side is proven separately in tests/seam-live-battery.sh.
#
# FORWARD-OBLIGATION (Rabbot ruling 2026-07-31, item-9): the full THROUGH-THE-WALL write->read E2E
# (remember lands bytes at the real /var/lib/agent-os/mem via the wired seam, recall reads them back)
# can only live in a nixosTest VM (real root, real /var, boot-wired seam) — a check-derivation cannot
# write /var. That VM E2E is tracked as an additive forward-obligation on task-279's go-live checklist
# (same cadence as the `taint boot --key-prefix` deferral); it is deliberately NOT built this pass and
# does NOT gate the A2 PR. What THIS battery proves is everything the impls actually do, end to end.
set -u

REMEMBER="${1:?path to bin/cap-mem-remember required}"
RECALL="${2:?path to bin/cap-mem-recall required}"
SCRATCH="${3:?scratch dir required}"
PY="${PYTHON:-python3}"

MEM="$SCRATCH/mem"          # primary scratch root for the round-trip + security legs
EMPTY="$SCRATCH/empty-root" # a never-created root for the missing-root leg
mkdir -p "$MEM"

fail() { echo "mem-cap FAIL: $*" >&2; exit 1; }

# Extract a value from a JSON line by python expression over `o` (same helper shape as cap-battery).
jf() { printf '%s' "$1" | "$PY" -c 'import sys,json
o=json.load(sys.stdin); v=eval(sys.argv[1])
sys.stdout.write("true" if v is True else "false" if v is False else "None" if v is None else str(v))' "$2"; }

# Emit a well-formed seam request. args are key=value pairs; the value is taken verbatim as a STRING
# (partition on the FIRST "=", so a content value may itself contain "="). Building the JSON in python
# keeps arbitrary content (spaces, unicode, interior newlines) correctly escaped.
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

# Invoke an impl directly with a scratch MEM_ROOT. $1=root, $2=request json. Sets $OUT and $RC.
rem() { OUT="$(printf '%s' "$2" | env AGENT_OS_MEM_ROOT="$1" "$PY" "$REMEMBER" 2>/dev/null)"; RC=$?; }
rec() { OUT="$(printf '%s' "$2" | env AGENT_OS_MEM_ROOT="$1" "$PY" "$RECALL"   2>/dev/null)"; RC=$?; }

# ── 1. remember round-trip + PIN-2 byte-identity (the integrity hinge) ────────
# Write one entry; assert the returned key is boot-shaped (session.<ns>.<16hex>.md), the on-disk
# file bytes EQUAL the exact seam content octets (NO frontmatter, NO trailing newline), and the
# returned `content` equals both (return-bytes == file-bytes == input). Geist's mandated round-trip.
C1='hello memory world — no trailing newline here'
rem "$MEM" "$(req mem.remember "namespace=notes" "content=$C1")"
[ "$RC" = 0 ] || fail "1: remember should exit 0, got $RC ($OUT)"
[ "$(jf "$OUT" 'o["ok"]')" = "true" ] || fail "1: remember ok should be true ($OUT)"
printf '%s' "$OUT" | MEM="$MEM" C1="$C1" "$PY" -c '
import sys, os, json, hashlib
o = json.load(sys.stdin)
c1 = os.environ["C1"]; mem = os.environ["MEM"]
key = o["meta"]["key"]
h16 = hashlib.sha256(c1.encode("utf-8")).hexdigest()[:16]
assert key == "session.notes.%s.md" % h16, "bad key %r (want session.notes.%s.md)" % (key, h16)
assert o["content"] == c1, "returned content != input"
path = os.path.join(mem, "session", "notes.%s.md" % h16)
disk = open(path, "rb").read()
assert disk == c1.encode("utf-8"), "PIN-2 broken: disk octets != seam octets"
assert len(disk) == len(c1.encode("utf-8")), "PIN-2 broken: length differs"
assert disk[-1:] != b"\n", "PIN-2 broken: writer appended a trailing newline"
assert hashlib.sha256(disk).hexdigest()[:16] == h16, "slug hash != content hash"
' || fail "1: byte-identity / key-shape (PIN-2 integrity hinge) failed"

# ── 2. content-addressed idempotency: same (ns,content) -> same slug, one file ─
rem "$MEM" "$(req mem.remember "namespace=notes" "content=$C1")"
[ "$RC" = 0 ] || fail "2: re-remember should exit 0, got $RC"
MEM="$MEM" "$PY" -c '
import os, sys, hashlib
mem = os.environ["MEM"]
h16 = hashlib.sha256("hello memory world — no trailing newline here".encode("utf-8")).hexdigest()[:16]
d = os.path.join(mem, "session")
same = [n for n in os.listdir(d) if n == "notes.%s.md" % h16]
assert len(same) == 1, "idempotency broken: %r" % same
' || fail "2: content-addressed remember is not idempotent"

# ── 3. recall round-trip + PIN-A per-entry exact octets + hash binding ────────
# Recall the entry just written; the model-facing content and broker-facing meta.entries are built
# from the SAME hit list. Each entry's `content` is the exact stored octets, and sha256(content)[:16]
# equals the 16-hex in its own key slug (the per-entry binding the broker recalls against — PIN A).
rec "$MEM" "$(req mem.recall "namespace=notes" "query=memory world")"
[ "$RC" = 0 ] || fail "3: recall should exit 0, got $RC ($OUT)"
[ "$(jf "$OUT" 'o["ok"]')" = "true" ] || fail "3: recall ok should be true"
printf '%s' "$OUT" | C1="$C1" "$PY" -c '
import sys, os, json, hashlib
o = json.load(sys.stdin); c1 = os.environ["C1"]
entries = o["meta"]["entries"]
assert isinstance(entries, list) and len(entries) == 1, "want exactly 1 entry, got %r" % entries
e = entries[0]
assert set(e) == {"key", "content"}, "entry leaked fields: %r" % e
assert e["content"] == c1, "PIN-A: entry content != stored octets"
# model-facing content mirrors meta.entries exactly (fail-closed consistency)
assert json.loads(o["content"]) == {"entries": entries}, "content vs meta.entries mismatch"
# per-entry hash binding: sha256(this entry content) == the 16-hex in this entry key
h16 = e["key"].rsplit(".", 2)[-2]
assert hashlib.sha256(e["content"].encode("utf-8")).hexdigest()[:16] == h16, "PIN-A hash unbound"
' || fail "3: recall round-trip / PIN-A per-entry octets+hash failed"

# ── 4. multi-hit scoring + ordering is deterministic (score desc) ────────────
# Three entries with 3/2/1 query-token overlap must rank strictly by score descending. Content-address
# keys are hash-unpredictable, so we assert order by the (known) content strings, not by key.
rem "$MEM" "$(req mem.remember "namespace=rank" "content=alpha beta gamma delta")" ; [ "$RC" = 0 ] || fail "4: seed A"
rem "$MEM" "$(req mem.remember "namespace=rank" "content=alpha beta")"             ; [ "$RC" = 0 ] || fail "4: seed B"
rem "$MEM" "$(req mem.remember "namespace=rank" "content=alpha")"                  ; [ "$RC" = 0 ] || fail "4: seed C"
rec "$MEM" "$(req mem.recall "namespace=rank" "query=alpha beta gamma")"
[ "$RC" = 0 ] || fail "4: rank recall exit $RC"
printf '%s' "$OUT" | "$PY" -c '
import sys, json
o = json.load(sys.stdin)
got = [e["content"] for e in o["meta"]["entries"]]
want = ["alpha beta gamma delta", "alpha beta", "alpha"]
assert got == want, "ranking not score-desc: %r" % got
' || fail "4: multi-hit ordering is not score-descending/deterministic"

# ── 5. MAX_HITS ceiling (8): 9 equally-scoring entries -> at most 8 returned ──
i=0; while [ "$i" -lt 9 ]; do
  rem "$MEM" "$(req mem.remember "namespace=cap" "content=common token entry $i")"
  [ "$RC" = 0 ] || fail "5: seed $i failed"
  i=$((i + 1))
done
rec "$MEM" "$(req mem.recall "namespace=cap" "query=common")"
[ "$RC" = 0 ] || fail "5: cap recall exit $RC"
printf '%s' "$OUT" | "$PY" -c '
import sys, json
o = json.load(sys.stdin)
n = len(o["meta"]["entries"])
assert n == 8, "MAX_HITS ceiling not enforced: got %d entries (want 8)" % n
' || fail "5: MAX_HITS multi-hit ceiling not enforced"

# ── 6. empty query = list mode: all namespace entries returned, key-ranked ────
rem "$MEM" "$(req mem.remember "namespace=listmode" "content=first list entry")"  ; [ "$RC" = 0 ] || fail "6: seed 1"
rem "$MEM" "$(req mem.remember "namespace=listmode" "content=second list entry")" ; [ "$RC" = 0 ] || fail "6: seed 2"
rec "$MEM" "$(req mem.recall "namespace=listmode" "query=")"
[ "$RC" = 0 ] || fail "6: listmode recall exit $RC"
printf '%s' "$OUT" | "$PY" -c '
import sys, json
o = json.load(sys.stdin)
entries = o["meta"]["entries"]
assert len(entries) == 2, "list mode should return both, got %d" % len(entries)
keys = [e["key"] for e in entries]
assert keys == sorted(keys), "list mode not key-ranked: %r" % keys
' || fail "6: empty-query list mode failed"

# ── 7. cross-namespace isolation + prefix non-collision (last-dot parse) ──────
# ns "a" and ns "a.b" both live under session/; recall "a" must NOT return the "a.b" entry (the impl
# parses on the LAST dot, so a.b.<hash>.md has ns_part "a.b" != "a"). Guards the namespace-prefix hole.
rem "$MEM" "$(req mem.remember "namespace=a" "content=namespace a payload")"    ; [ "$RC" = 0 ] || fail "7: seed a"
rem "$MEM" "$(req mem.remember "namespace=a.b" "content=namespace a.b payload")" ; [ "$RC" = 0 ] || fail "7: seed a.b"
rec "$MEM" "$(req mem.recall "namespace=a" "query=payload")"
[ "$RC" = 0 ] || fail "7: ns-a recall exit $RC"
printf '%s' "$OUT" | "$PY" -c '
import sys, json
o = json.load(sys.stdin)
cs = [e["content"] for e in o["meta"]["entries"]]
assert cs == ["namespace a payload"], "prefix collision / cross-ns leak: %r" % cs
' || fail "7: namespace prefix collision — 'a' leaked 'a.b'"

# ── 8. recall on an unknown namespace = well-formed EMPTY (valid, not error) ──
rec "$MEM" "$(req mem.recall "namespace=nonesuch" "query=whatever")"
[ "$RC" = 0 ] || fail "8: empty recall must exit 0 (empty is valid), got $RC"
[ "$(jf "$OUT" 'o["ok"]')" = "true" ] || fail "8: empty recall ok must be true"
[ "$(jf "$OUT" 'len(o["meta"]["entries"])')" = "0" ] || fail "8: unknown-ns recall must be empty"

# ── 9. recall against a MISSING root = empty ok (nothing remembered yet) ──────
rec "$EMPTY" "$(req mem.recall "namespace=notes" "query=memory")"
[ "$RC" = 0 ] || fail "9: recall on missing root must exit 0"
[ "$(jf "$OUT" 'o["ok"]')" = "true" ] || fail "9: recall on missing root ok must be true"
[ "$(jf "$OUT" 'len(o["meta"]["entries"])')" = "0" ] || fail "9: missing-root recall must be empty"

# ── 10. key-grammar fence: illegal namespaces fail closed, NOTHING written ────
# The impl re-checks the namespace (dumb twin of the broker's _NS_RE) so a contract slip can't smuggle
# a path segment. Each of these must exit 3, ok:false, and create NO file.
before="$(find "$MEM/session" -type f | wc -l)"
for ns in "a/b" "../x" "-lead" ".lead" "" "$(printf 'x%.0s' $(seq 1 65))"; do
  OUT="$(printf '%s' "{\"capability\":\"mem.remember\",\"arguments\":{\"namespace\":\"$ns\",\"content\":\"x\"}}" \
         | env AGENT_OS_MEM_ROOT="$MEM" "$PY" "$REMEMBER" 2>/dev/null)"; RC=$?
  [ "$RC" = 3 ] || fail "10: illegal namespace '$ns' must exit 3, got $RC"
  [ "$(jf "$OUT" 'o["ok"]')" = "false" ] || fail "10: illegal namespace '$ns' ok must be false"
done
after="$(find "$MEM/session" -type f | wc -l)"
[ "$before" = "$after" ] || fail "10: an illegal-namespace remember wrote a file ($before -> $after)"

# ── 11. content-type fence: non-string / non-object args fail closed ──────────
for bad in \
  '{"capability":"mem.remember","arguments":{"namespace":"x","content":123}}' \
  '{"capability":"mem.remember","arguments":{"namespace":"x"}}' \
  '{"capability":"mem.remember","arguments":[1,2]}' \
  '{"capability":"mem.remember","arguments":{"namespace":"x","content":null}}'; do
  OUT="$(printf '%s' "$bad" | env AGENT_OS_MEM_ROOT="$MEM" "$PY" "$REMEMBER" 2>/dev/null)"; RC=$?
  [ "$RC" = 3 ] || fail "11: malformed remember must exit 3 ($bad -> $RC)"
  [ "$(jf "$OUT" 'o["ok"]')" = "false" ] || fail "11: malformed remember ok must be false ($bad)"
done
# recall likewise fail-closes on a non-string query / missing namespace
OUT="$(printf '%s' '{"capability":"mem.recall","arguments":{"namespace":"x","query":5}}' \
       | env AGENT_OS_MEM_ROOT="$MEM" "$PY" "$RECALL" 2>/dev/null)"; RC=$?
[ "$RC" = 3 ] || fail "11: recall non-string query must exit 3, got $RC"

# ── 12. recall fail-closed DROPS: oversized, non-utf8, illegal-slug files ─────
# Plant hostile files DIRECTLY (bypassing remember). recall must DROP each (never release, never
# half-taint) yet still succeed (ok:true) — drop-not-release is the fail-closed posture (a dropped
# entry cannot launder). Distinct namespaces keep these isolated from the round-trip legs above.
plant="$MEM/session"; mkdir -p "$plant"
hex16="0123456789abcdef"
# (a) oversized: > MAX_ENTRY_BYTES (262144)
"$PY" -c 'import sys; sys.stdout.buffer.write(b"A"*300000)' > "$plant/big.$hex16.md"
# (b) non-utf8 bytes
printf '\xff\xfe\x00bad' > "$plant/bad.$hex16.md"
# (c) illegal slug shapes (not <ns>.<16hex>.md) — must simply never match
printf 'x' > "$plant/notanentry.md"
printf 'x' > "$plant/nothex.zzzzzzzzzzzzzzzz.md"
for ns in big bad; do
  rec "$MEM" "$(req mem.recall "namespace=$ns" "query=")"
  [ "$RC" = 0 ] || fail "12: recall over planted '$ns' must exit 0 (fail-closed drop), got $RC"
  [ "$(jf "$OUT" 'o["ok"]')" = "true" ] || fail "12: recall over planted '$ns' ok must be true"
  [ "$(jf "$OUT" 'len(o["meta"]["entries"])')" = "0" ] || fail "12: planted '$ns' file was NOT dropped"
done
# the illegal-slug files are not parseable as <ns>.<16hex>.md -> never a hit under any real namespace
rec "$MEM" "$(req mem.recall "namespace=notanentry" "query=")"
[ "$(jf "$OUT" 'len(o["meta"]["entries"])')" = "0" ] || fail "12: illegal-slug file surfaced as a hit"

echo "mem-cap: all properties hold"
exit 0
