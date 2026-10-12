#!/usr/bin/env bash
# tests/seal-check-window-battery.sh — the DECISION TABLE of seal-check's setup-window variant
# (modules/seal-check.nix; docs/design/first-login-and-setup-window.md §2.5), run on the REAL rendered
# script with its four tools replaced by fakes. No VM, no root: the flake check `setup-window` runs it.
#
#   seal-check-window-battery.sh <window script> <sealed-by-build script> <nft> <systemctl> <status tool> <ip>
#
# The four tool arguments are the exact store paths the scripts were rendered with. The battery
# rewrites them, and the two /run paths, into a scratch directory, and REFUSES TO RUN if a rewrite
# did not take (a script still holding a real tool path would test the build machine, not the table).
#
# The fakes answer from files in $SC: `table` and `set` are what nft prints (absent = nft fails),
# `status` is what the window program's `status` prints (absent = it fails), `window-active` and
# `nftables-failed` are what systemctl says. The texts are the formats a booted box prints
# (tests/seal-wait.nix prints both in D1, and runs the same script against the kernel).
set -u
[ $# = 6 ] || { echo "usage: $0 <window script> <sealed-by-build script> <nft> <systemctl> <status tool> <ip>" >&2; exit 2; }
WIN="$1"; SBB="$2"; NFT="$3"; SCTL="$4"; STATUS="$5"; IP="$6"
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
SC="$W/state"; mkdir -p "$W/bin" "$SC"; export SC

cat > "$W/bin/nft" <<'EOF'
#!/bin/sh
[ ! -e "$SC/hang" ] || sleep 60
case "$*" in
  "list table inet agentos_egress") [ -f "$SC/table" ] || exit 1; cat "$SC/table" ;;
  "list set inet agentos_egress setup_uid") [ -f "$SC/set" ] || exit 1; cat "$SC/set" ;;
  *) echo "fake nft: unexpected: $*" >> "$SC/calls"; exit 1 ;;
esac
EOF
cat > "$W/bin/systemctl" <<'EOF'
#!/bin/sh
case "$*" in
  "is-failed --quiet nftables.service") [ -e "$SC/nftables-failed" ] ;;
  "is-active --quiet agentos-setup-window.service") [ -e "$SC/window-active" ] ;;
  *) echo "systemctl $*" >> "$SC/calls" ;;
esac
EOF
cat > "$W/bin/status" <<'EOF'
#!/bin/sh
[ "$*" = status ] || { echo "fake status: unexpected: $*" >> "$SC/calls"; exit 2; }
[ -f "$SC/status" ] || exit 1
cat "$SC/status"
EOF
cat > "$W/bin/ip" <<'EOF'
#!/bin/sh
echo "ip $*" >> "$SC/calls"
EOF
chmod +x "$W/bin/"*

rewrite() {   # <script> <out>
  sed -e "s|$NFT|$W/bin/nft|g" -e "s|$SCTL|$W/bin/systemctl|g" -e "s|$STATUS|$W/bin/status|g" -e "s|$IP|$W/bin/ip|g" \
      -e "s|/run/agentos-unsealed|$W/unsealed|g" -e "s|/run/agentos-sealed-ok|$W/sealed-ok|g" "$1" > "$2"
  for real in "$NFT" "$SCTL" "$IP" /run/agentos-unsealed /run/agentos-sealed-ok; do
    if grep -qF -- "$real" "$2"; then echo "battery: $1 still names $real after the rewrite; refusing to run" >&2; exit 1; fi
  done
  grep -qF -- "$W/bin/nft" "$2" && grep -qF -- "$W/bin/systemctl" "$2" && grep -qF -- "$W/sealed-ok" "$2" || {
    echo "battery: the rewrite of $1 did not take (tool paths not found in it)" >&2; exit 1; }
}
rewrite "$WIN" "$W/window-check"
rewrite "$SBB" "$W/sbb-check"
grep -qF -- "$W/bin/status" "$W/window-check" || { echo "battery: the window script does not call the status tool" >&2; exit 1; }
if grep -qF -- "$STATUS" "$W/window-check"; then echo "battery: the window script still names the real status tool" >&2; exit 1; fi

