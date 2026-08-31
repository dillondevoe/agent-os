#!/usr/bin/env bash
# fetch-verified.sh — download a pinned artifact, or produce nothing.
#
# Replaces `curl ... | bash`. Two differences, both load-bearing:
#
#   1. THE BYTES ARE CHECKED BEFORE ANYTHING RUNS. The digest comes from
#      supply-chain/pins.txt, which is in git and reviewed like code.
#   2. IT NEVER EXECUTES ANYTHING. It writes a file and exits; the caller runs it.
#      A helper that fetched AND executed would be one `|| true` away from being the
#      pipe it replaced. Keeping execution at the call site keeps it visible there.
#
# Not `set -e`: the exit code IS the output, and it is three-valued.
#   0  verified — the file at <outfile> is byte-identical to the pin
#   1  MISMATCH — fetched successfully, digest differs. Nothing is left at <outfile>.
#   2  CANNOT-ASSESS — no pin, no tools, or the fetch failed. Also NOT a pass:
#      an instrument that could not read its input must never report clean.
#
#   fetch-verified.sh <name> <outfile>
#   fetch-verified.sh --record <name>     print the CURRENT digest (does not write pins.txt)
set -uo pipefail

MANIFEST="${AGOS_PIN_MANIFEST:-$(dirname "$0")/../supply-chain/pins.txt}"

die2() { echo "fetch-verified: CANNOT-ASSESS: $*" >&2; exit 2; }

lookup() {                      # name -> "sha url" on stdout, or empty
  awk -v n="$1" '$1 !~ /^#/ && $1 == n { print $2, $3; found=1; exit } END { exit !found }' "$MANIFEST"
}

[ -r "$MANIFEST" ] || die2 "manifest not readable: $MANIFEST"
command -v curl     >/dev/null 2>&1 || die2 "curl not found"
command -v sha256sum >/dev/null 2>&1 || die2 "sha256sum not found"

record=0
if [ "${1:-}" = "--record" ]; then record=1; shift; fi
name="${1:-}"
[ -n "$name" ] || die2 "usage: fetch-verified.sh [--record] <name> [outfile]"

entry="$(lookup "$name")" || die2 "no pin named '$name' in $MANIFEST — add one, do not fetch unpinned"
want="${entry%% *}"; url="${entry##* }"

tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
curl -fsSL --proto '=https,file' -o "$tmp" "$url" \
  || die2 "fetch failed: $url (an unreachable upstream is not a verified artifact)"
got="$(sha256sum "$tmp" | awk '{print $1}')"

if [ "$record" = 1 ]; then
  printf '%s  %s  %s\n' "$name" "$got" "$url"
  [ "$got" = "$want" ] && echo "fetch-verified: unchanged (pin already current)" >&2 \
                       || echo "fetch-verified: CHANGED — read the diff before re-recording" >&2
  exit 0
fi

out="${2:-}"
[ -n "$out" ] || die2 "usage: fetch-verified.sh <name> <outfile>"

if [ "$got" != "$want" ]; then
  echo "fetch-verified: MISMATCH for '$name'" >&2
  echo "  url:      $url" >&2
  echo "  expected: $want" >&2
  echo "  got:      $got" >&2
  echo "  Refusing to write. Upstream may have rotated OR the response may be hostile;" >&2
  echo "  from here those are the same event. Diff the bytes, then --record deliberately." >&2
  exit 1
fi

cat "$tmp" > "$out" || die2 "could not write $out"
echo "fetch-verified: $name OK ($want)"
exit 0
