#!/usr/bin/env bash
# Battery for flake-input-provenance-contract.py — ITS CONTROL ARMS WERE A DOCSTRING.
#
# The contract itself is wired into flake-check.yml and runs on every push. Its control arms were
# three `--lock-json` invocations listed in the module docstring under the line:
#
#     Control arms (each MUST fail — if one does not, that check is not a check)
#
# A MUST, in prose, executed by nobody. The contract could regress into a checker that returns 0
# for every input — the single worst failure available to it, and the one the arms exist to
# exclude — and CI would stay green, because the only thing CI ran was the healthy case.
#
# That is the same shape the contract is ABOUT: "a check that degrades to a no-op when its input
# is absent is the bug it was written to catch", four lines above the arms it did not run. Written
# 2026-08-27, straight after adding a control by hand for the root-key fix and noticing the arm I
# had just typed had nowhere to live.
#
# Every arm is either MUST-FAIL or MUST-PASS, and both kinds are present on purpose: a battery of
# only MUST-FAILs is satisfied by a checker that rejects everything.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
C="$HERE/flake-input-provenance-contract.py"
fails=0
must_fail() { if python3 "$C" --lock-json "$2" >/dev/null 2>&1; then
    fails=$((fails+1)); echo "FAIL: [$1] was accepted — this check is not a check"; fi; }
must_pass() { if ! python3 "$C" --lock-json "$2" >/dev/null 2>&1; then
    fails=$((fails+1)); echo "FAIL: [$1] was rejected — a healthy lock must pass"; fi; }

# --- the three arms the docstring named -------------------------------------------------------
must_fail "third-party input"      '{"nodes":{"root":{},"evil":{"locked":{"type":"github","owner":"someone","repo":"somelib"}}}}'
must_fail "fork NAMED nixpkgs"     '{"nodes":{"root":{},"nixpkgs":{"locked":{"type":"github","owner":"a-fork","repo":"nixpkgs"}}}}'
must_fail "zero non-root nodes"    '{"nodes":{"root":{}}}'

# --- arms Geist ran by hand at the #163 gate, now written down ---------------------------------
must_fail "tarball url"            '{"nodes":{"root":{},"t":{"locked":{"type":"tarball","url":"https://example.invalid/x.tar.gz"}}}}'
must_fail "node with NO locked"    '{"nodes":{"root":{},"x":{}}}'
must_fail "empty object"           '{}'

# --- CONTROL: the checker must be able to say yes ----------------------------------------------
# Without this, every arm above is satisfied by `return 1`.
must_pass "healthy nixpkgs-only"   '{"nodes":{"root":{},"nixpkgs":{"locked":{"type":"github","owner":"NixOS","repo":"nixpkgs"}}}}'

# --- the root-key fix (Geist P3, #163 gate) ----------------------------------------------------
# `lock["root"]` names the root node; this file used to filter the LITERAL "root". If a lock ever
# names it otherwise, the literal walks the real root as though it were an input — and the root
# carries no `locked`, so it trips the "no locked key" arm and turns a HEALTHY lock red with a
# message about provenance. Verified against the pre-fix code before the fix landed: it printed
# `FAIL: input 'top' is <no locked source>`.
must_pass "root named by lock[root]" '{"root":"top","nodes":{"top":{},"nixpkgs":{"locked":{"type":"github","owner":"NixOS","repo":"nixpkgs"}}}}'
# CONTROL for the arm above: same non-literal root, but with a genuinely bad input beside it.
# Without this, the arm would also pass if the fix made the walker filter EVERYTHING — blind and
# green, which is the exact vacuity the zero-nodes arm exists to catch one level down.
must_fail "non-literal root, bad input" '{"root":"top","nodes":{"top":{},"evil":{"locked":{"type":"github","owner":"someone","repo":"somelib"}}}}'

# --- the real tree ------------------------------------------------------------------------------
if ! python3 "$C" >/dev/null 2>&1; then
    fails=$((fails+1)); echo "FAIL: the repo's own flake.lock does not satisfy the contract"; fi

if [ "$fails" -eq 0 ]; then echo "flake-input-provenance battery: all 10 arms pass"; else
    echo "flake-input-provenance battery: $fails FAILING"; fi
exit "$fails"