TABLE_OK='table inet agentos_egress {
	set setup_uid {
		typeof meta skuid
	}

	chain output {
		type filter hook output priority filter; policy drop;
		oifname != "lo" meta skuid 351 meta skuid != @setup_uid drop
		ct state established,related accept
		oifname "lo" accept
		meta nfproto ipv4 meta skuid @setup_uid udp dport 53 accept
		meta nfproto ipv4 meta skuid @setup_uid tcp dport { 53, 443 } accept
	}
}'
SET_NONE='table inet agentos_egress {
	set setup_uid {
		typeof meta skuid
	}
}'
set_with() { printf 'table inet agentos_egress {\n\tset setup_uid {\n\t\ttypeof meta skuid\n\t\telements = { %s }\n\t}\n}\n' "$1"; }
ST_SEALED='{"reason": "complete", "ruleset": "same", "sealed": "valid", "window": "valid", "remaining": 100, "boots": 1}'
ST_SEALED_NOWIN='{"reason": "budget", "ruleset": "same", "sealed": "valid", "window": "absent"}'
ST_SEALED_REBUILT='{"reason": "complete", "ruleset": "changed", "sealed": "valid", "window": "absent"}'
ST_OPEN='{"boots": 1, "remaining": 6000, "sealed": "absent", "window": "valid"}'
ST_NOBOOKS='{"sealed": "absent", "window": "absent"}'
ST_BADBOOKS='{"sealed": "absent", "window": "malformed"}'
ST_MALFORMED='{"sealed": "malformed", "window": "valid"}'

pass=0
state() {   # <table text|-> <set text|-> <status text|-> <window-active 0|1> <nftables-failed 0|1>
  rm -rf "$SC"; mkdir -p "$SC"
  [ "$1" = - ] || printf '%s\n' "$1" > "$SC/table"
  [ "$2" = - ] || printf '%s\n' "$2" > "$SC/set"
  [ "$3" = - ] || printf '%s\n' "$3" > "$SC/status"
  [ "$4" = 1 ] && : > "$SC/window-active"
  [ "$5" = 1 ] && : > "$SC/nftables-failed"
  return 0
}
expect() {  # <script> <name> <want rc> <want token 0|1> <want: text the banner must hold, or "" for no banner>
  script="$1"; name="$2"
  rm -f "$W/unsealed"
  rc=0; bash "$script" >/dev/null 2>&1 || rc=$?
  tok=0; [ -e "$W/sealed-ok" ] && tok=1
  ok=1
  [ "$rc" = "$3" ] || ok=0
  [ "$tok" = "$4" ] || ok=0
  if [ -n "$5" ]; then
    grep -qF -- "$5" "$W/unsealed" 2>/dev/null || ok=0
    grep -qF 'EGRESS SEAL DOWN' "$W/unsealed" 2>/dev/null || ok=0
    if [ "$script" = "$W/window-check" ]; then grep -qF 'Repair console: Ctrl+Alt+F3' "$W/unsealed" 2>/dev/null || ok=0; fi
    grep -q '^systemctl mask --runtime --now NetworkManager.service' "$SC/calls" 2>/dev/null || ok=0     # the sealed response
  else
    [ ! -e "$W/unsealed" ] || ok=0
    [ ! -s "$SC/calls" ] || ok=0                                                                          # a passing run touches nothing
  fi
  if [ "$ok" = 1 ]; then pass=$((pass+1)); echo "  ok  $name"; else
    echo "FAIL  $name: rc=$rc (want $3) token=$tok (want $4) banner: $(cat "$W/unsealed" 2>/dev/null | tail -1) calls: $(cat "$SC/calls" 2>/dev/null | tr '\n' ';')" >&2
    exit 1
  fi
}
C="$W/window-check"

