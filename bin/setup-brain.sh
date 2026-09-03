#!/usr/bin/env bash
# setup-brain.sh — first-boot brain installer for Agent OS (Phase 1).
# The imperative half of the brain: model WEIGHTS (`ollama pull`) + the cloud CLI
# (Claude Code, not in nixpkgs). The ollama DAEMON is declarative (modules/brain.nix)
# because NixOS's /usr is immutable and the upstream curl|sh installer can't work.
set -euo pipefail

usage() {
  cat <<'USAGE'
setup-brain.sh — provision the Agent OS brain(s).

  bin/setup-brain.sh                 install the local floor (pull the default model)
  bin/setup-brain.sh --model TAG     pull a specific ollama model tag
  bin/setup-brain.sh --cloud         ALSO install the cloud brain (Claude Code)
  bin/setup-brain.sh --cloud-only    install only the cloud brain (skip the model pull)
                                     OPEN PROFILE ONLY: the sealed profile drops the
                                     installer's fetch by design, and installing the
                                     cloud brain does NOT make it the login brain —
                                     that takes an explicit BRAIN=claude.
  bin/setup-brain.sh -h | --help     this help

Env:
  BRAIN_MODEL   model tag to pull (default: $OLLAMA_MODEL, else qwen3.5:9b —
                the ~6.6GB dense 9B = v0.1 default brain. There is NO small dense
                tier above it in this generation: Qwen3.5 has no 14B, so the old
                `qwen2.5:14b` judgment-lane advice is dead. If you want more
                headroom the next real step up is a Qwen3.6 MoE/dense
                (e.g. qwen3.6:27b), which wants ~17GB and its own hardware tier.)
  OLLAMA_HOST   ollama endpoint  (default: http://127.0.0.1:11434)
USAGE
}

MODEL="${BRAIN_MODEL:-${OLLAMA_MODEL:-qwen3.5:9b}}"
OLLAMA_HOST="${OLLAMA_HOST:-http://127.0.0.1:11434}"
DO_LOCAL=1; DO_CLOUD=0

while [ $# -gt 0 ]; do
  case "$1" in
    --model)      MODEL="${2:?--model needs a tag}"; shift 2 ;;
    --cloud)      DO_CLOUD=1; shift ;;
    --cloud-only) DO_CLOUD=1; DO_LOCAL=0; shift ;;
    --local-only) DO_LOCAL=1; DO_CLOUD=0; shift ;;
    -h|--help)    usage; exit 0 ;;
    *) echo "setup-brain: unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

say()  { printf '\033[38;5;79m[setup-brain]\033[0m %s\n' "$*"; }
warn() { printf '\033[38;5;179m[setup-brain]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[38;5;196m[setup-brain]\033[0m %s\n' "$*" >&2; exit 1; }

# --- local floor: daemon (declarative) is assumed present; pull the weights here ----
if [ "$DO_LOCAL" = 1 ]; then
  command -v ollama >/dev/null 2>&1 || die \
"ollama not on PATH. The daemon is declarative on Agent OS: ensure modules/brain.nix
             (services.ollama.enable) is imported, run 'sudo nixos-rebuild switch', then re-run."

  say "waiting for the ollama daemon at ${OLLAMA_HOST} ..."
  reachable=0
  for _ in $(seq 1 30); do
    if curl -fsS -o /dev/null --max-time 3 "${OLLAMA_HOST}/api/version" 2>/dev/null; then
      reachable=1; break
    fi
    sleep 1
  done
  [ "$reachable" = 1 ] || die \
    "ollama unreachable at ${OLLAMA_HOST} after 30s — check 'systemctl status ollama'."

  say "pulling model '${MODEL}' (idempotent — skips if already present) ..."
  ollama pull "$MODEL"

  # Prove the floor end-to-end with the shim's own preflight, pinned to this model.
  if command -v brain-ollama >/dev/null 2>&1; then
    if OLLAMA_HOST="$OLLAMA_HOST" OLLAMA_MODEL="$MODEL" brain-ollama --check; then
      say "local floor OK: '${MODEL}' reachable, brain-ollama --check passed."
    else
      warn "model pulled but brain-ollama --check failed — inspect 'ollama list'."
    fi
  fi
fi

# --- cloud brain: Claude Code (not in nixpkgs; installs into $HOME) -----------------
if [ "$DO_CLOUD" = 1 ]; then
  if command -v claude >/dev/null 2>&1; then
    say "cloud brain already installed ($(command -v claude)) — skipping."
  else
    command -v curl >/dev/null 2>&1 || die "curl not found — cannot fetch the Claude Code installer."
    say "installing Claude Code (cloud brain) into \$HOME/.local/bin ..."
    # The native installer targets $HOME (writable) — not the immutable /usr the ollama
    # daemon-installer needs — so unlike ollama, the cloud CLI *can* be fetched and run here.
    #
    # It is NOT piped into a shell. `curl ... | bash` executes whatever the socket produced,
    # including a truncated transfer — bash runs the first half of a script whose second half
    # never arrived — and there is no point at which the bytes could have been rejected.
    # fetch-verified.sh checks them against supply-chain/pins.txt FIRST and writes nothing on
    # a mismatch, so this call has two outcomes: the pinned installer, or a hard stop.
    #
    # A mismatch here is not automatically an attack; upstream rotates the script. It is also
    # not automatically a rotation. Read the diff before re-recording the pin — see pins.txt.
    HERE="$(cd "$(dirname "$0")" && pwd)"
    INST="$(mktemp)"; trap 'rm -f "$INST"' EXIT
    "$HERE/fetch-verified.sh" claude-code-install "$INST" \
      || die "Claude Code installer failed verification (see above) — refusing to run it."
    bash "$INST" || die "Claude Code install failed."
    if command -v claude >/dev/null 2>&1; then
      say "Claude Code installed — run 'claude' once to log in (interactive)."
    else
      warn "installer finished but 'claude' isn't on PATH yet — open a new shell (adds ~/.local/bin)."
    fi
  fi
fi

# --- how the shell will choose on next login ----------------------------------------
say "done. On next login agent-shell picks a brain by precedence:"
printf '   1. $BRAIN                 (explicit override, if set; probed before use)\n'
printf '   2. local `agent-loop`     tool-calling loop -> %s @ %s\n' "$MODEL" "$OLLAMA_HOST"
printf '   3. local `brain-ollama`   chat floor\n'
printf '   4. mem REPL               (always works, zero deps)\n'
printf '\n'
printf '   The cloud brain is NEVER auto-selected, installed or not. Having `claude` on\n'
printf '   PATH does not send anything off this machine; it only makes the local brain\n'
printf '   able to call out when YOU say yes. To hand the login shell to the cloud, set\n'
printf '   BRAIN=claude deliberately.\n'
if [ "$DO_LOCAL" = 1 ] && [ "$DO_CLOUD" = 0 ]; then
  say "sovereign: no cloud brain installed — this box talks with NO internet on '${MODEL}'."
fi
