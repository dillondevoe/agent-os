#!/usr/bin/env bash
# agos-key-drift — is DECLARED STATE the whole of SSH access on this box?
#
# WHY THIS EXISTS. On 2026-08-31 the Dell was carrying a hand-written
# /root/.ssh/authorized_keys with an undeclared ed25519 key in it (mtime Aug 16).
# sshd reads BOTH %h/.ssh/authorized_keys and /etc/ssh/authorized_keys.d/%u, and only
# the second is Nix-managed — so a `nixos-rebuild switch` is green, the config is
# faithful, and root access is still wider than anything anyone declared. Nothing in
# the build could have seen it: this is a property of a RUNNING MACHINE, not of an
# evaluation, which is why it is a systemd unit and not a flake check.
#
# THE ORACLE IS THE BOX'S OWN DECLARED FILES, not a list re-typed here. It compares
# each mutable authorized_keys against /etc/ssh/authorized_keys.d/<user>, which is what
# Nix actually wrote. Re-spelling meshPubKeys in this script would put two halves of one
# rule in two languages with nothing asserting they agree — the parallel-surface trap
# this tree already carries scars from. There is no second spelling to drift.
#
# COMPARISON IS BY FINGERPRINT, NOT BY LINE. The same key differs textually between the
# two files all the time: a trailing comment, a `restrict,` option prefix, whitespace.
# A line-equality check would report drift on every box and be switched off within a week.
#
# PER-USER, NOT UNION. A key declared for `agent` and hand-added to `root` is precisely
# the escalation this is looking for; a union comparison would call it clean.
#
# EXIT CODES ARE THREE, AND THE THIRD IS THE POINT:
#   0  no undeclared key found
#   1  DRIFT — at least one undeclared fingerprint has SSH access
#   2  the instrument could not answer (unreadable file, unparseable key line)
# A check that cannot read its input must not report "clean". 2 is not 0.
set -uo pipefail

PREFIX="${AGOS_KEY_DRIFT_ROOT:-}"          # test seam: point the whole scan at a fixture
DECL_DIR="$PREFIX/etc/ssh/authorized_keys.d"
REPORT="${AGOS_KEY_DRIFT_REPORT:-$PREFIX/var/lib/agos-key-drift/report.txt}"

rc=0
out=""
files_examined=0
keys_examined=0
say() { out+="$1"$'\n'; echo "$1"; }

# Fingerprint every key in a file, one "SHA256:... comment" per line.
# An authorized_keys line may carry an options prefix; ssh-keygen -lf handles that, so
# each line is fingerprinted on its own rather than parsed by hand here.
fps() {
  local f="$1" line tmp fp
  [ -r "$f" ] || return 0
  tmp="$(mktemp)"
  while IFS= read -r line; do
    case "$line" in ''|'#'*) continue ;; esac
    printf '%s\n' "$line" > "$tmp"
    if fp="$(ssh-keygen -lf "$tmp" 2>/dev/null)"; then
      # "256 SHA256:xxx comment (ED25519)" -> "SHA256:xxx comment"
      printf '%s %s\n' "$(printf '%s' "$fp" | awk '{print $2}')" \
                       "$(printf '%s' "$fp" | awk '{$1="";$2="";sub(/ *\([^)]*\)$/,"");print}' | sed 's/^ *//')"
    else
      printf 'UNPARSEABLE %s\n' "$f"
    fi
  done < "$f"
  rm -f "$tmp"
}

check_user() {
  local user="$1" mutable="$2" declared="$DECL_DIR/$1"
  [ -e "$mutable" ] || return 0
  files_examined=$((files_examined+1))
  if [ ! -r "$mutable" ]; then
    say "CANNOT-ASSESS $user: $mutable exists but is unreadable"; rc=2; return 0
  fi
  local live decl
  live="$(fps "$mutable")"
  decl="$(fps "$declared")"
  if printf '%s' "$live$decl" | grep -q '^UNPARSEABLE'; then
    say "CANNOT-ASSESS $user: a key line did not parse"; rc=2
  fi
  local f c
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    case "$f" in UNPARSEABLE*) continue ;; esac
    c="${f%% *}"
    keys_examined=$((keys_examined+1))
    if printf '%s\n' "$decl" | awk '{print $1}' | grep -qxF "$c"; then
      say "ok        $user: $f (declared)"
    else
      say "UNDECLARED $user: $f  <- has SSH access, is in NO declared file"
      [ "$rc" = 2 ] || rc=1
    fi
  done <<< "$live"
}

say "agos-key-drift $(date -u +%Y-%m-%dT%H:%M:%SZ) host=$(uname -n)"
if [ ! -d "$DECL_DIR" ]; then
  say "CANNOT-ASSESS: no $DECL_DIR — nothing declares anything, so nothing can be compared"
  rc=2
else
  # WHICH ACCOUNTS. sshd reads %h/.ssh/authorized_keys for EVERY account with a home,
  # so the set to scan is the passwd database — not a /home/* glob. Measured on the Dell
  # 2026-09-02: 48 of 51 declared users have homes outside /root and /home/*, one of them
  # writable (`ollama` -> /var/lib/ollama). The incident key planted in such a home was
  # invisible to the glob and the scan still printed "RESULT clean". Same defect this tree
  # has now hit five times: a sound predicate handed the wrong SET.
  #
  # ONE code path for box and fixture. Reading passwd only when unprefixed would leave the
  # real enumeration untested — the battery writes an $PREFIX/etc/passwd and exercises
  # exactly the lines that run on the box.
  if [ -r "$PREFIX/etc/passwd" ]; then
    while IFS=: read -r u _ _ _ _ home _; do
      [ -n "$u" ] && [ -n "$home" ] || continue
      check_user "$u" "$PREFIX$home/.ssh/authorized_keys"
    done < "$PREFIX/etc/passwd"
  else
    say "CANNOT-ASSESS: no $PREFIX/etc/passwd — the set of accounts to scan is unknown"
    rc=2
  fi
fi

# THE POPULATION IS PART OF THE VERDICT. A clean result on a box with no mutable
# authorized_keys anywhere (the shipped posture since 2026-08-31) was byte-identical to a
# clean result on a box where every key checked out — and identical again to a scan whose
# enumeration silently returned nothing. A zero you can READ beats a zero you have to infer.
scanned=" [examined $files_examined mutable authorized_keys file(s), $keys_examined key(s)]"

case $rc in
  0) say "RESULT clean — declared state is the whole of SSH access$scanned" ;;
  1) say "RESULT DRIFT — undeclared key(s) above have access; declare them or remove them$scanned" ;;
  2) say "RESULT CANNOT-ASSESS — the scan is incomplete; this is NOT a clean result$scanned" ;;
esac

mkdir -p "$(dirname "$REPORT")" 2>/dev/null
printf '%s' "$out" > "$REPORT" 2>/dev/null || true
exit $rc