# (c) sealed and the set is empty: the ONLY state that writes the token.
rm -f "$W/sealed-ok"
state "$TABLE_OK" "$SET_NONE" "$ST_SEALED" 1 0;        expect "$C" "c   sealed, set empty: the token" 0 1 ""
state "$TABLE_OK" "$SET_NONE" "$ST_SEALED_NOWIN" 0 0;  expect "$C" "c   sealed, no books, window unit not running: the token" 0 1 ""
# a rebuild changed the ruleset since the seal: still sealed, the token is written, and it is LOGGED.
state "$TABLE_OK" "$SET_NONE" "$ST_SEALED_REBUILT" 0 0; expect "$C" "c   sealed under another ruleset (a rebuild): the token, not a failure" 0 1 ""
bash "$C" 2>/dev/null | grep -q 'sealed-generation-changed' || { echo "FAIL  a changed generation must be logged" >&2; exit 1; }
state "$TABLE_OK" "$SET_NONE" "$ST_SEALED" 1 0
if bash "$C" 2>/dev/null | grep -q 'sealed-generation-changed'; then echo "FAIL  an unchanged generation was logged as changed" >&2; exit 1; fi
pass=$((pass+1)); echo "  ok  c   the changed generation is logged, the unchanged one is not"
# (c) violated: sealed but the set holds the uid. The token from the run before must be GONE.
state "$TABLE_OK" "$(set_with 351)" "$ST_SEALED" 1 0;  expect "$C" "c   sealed but the set is not empty: DOWN, and the old token removed" 1 0 "sealed, but the setup window's set is not empty"
# A token from an earlier run does not outlive a run that does not prove (c), even one that passes.
state "$TABLE_OK" "$SET_NONE" "$ST_SEALED" 1 0;        expect "$C" "c   sealed, set empty again: the token" 0 1 ""
state "$TABLE_OK" "$SET_NONE" "$ST_NOBOOKS" 0 0;       expect "$C" "e   then no marker (state removed): accepted, and the OLD token is gone" 0 0 ""
# (d) the open window.
state "$TABLE_OK" "$(set_with 351)" "$ST_OPEN" 1 0;    expect "$C" "d   open window with books and a running unit: accepted, no token" 0 0 ""
state "$TABLE_OK" "$(set_with 351)" "$ST_OPEN" 0 0;    expect "$C" "d   set open, window unit not running: DOWN" 1 0 "the window unit is not running"
state "$TABLE_OK" "$(set_with 351)" "$ST_NOBOOKS" 1 0; expect "$C" "d   set open, no books: DOWN" 1 0 "no valid books"
state "$TABLE_OK" "$(set_with 351)" "$ST_BADBOOKS" 1 0; expect "$C" "d   set open, malformed books: DOWN" 1 0 "no valid books"
# (e) no marker, set empty: sealed-or-down. Never a token, whatever the unit and the books say.
state "$TABLE_OK" "$SET_NONE" "$ST_NOBOOKS" 0 0;       expect "$C" "e   no marker, window unit never ran, set empty: accepted, no token" 0 0 ""
state "$TABLE_OK" "$SET_NONE" "$ST_OPEN" 1 0;          expect "$C" "e   no marker, books say open, set empty (closing): accepted, no token" 0 0 ""
state "$TABLE_OK" "$SET_NONE" "$ST_BADBOOKS" 0 0;      expect "$C" "e   no marker, malformed books, set empty: accepted, no token" 0 0 ""
# the set holds anything but the fetcher's uid, sealed or not.
for elems in "351, 1000" "1000" "0" "3510" "35" "351-352"; do
  state "$TABLE_OK" "$(set_with "$elems")" "$ST_OPEN" 1 0;   expect "$C" "    set = { $elems }, open: DOWN" 1 0 "something other than the fetcher's uid"
  state "$TABLE_OK" "$(set_with "$elems")" "$ST_SEALED" 1 0; expect "$C" "    set = { $elems }, sealed: DOWN" 1 0 "something other than the fetcher's uid"
