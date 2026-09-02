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
  # A CURATED tool PATH with no `systemctl` in it, so the first-boot network branch is
  # unreachable in every arm on every machine. Without this the pinned arms below are
  # satisfiable through that branch on any box that has systemctl and a route to
  # registry.ollama.ai — which is exactly how they passed here and failed in CI on
  # 2026-09-02. Shadowing systemctl with a non-executable file does NOT work: a PATH search
  # skips a non-executable entry and keeps looking, so `command -v` still finds /usr/bin's.
  # The dir must be BUILT, not assumed: hardcoding /usr/bin:/bin fails every arm under a nix
  # sandbox for a reason unrelated to brain precedence, which is a red that reads like a
  # caught defect.
  mkdir -p "$SB/tools"
  for _t in bash sh env grep sed cat find wc tr timeout mktemp dirname mkdir chmod tail printf head sort; do
    _w="$(command -v "$_t" 2>/dev/null)" && ln -sf "$_w" "$SB/tools/$_t"
  done
  [ -x "$SB/tools/bash" ] || { echo "CANNOT-ASSESS: could not build a tool PATH (no bash)"; exit 2; }
  if PATH="$SB/tools" command -v systemctl >/dev/null 2>&1; then
    echo "CANNOT-ASSESS: systemctl reachable from the curated PATH — the first-boot branch is live"; exit 2
  fi
}
run() {  # run() [VAR=val ...] -> stdout of agent-shell, plus a __RC= completion token
  # PATH is the stub brains plus the curated tool dir mk() built — see the note there for
  # why it is built rather than inherited or hardcoded.
  #
  # The trailing __RC= token is SUBSTRATE for the deny arms below. A "this marker is
  # absent" assertion passes on empty output — so it passes hardest when the script
  # crashed, hung, or never ran at all. __RC= is emitted only if the run actually
  # terminated, and 124 (timeout) is rejected, so absence means "it decided otherwise",
  # not "there was nothing to read".
  local out rc
  out="$(timeout 120 env -i HOME="$SB/home" PATH="$SB/bin:$SB/tools" TERM=dumb AGENT_OS_NOANIM=1 \
      "$@" bash "$SHELL_BIN" </dev/null 2>&1)"; rc=$?
  printf '%s\n__RC=%s\n' "$out" "$rc"
}
arm() {  # arm <name> <marker|!marker> <output>   — "!X" asserts X was NOT exec'd
  RAN=$((RAN+1))
  local name="$1" want="$2" out="$3"
  # Substrate first, for BOTH forms: a run that did not terminate proves nothing, and a
  # deny arm would read its silence as a pass.
  if ! printf '%s' "$out" | grep -q '__RC='; then
    echo "FAIL $name — no completion token: agent-shell did not terminate"; FAIL=$((FAIL+1)); return
  fi
  if printf '%s' "$out" | grep -q '__RC=124'; then
    echo "FAIL $name — agent-shell timed out"; FAIL=$((FAIL+1)); return
  fi
  # 126/127 is a FAILED EXEC — the login shell died trying to launch a brain that cannot
  # run, which IS the getty crash-loop this suite exists to close. Without this the deny
  # arms pass on the pre-fix script: it exec'd the unrunnable stub, the shell died before
  # printing anything, and "the marker is absent" scored that as a refusal. Verified: the
  # pre-fix agent-shell takes exactly this route on the two pinned arms.
  if printf '%s' "$out" | grep -qE '__RC=(126|127)'; then
    echo "FAIL $name — agent-shell died on a failed exec (rc 126/127): the crash-loop"; FAIL=$((FAIL+1)); return
  fi
  case "$want" in
    !*)
      if printf '%s' "$out" | grep -q "${want#!}"; then
        echo "FAIL $name — ${want#!} was exec'd and must not have been"; FAIL=$((FAIL+1)); return
      fi ;;
    *)
      if ! printf '%s' "$out" | grep -q "$want"; then
        echo "FAIL $name — expected $want"; FAIL=$((FAIL+1)); return
      fi ;;
  esac
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
arm "auto/claude-alone-is-not-chosen"   "!BRAIN=claude"    "$(run)"

# (iv) BRAIN=claude + probe passes -> claude. The control arm: without it a script that
# refused the cloud brain unconditionally would pass every other arm here.
mk claude:ok agent-loop:ok
arm "pinned/claude-runnable-wins"       "BRAIN=claude"     "$(run BRAIN=claude)"

# (iii) BRAIN=claude + probe fails -> the LOCAL chain, in the precedence block.
#
# These two arms are the ones CI failed on 2026-09-02 while they passed 6/6 on the authoring
# machine, and both the failure and the fix are worth keeping written down. They expect a
# landing spot — "falls to the local chain". At the time the script only reached the local
# chain under `[ -z "$BRAIN" ]`, so a PINNED brain that failed its probe re-reached it solely
# through the first-boot recovery branch, gated on `command -v systemctl` AND a live tcp/443
# probe to registry.ollama.ai. This box has both; a nix build sandbox has neither. The arms
# were measuring an ambient network and reporting it as brain precedence — the same defect
# these tests exist to close, one layer out.
#
# The answer was not to weaken the arms to non-execution: that is satisfied by the login
# shell simply DYING on the failed exec, which is the crash-loop itself. It was to move the
# local chain to be the fallthrough for an empty commit (Geist's gate on #258), so the arms
# now hold in the sandbox for the reason they claim, and hold on a sealed box too.
mk claude:noexec agent-loop:ok
arm "pinned/claude-unrunnable-falls"    "BRAIN=agent-loop"   "$(run BRAIN=claude)"

# A pinned local brain that fails --check still falls through (pre-existing law, kept armed).
mk agent-loop:nocheck brain-ollama:ok
arm "pinned/local-failing-check-falls"  "BRAIN=brain-ollama" "$(run BRAIN=agent-loop)"

echo "--- ran $RAN arms: $PASS passed, $FAIL failed"
[ "$RAN" -eq 6 ] || { echo "CANNOT-ASSESS: expected 6 arms, ran $RAN"; exit 2; }
[ "$FAIL" -eq 0 ]
