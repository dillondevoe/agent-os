#!/usr/bin/env bash
# agent-shell-brain-precedence — the login shell must never auto-select the cloud brain,
# and must never exec a brain it has not PROVED can run.
#
# Two failures this arms, both found by Geist 2026-09-02 from Dillon's question "what
# happens if someone installs claude code on agent os":
#
#   1. SOVEREIGNTY. `command -v claude` alone promoted the cloud brain to primary. The
#      box's headline property — "by default nothing leaves the machine" — inverted on
#      the presence of a file, with no $BRAIN set and no per-turn consent, because the
#      local brain that owns the consent flow never started.
#   2. GETTY CRASH-LOOP. Cloud `claude` was exempt from the probe every local brain must
#      pass. On NixOS the native installer's dynamically-linked ELF may not exec at all
#      (no nix-ld in this config), so `command -v` can succeed on a binary that cannot
#      run — exec fails, login dies, getty autologins, forever.
#
# Every arm runs the REAL script in a sandboxed HOME with stub brains on PATH, because
# the defect lives in agent-shell's own selection branch: a test that reimplemented the
# precedence would pass on the exact bug this closes. The arm count is printed with the
# verdict so a future zero is readable rather than inferred.
set -uo pipefail
SHELL_BIN="$(cd "$(dirname "$0")/.." && pwd)/bin/agent-shell"
[ -x "$SHELL_BIN" ] || { echo "CANNOT-ASSESS: agent-shell not executable at $SHELL_BIN"; exit 2; }

PASS=0; FAIL=0; RAN=0

# Build a sandbox: fresh HOME, a stub for each named brain. A stub named with a "-" suffix
# is created NON-runnable (present on PATH, cannot exec) — the F3 shape.
mk() {
  SB="$(mktemp -d)"; mkdir -p "$SB/bin" "$SB/home"
  for spec in "$@"; do
    name="${spec%%:*}"; kind="${spec##*:}"
    case "$kind" in
      ok)      printf '#!/bin/sh\n[ "$1" = --check ] || [ "$1" = --version ] && exit 0\necho "BRAIN=%s"\n' "$name" > "$SB/bin/$name"; chmod +x "$SB/bin/$name" ;;
      nocheck) printf '#!/bin/sh\n[ "$1" = --check ] || [ "$1" = --version ] && exit 1\necho "BRAIN=%s"\n' "$name" > "$SB/bin/$name"; chmod +x "$SB/bin/$name" ;;
      noexec)  printf '#!/nonexistent/loader\n' > "$SB/bin/$name"; chmod +x "$SB/bin/$name" ;;
    esac
  done
}
run() {  # run() [VAR=val ...] -> stdout of agent-shell, brain marker included
  # Inherit the caller's PATH rather than hardcoding /usr/bin:/bin — under a nix build
  # sandbox those do not exist, and a run() that cannot find `bash` would fail every arm
  # for a reason that has nothing to do with brain precedence.
  env -i HOME="$SB/home" PATH="$SB/bin:$PATH" TERM=dumb AGENT_OS_NOANIM=1 \
      "$@" bash "$SHELL_BIN" </dev/null 2>&1
}
arm() {  # arm <name> <expected-marker|NONE> <output>
  RAN=$((RAN+1))
  local name="$1" want="$2" out="$3"
  if [ "$want" = NONE ]; then
    if printf '%s' "$out" | grep -q 'BRAIN=claude'; then
      echo "FAIL $name — cloud brain was selected"; FAIL=$((FAIL+1)); return
    fi
  else
    if ! printf '%s' "$out" | grep -q "$want"; then
      echo "FAIL $name — expected $want"; FAIL=$((FAIL+1)); return
    fi
  fi
  echo "PASS $name"; PASS=$((PASS+1))
}

# (ii) claude on PATH, $BRAIN unset -> the LOCAL brain is chosen. The sovereignty arm.
mk claude:ok agent-loop:ok
arm "auto/claude-present-local-wins"    "BRAIN=agent-loop" "$(run)"

# (i) claude on PATH but not runnable, $BRAIN unset -> local chain, not a dead exec.
mk claude:noexec agent-loop:ok
arm "auto/claude-unrunnable-local-wins" "BRAIN=agent-loop" "$(run)"

# claude the ONLY brain on PATH, $BRAIN unset -> must NOT be selected; falls to the REPL.
# Without this the arm above passes for the wrong reason (agent-loop simply ranked higher).
mk claude:ok
arm "auto/claude-alone-is-not-chosen"   "NONE"             "$(run)"

# (iv) BRAIN=claude + probe passes -> claude. The control arm: without it a script that
# refused the cloud brain unconditionally would pass every other arm here.
mk claude:ok agent-loop:ok
arm "pinned/claude-runnable-wins"       "BRAIN=claude"     "$(run BRAIN=claude)"

# (iii) BRAIN=claude + probe fails -> local chain, never the dead exec.
mk claude:noexec agent-loop:ok
arm "pinned/claude-unrunnable-falls"    "BRAIN=agent-loop" "$(run BRAIN=claude)"

# A pinned local brain that fails --check still falls through (pre-existing law, kept armed).
mk agent-loop:nocheck brain-ollama:ok
arm "pinned/local-failing-check-falls"  "BRAIN=brain-ollama" "$(run BRAIN=agent-loop)"

echo "--- ran $RAN arms: $PASS passed, $FAIL failed"
[ "$RAN" -eq 6 ] || { echo "CANNOT-ASSESS: expected 6 arms, ran $RAN"; exit 2; }
[ "$FAIL" -eq 0 ]