done
# the marker.
state "$TABLE_OK" "$SET_NONE" "$ST_MALFORMED" 1 0;     expect "$C" "    malformed marker: DOWN" 1 0 "the sealed marker is malformed"
state "$TABLE_OK" "$SET_NONE" - 1 0;                   expect "$C" "    the state cannot be read: DOWN" 1 0 "state cannot be read"
state "$TABLE_OK" "$SET_NONE" '{}' 1 0;                expect "$C" "    a status with no verdict: DOWN" 1 0 "the sealed marker is malformed"
# (a) the wall.
state - "$SET_NONE" "$ST_SEALED" 1 0;                  expect "$C" "a   no table: DOWN" 1 0 "the egress table is not loaded"
state "$TABLE_OK" "$SET_NONE" "$ST_SEALED" 1 1;        expect "$C" "a   nftables.service failed: DOWN" 1 0 "nftables failed"
state "$TABLE_OK" - "$ST_SEALED" 1 0;                  expect "$C" "    the set cannot be listed: DOWN" 1 0 "set cannot be read"
# (b) the loaded ruleset is not the reviewed one.
no_guard="$(printf '%s\n' "$TABLE_OK" | grep -v '!= @setup_uid drop')"
one_accept="$(printf '%s\n' "$TABLE_OK" | grep -v 'udp dport 53 accept')"
extra_rule="$(printf '%s\n' "$TABLE_OK" | sed 's|^\t\toifname "lo" accept$|&\n\t\tmeta skuid @setup_uid tcp dport 80 accept|')"
other_guard="$(printf '%s\n' "$TABLE_OK" | sed 's|meta skuid 351 meta skuid != @setup_uid drop|meta skuid 352 meta skuid != @setup_uid drop|')"
no_set="$(printf '%s\n' "$TABLE_OK" | grep -v 'set setup_uid {')"
# three references but only ONE accept (the other turned into a drop), and four references with
# exactly two accepts: each of the two counts has to be the one that catches its fixture.
accept_to_drop="$(printf '%s\n' "$TABLE_OK" | sed 's|@setup_uid udp dport 53 accept|@setup_uid udp dport 53 drop|')"
extra_counter="$(printf '%s\n' "$TABLE_OK" | sed 's|^\t\toifname "lo" accept$|&\n\t\tmeta skuid @setup_uid counter|')"
# the right SHAPE (one set, three references, two accepts, the guard) with a wider grant.
wide_ports="$(printf '%s\n' "$TABLE_OK" | sed 's|@setup_uid tcp dport { 53, 443 } accept|@setup_uid tcp dport 0-65535 accept|')"
with_80="$(printf '%s\n' "$TABLE_OK" | sed 's|@setup_uid tcp dport { 53, 443 } accept|@setup_uid tcp dport { 53, 80, 443 } accept|')"
no_v4_pin="$(printf '%s\n' "$TABLE_OK" | sed 's|meta nfproto ipv4 meta skuid @setup_uid udp dport 53 accept|meta skuid @setup_uid udp dport 53 accept|')"
# a guard that still names the uid and the set but is narrowed to one interface.
narrow_guard="$(printf '%s\n' "$TABLE_OK" | sed 's|oifname != "lo" meta skuid 351 meta skuid != @setup_uid drop|oifname "eth0" meta skuid 351 meta skuid != @setup_uid drop|')"
for f in "$accept_to_drop" "$extra_counter" "$wide_ports" "$with_80" "$no_v4_pin" "$narrow_guard"; do
  [ "$f" != "$TABLE_OK" ] || { echo "battery: a (b) fixture is the reviewed table itself" >&2; exit 1; }
done
[ "$extra_rule" != "$TABLE_OK" ] && [ "$other_guard" != "$TABLE_OK" ] || { echo "battery: a (b) fixture is the reviewed table itself" >&2; exit 1; }
state "$no_guard" "$SET_NONE" "$ST_SEALED" 1 0;        expect "$C" "b   the guard is missing: DOWN" 1 0 "not the reviewed one"
state "$one_accept" "$SET_NONE" "$ST_SEALED" 1 0;      expect "$C" "b   one accept is missing: DOWN" 1 0 "not the reviewed one"
state "$extra_rule" "$SET_NONE" "$ST_SEALED" 1 0;      expect "$C" "b   a fourth rule refers to the set: DOWN" 1 0 "not the reviewed one"
state "$other_guard" "$SET_NONE" "$ST_SEALED" 1 0;     expect "$C" "b   the guard names another uid: DOWN" 1 0 "not the reviewed one"
state "$no_set" "$SET_NONE" "$ST_SEALED" 1 0;          expect "$C" "b   the set is not declared: DOWN" 1 0 "not the reviewed one"
state "$accept_to_drop" "$SET_NONE" "$ST_SEALED" 1 0;  expect "$C" "b   three references but one accept: DOWN" 1 0 "not the reviewed one"
state "$extra_counter" "$SET_NONE" "$ST_SEALED" 1 0;   expect "$C" "b   four references, two accepts: DOWN" 1 0 "not the reviewed one"
state "$wide_ports" "$SET_NONE" "$ST_SEALED" 1 0;      expect "$C" "b   the right shape, every TCP port: DOWN" 1 0 "not the reviewed one"
state "$with_80" "$SET_NONE" "$ST_SEALED" 1 0;         expect "$C" "b   the right shape, port 80 added: DOWN" 1 0 "not the reviewed one"
state "$no_v4_pin" "$SET_NONE" "$ST_SEALED" 1 0;       expect "$C" "b   the right shape, the v4 pin gone: DOWN" 1 0 "not the reviewed one"
state "$narrow_guard" "$SET_NONE" "$ST_SEALED" 1 0;    expect "$C" "b   the guard narrowed to one interface: DOWN" 1 0 "not the reviewed one"
# recoverable: after a DOWN, the healthy state passes again and the banner is gone.
state "$TABLE_OK" "$SET_NONE" "$ST_SEALED" 1 0;        expect "$C" "    recovers: the healthy state after a DOWN" 0 1 ""

