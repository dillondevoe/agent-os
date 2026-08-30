#!/usr/bin/env bash
# Install the personal-data gate as a local pre-push hook.
#
# PRE-PUSH, not pre-commit, and the choice is deliberate: a local commit is not a
# publication. Blocking commits would make the gate an obstacle during ordinary
# work (including the cleanup commits that REMOVE these strings), and a gate people
# route around is worse than none. Publication is the event worth stopping.
#
# Hooks are not version-controlled and are opt-in per clone, so this hook is layer 1
# of three and the weakest: it protects the operator's own machine. Layer 2 is the CI
# workflow, which cannot be skipped with --no-verify. Layer 3 is GitHub push protection.
set -eu
root=$(git rev-parse --show-toplevel)
hook="$root/.git/hooks/pre-push"
cat > "$hook" <<'HOOK'
#!/usr/bin/env bash
# personal-data gate -- installed by tools/install-hooks.sh
set -u
root=$(git rev-parse --show-toplevel)
status=0
while read -r _lref lsha _rref rsha; do
  [ "$lsha" = "0000000000000000000000000000000000000000" ] && continue
  if [ "$rsha" = "0000000000000000000000000000000000000000" ]; then
    base=$(git merge-base "$lsha" origin/HEAD 2>/dev/null || echo "$lsha~1")
  else
    base=$rsha
  fi
  "$root/tools/personal-data-gate.sh" "$base..$lsha" || status=1
done
exit $status
HOOK
chmod +x "$hook"
echo "installed: $hook"
