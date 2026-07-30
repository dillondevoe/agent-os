#!/usr/bin/env bash
# cap-battery.sh — property tests for the Step-7 capability seam (Phase 2 · Step 7).
#
# Usage: cap-battery.sh <cap-invoke> <cap-capabilities-list> <real-registry.json> <scratch-dir>
# Exits 0 iff EVERY property holds. Run by the flake check (checks.<sys>.capabilities) and
# standalone. Each property maps to a threat-model/seam-contract clause; a failure names it.
#
# Under test:
#   * bin/cap-invoke              — the AGENT_OS_INVOKE_SEAM DISPATCHER (thin, fail-closed resolver).
#   * bin/cap-capabilities-list   — the T0 impl behind `capabilities.list`.
# The dispatcher runs against the REAL materialized registry (the production routing contract) for
# the live path, and a small SCRATCH registry + scripted fake impls for the adversarial legs (a
# path-escaping impl name, a crashing impl, a garbage-emitting impl, an origin-smuggling impl) —
# the only way to exercise the dispatcher's contract-enforcement boundary against a hostile impl.
set -u

INVOKE="${1:?path to bin/cap-invoke required}"
CAPLIST="${2:?path to bin/cap-capabilities-list required}"
REGREAL="${3:?path to the materialized real registry json required}"
SCRATCH="${4:?scratch dir required}"
PY="${PYTHON:-python3}"

fail() { echo "cap-battery FAIL: $*" >&2; exit 1; }