# A healthy sealed box keeps its token THROUGH a run (the check runs again at every start of tty1's
# getty): nothing removes it before the verdict. Only enforce_offline and the "not sealed" exit do.
# The token is removed by the EXIT trap on every way out that did not prove (c), and "proved" is set
# in exactly one place: on the line that writes the token.
[ "$(grep -c "^trap '\[ \"\$proved\" = 1 \] || rm -f \"\$OK\"' EXIT$" "$C")" = 1 ] || { echo "FAIL  the window script must remove an unproved token in an EXIT trap" >&2; exit 1; }
[ "$(grep -c 'proved=1' "$C")" = 1 ] && grep -qx '    : > "$OK" && proved=1' "$C" || { echo "FAIL  proved=1 must be set only where the token is written" >&2; exit 1; }
[ "$(grep -n '^proved=0$' "$C" | cut -d: -f1)" -lt "$(grep -n '^wall_up || down' "$C" | cut -d: -f1)" ] || { echo "FAIL  the trap must be armed before the first check" >&2; exit 1; }
# The shared helper's line ("See tty1 to repair") is defined first; the window's own definition must
# come after it, so it is the one in force. tty1 is where nothing comes up on a DOWN.
t1="$(grep -n 'See tty1 to repair' "$C" | tail -1 | cut -d: -f1)"; t3="$(grep -n 'Repair console: Ctrl+Alt+F3 (tty3)' "$C" | tail -1 | cut -d: -f1)"
[ -n "$t3" ] && [ "$(grep -c '^console_banner() {' "$C")" = 2 ] && [ "$t3" -gt "${t1:-0}" ] || { echo "FAIL  the console line in force must point at tty3, not tty1" >&2; exit 1; }
pass=$((pass+1)); echo "  ok      the token is not removed before the verdict; the console line points at tty3"

# A run that is KILLED (systemd's start timeout sends TERM to the whole unit) must take an earlier
# token with it: a hung check has proved nothing. The whole process group is signalled, as systemd does.
state "$TABLE_OK" "$SET_NONE" "$ST_SEALED" 1 0;        expect "$C" "c   sealed, set empty: the token (before the hang)" 0 1 ""
command -v setsid >/dev/null || { echo "battery: setsid (util-linux) is needed for the killed-run case" >&2; exit 1; }
: > "$SC/hang"
setsid bash "$C" >/dev/null 2>&1 &
hung=$!
sleep 1
[ -e "$W/sealed-ok" ] || { echo "FAIL  the token vanished while a healthy box's check was merely running" >&2; exit 1; }
kill -TERM -- "-$hung" 2>/dev/null || kill -TERM "$hung"
wait "$hung" 2>/dev/null; hung_rc=$?
rm -f "$SC/hang"
[ ! -e "$W/sealed-ok" ] && [ "$hung_rc" != 0 ] || { echo "FAIL  a check killed mid-run left the old token (rc=$hung_rc)" >&2; exit 1; }
pass=$((pass+1)); echo "  ok      a hung check keeps the token while it runs, and a killed one takes it away"
state "$TABLE_OK" "$SET_NONE" "$ST_SEALED" 1 0;        expect "$C" "c   sealed, set empty: the token again after the kill" 0 1 ""

# The state is read BEFORE the set (the marker is written after the flush).
[ "$(grep -n "$W/bin/status" "$C" | head -1 | cut -d: -f1)" -lt "$(grep -n 'elems="$(set_state)"' "$C" | head -1 | cut -d: -f1)" ] || {
  echo "FAIL  the window script must read the state before the set" >&2; exit 1; }
pass=$((pass+1)); echo "  ok      the state is read before the set"

# Sealed by build, built with the window's ruleset: the build flag is the marker; the set must be empty.
S="$W/sbb-check"
rm -f "$W/sealed-ok"
state "$TABLE_OK" "$SET_NONE" - 0 0;                   expect "$S" "sbb sealed by build, set empty: the token" 0 1 ""
state "$TABLE_OK" "$(set_with 351)" - 0 0;             expect "$S" "sbb sealed by build, set NOT empty: DOWN" 1 0 "EGRESS SEAL DOWN"
state "$TABLE_OK" - - 0 0;                             expect "$S" "sbb sealed by build, set cannot be listed: DOWN" 1 0 "EGRESS SEAL DOWN"
state - "$SET_NONE" - 0 0;                             expect "$S" "sbb sealed by build, no table: DOWN" 1 0 "EGRESS SEAL DOWN"

echo "seal-check-window-battery: PASS ($pass cases)"