# Extract a value from a JSON line by python expression over `o`. Prints true/false/None/str;
# non-zero if invalid JSON or the path is absent (strict) — same helper shape as broker-battery.
jf() { printf '%s' "$1" | "$PY" -c 'import sys,json
o=json.load(sys.stdin); v=eval(sys.argv[1])
sys.stdout.write("true" if v is True else "false" if v is False else "None" if v is None else str(v))' "$2"; }

# ── build a scratch impl dir + a scratch registry with scripted fake impls ────
BIN="$SCRATCH/bin"
mkdir -p "$BIN"
cp "$CAPLIST" "$BIN/cap-capabilities-list"; chmod +x "$BIN/cap-capabilities-list"

# fake impl: valid object with an impl-reported origin + junk meta the dispatcher MUST strip.
cat > "$BIN/cap-echo-good" <<'PYEOF'
#!/usr/bin/env python3
import sys, json
sys.stdin.buffer.read()
sys.stdout.write(json.dumps({"ok": True, "content": "x",
                             "meta": {"key": "k", "origin": "TRUSTED", "evil": "z"},
                             "origin": "TRUSTED"}))
PYEOF
# fake impl: in-band decline (ok:false) with an error body — MUST be forwarded (flows to taint).
cat > "$BIN/cap-decline" <<'PYEOF'
#!/usr/bin/env python3
import sys, json
sys.stdin.buffer.read()
sys.stdout.write(json.dumps({"ok": False, "content": "boom", "meta": {}}))
PYEOF
# fake impl: exit 0 but non-JSON stdout — dispatcher must fail closed, never forward.
cat > "$BIN/cap-garbage" <<'PYEOF'
#!/usr/bin/env python3
import sys
sys.stdin.buffer.read()
sys.stdout.write("not json at all")
PYEOF
# fake impl: valid JSON but crashes (nonzero exit) — its stdout must NEVER be forwarded.
cat > "$BIN/cap-nonzero-json" <<'PYEOF'
#!/usr/bin/env python3
import sys, json
sys.stdin.buffer.read()
sys.stdout.write(json.dumps({"ok": True, "content": "leaked", "meta": {}}))
sys.exit(3)
PYEOF
# fake impl: valid JSON, valid content, but 'ok' is a truthy NON-bool (integer 1). The strict-type
# contract must REJECT it (fail closed) — never truthy-coerce it into a forwarded seam object. This
# is the {"ok":1} coercion hole (Python True==1==1.0); guards the isinstance(ok,bool) enforcement.
cat > "$BIN/cap-ok-int" <<'PYEOF'
#!/usr/bin/env python3
import sys, json
sys.stdin.buffer.read()
sys.stdout.write(json.dumps({"ok": 1, "content": "coerced", "meta": {}}))
PYEOF
# fake impl: hangs well past the per-impl wall-clock timeout. The dispatcher must kill+reap it and
# fail closed — its (post-sleep) stdout must NEVER be forwarded, and a hung impl must not wedge the
# single-flight broker (which does not itself timeout the invoke seam).
cat > "$BIN/cap-sleep" <<'PYEOF'
#!/usr/bin/env python3
import sys, time
sys.stdin.buffer.read()
time.sleep(3)
sys.stdout.write('{"ok": true, "content": "slept", "meta": {}}')
PYEOF
chmod +x "$BIN/cap-echo-good" "$BIN/cap-decline" "$BIN/cap-garbage" "$BIN/cap-nonzero-json" \
         "$BIN/cap-ok-int" "$BIN/cap-sleep"

# Pin every scratch impl's shebang to the ABSOLUTE python interpreter. The nix build sandbox
# (sandbox = true) has NO /usr/bin/env, so a `#!/usr/bin/env python3` impl execve()s ENOEXEC and
# the dispatcher reports it as an impl-spawn failure (exit 3) rather than exercising the contract
# under test — cell 1 alone would false-fail the whole check under a sandboxed `nix flake check`.
# The copied real impl (cap-capabilities-list) is a raw source path in this runCommand (nix does
# not patchShebangs it), so it needs the same pin. Harmless where /usr/bin/env exists (standalone).
PYBIN="$(command -v "$PY")"
for f in "$BIN"/*; do
  # sed -i.bak … && rm -f "$f.bak" is portable to BOTH GNU sed (bare -i) and BSD/macOS sed
  # (which requires a suffix arg after -i); the standalone runner may be on either.
  [ -f "$f" ] && sed -i.bak "1s|^#!.*|#!$PYBIN|" "$f" && rm -f "$f.bak"
done

# scratch registry: real capabilities.list + fake caps (incl. a path-escaping impl name).
TESTREG="$SCRATCH/testreg.json"
cat > "$TESTREG" <<'JSONEOF'
{
  "capabilities.list": {"tier": "T0", "impl": "cap-capabilities-list", "summary": "list", "args": {}, "argEnums": {}, "sandbox": {"network": false, "readOnlyPaths": [], "readWritePaths": [], "egressDeny": [], "egressAllow": []}},
  "echo.good":     {"tier": "T0", "impl": "cap-echo-good",     "summary": "echo", "args": {}, "argEnums": {}, "sandbox": {"network": false, "readOnlyPaths": [], "readWritePaths": [], "egressDeny": [], "egressAllow": []}},
  "echo.decline":  {"tier": "T0", "impl": "cap-decline",       "summary": "decl", "args": {}, "argEnums": {}, "sandbox": {"network": false, "readOnlyPaths": [], "readWritePaths": [], "egressDeny": [], "egressAllow": []}},
  "echo.garbage":  {"tier": "T0", "impl": "cap-garbage",       "summary": "junk", "args": {}, "argEnums": {}, "sandbox": {"network": false, "readOnlyPaths": [], "readWritePaths": [], "egressDeny": [], "egressAllow": []}},
  "echo.crash":    {"tier": "T0", "impl": "cap-nonzero-json",  "summary": "boom", "args": {}, "argEnums": {}, "sandbox": {"network": false, "readOnlyPaths": [], "readWritePaths": [], "egressDeny": [], "egressAllow": []}},
  "echo.okint":    {"tier": "T0", "impl": "cap-ok-int",        "summary": "okint","args": {}, "argEnums": {}, "sandbox": {"network": false, "readOnlyPaths": [], "readWritePaths": [], "egressDeny": [], "egressAllow": []}},
  "echo.sleep":    {"tier": "T0", "impl": "cap-sleep",         "summary": "sleep","args": {}, "argEnums": {}, "sandbox": {"network": false, "readOnlyPaths": [], "readWritePaths": [], "egressDeny": [], "egressAllow": []}},
  "escape.cap":    {"tier": "T0", "impl": "../cap-invoke",     "summary": "esc",  "args": {}, "argEnums": {}, "sandbox": {"network": false, "readOnlyPaths": [], "readWritePaths": [], "egressDeny": [], "egressAllow": []}}
}
JSONEOF

# run the dispatcher: disp <request> <registry> <cap-bin-dir> ; sets $OUT and $RC.
disp() { OUT="$(printf '%s' "$1" | env AGENT_OS_REGISTRY="$2" AGENT_OS_CAP_BIN_DIR="$3" "$PY" "$INVOKE" 2>/dev/null)"; RC=$?; }

# ── 1. live path: capabilities.list against the REAL registry ─────────────────
disp '{"capability":"capabilities.list","arguments":{}}' "$REGREAL" "$BIN"
[ "$RC" = 0 ] || fail "1: capabilities.list should exit 0, got $RC"
[ "$(jf "$OUT" 'o["ok"]')" = "true" ] || fail "1: capabilities.list ok should be true"
[ "$(jf "$OUT" 'o["meta"]')" = "{}" ] || fail "1: capabilities.list meta should be empty"

# 2. content enumerates the real caps with valid tiers, and leaks NO sandbox internals.
printf '%s' "$OUT" | "$PY" -c '
import sys, json
o = json.load(sys.stdin)
caps = json.loads(o["content"])["capabilities"]
names = {c["name"] for c in caps}
for want in ("capabilities.list", "file.read", "file.write", "mem.recall",
             "mem.remember", "net.fetch", "message.send"):
    assert want in names, "missing cap "+want
for c in caps:
    assert c["tier"] in ("T0", "T1", "T2"), "bad tier "+repr(c)
    assert set(c) == {"name", "tier", "summary"}, "leaked field "+repr(c)
assert "/var/lib" not in o["content"], "sandbox path leaked into content"
assert "readWritePaths" not in o["content"], "sandbox decl leaked into content"
' || fail "2: enumeration shape/leak check failed"

# ── 3. unknown capability -> deny (no impl run) ───────────────────────────────
disp '{"capability":"nope.nope","arguments":{}}' "$REGREAL" "$BIN"
[ "$RC" != 0 ] || fail "3: unknown capability must exit nonzero"
[ "$(jf "$OUT" 'o["ok"]')" = "false" ] || fail "3: unknown capability ok must be false"

# ── 4. malformed stdin -> fail closed ─────────────────────────────────────────
disp 'this is not json' "$REGREAL" "$BIN"
[ "$RC" != 0 ] || fail "4: malformed request must exit nonzero"

# ── 5. missing capability field -> fail closed ────────────────────────────────
disp '{"arguments":{}}' "$REGREAL" "$BIN"
[ "$RC" != 0 ] || fail "5: missing capability must exit nonzero"

# ── 6. arguments not an object -> fail closed ─────────────────────────────────
disp '{"capability":"capabilities.list","arguments":[1,2]}' "$REGREAL" "$BIN"
[ "$RC" != 0 ] || fail "6: non-object arguments must exit nonzero"

# ── 7. AGENT_OS_CAP_BIN_DIR unset -> fail closed (never PATH fallback) ─────────
OUT="$(printf '%s' '{"capability":"capabilities.list","arguments":{}}' | env -u AGENT_OS_CAP_BIN_DIR AGENT_OS_REGISTRY="$REGREAL" "$PY" "$INVOKE" 2>/dev/null)"; RC=$?
[ "$RC" != 0 ] || fail "7: unset cap-bin-dir must exit nonzero"

# ── 8. impl binary absent (empty bin dir) -> fail closed ──────────────────────
mkdir -p "$SCRATCH/emptybin"
disp '{"capability":"capabilities.list","arguments":{}}' "$REGREAL" "$SCRATCH/emptybin"
[ "$RC" != 0 ] || fail "8: absent impl binary must exit nonzero"

# ── 9. path-escaping impl name in the registry -> rejected, nothing runs ──────
disp '{"capability":"escape.cap","arguments":{}}' "$TESTREG" "$BIN"
[ "$RC" != 0 ] || fail "9: path-escaping impl name must exit nonzero"

# ── 10. generic impl forward + origin/extra-meta STRIPPED to identity ─────────
disp '{"capability":"echo.good","arguments":{}}' "$TESTREG" "$BIN"
[ "$RC" = 0 ] || fail "10: echo.good should exit 0, got $RC"
printf '%s' "$OUT" | "$PY" -c '
import sys, json
o = json.load(sys.stdin)
assert o == {"ok": True, "content": "x", "meta": {"key": "k"}}, "not normalized: "+repr(o)
' || fail "10: origin/extra-meta not stripped to {ok,content,meta:{key}}"

# ── 11. in-band decline (ok:false + error body) is FORWARDED (flows to taint) ─
disp '{"capability":"echo.decline","arguments":{}}' "$TESTREG" "$BIN"
[ "$RC" = 0 ] || fail "11: in-band decline must still exit 0 (content flows to taint)"
[ "$(jf "$OUT" 'o["ok"]')" = "false" ] || fail "11: decline ok must be false"
[ "$(jf "$OUT" 'o["content"]')" = "boom" ] || fail "11: decline error body must be forwarded"

# ── 12. impl emits garbage (exit 0, non-JSON) -> fail closed, not forwarded ───
disp '{"capability":"echo.garbage","arguments":{}}' "$TESTREG" "$BIN"
[ "$RC" != 0 ] || fail "12: garbage impl output must exit nonzero"
[ "$(jf "$OUT" 'o["content"]')" = "None" ] || fail "12: garbage must not leak into content"

# ── 13. impl valid JSON but nonzero exit -> stdout NEVER forwarded ────────────
disp '{"capability":"echo.crash","arguments":{}}' "$TESTREG" "$BIN"
[ "$RC" != 0 ] || fail "13: crashing impl must exit nonzero"
case "$OUT" in *leaked*) fail "13: crashed impl stdout was forwarded";; esac

# ── 14. strict bool: impl 'ok' is a truthy NON-bool (1) -> fail closed, never truthy-coerced ──
# {"ok":1} passed the old `ok in (True, False)` gate (Python True==1==1.0) and got bool()-coerced
# into a forwarded exit-0 seam object — the CF-1c/MCP coercion class. isinstance(ok,bool) denies it:
# the dispatcher must exit nonzero and forward NONE of the impl's content.
disp '{"capability":"echo.okint","arguments":{}}' "$TESTREG" "$BIN"
[ "$RC" != 0 ] || fail "14: non-bool ok (1) must exit nonzero (no truthy coercion)"
[ "$(jf "$OUT" 'o["content"]')" = "None" ] || fail "14: coerced-ok content must not be forwarded"
case "$OUT" in *coerced*) fail "14: non-bool-ok impl content leaked into output";; esac

# ── 15. per-impl wall-clock timeout: a hung impl is killed+reaped, its output withheld ────────
# AGENT_OS_CAP_TIMEOUT_S=1 vs a 3s-sleeping impl: the dispatcher must TimeoutExpired -> fail closed
# (exit nonzero), never let the child's post-sleep stdout be forwarded. A hung impl cannot wedge
# the single-flight broker (the broker does not itself timeout the invoke seam).
OUT="$(printf '%s' '{"capability":"echo.sleep","arguments":{}}' | env AGENT_OS_REGISTRY="$TESTREG" AGENT_OS_CAP_BIN_DIR="$BIN" AGENT_OS_CAP_TIMEOUT_S=1 "$PY" "$INVOKE" 2>/dev/null)"; RC=$?
[ "$RC" != 0 ] || fail "15: timed-out impl must exit nonzero"
[ "$(jf "$OUT" 'o["content"]')" = "None" ] || fail "15: timed-out impl must not leak content"
case "$OUT" in *slept*) fail "15: timed-out impl stdout was forwarded";; esac

echo "cap-battery: all properties hold"
exit 0
