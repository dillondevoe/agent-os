#!/usr/bin/env python3
# backend-absence-contract.py — every agos-* hand that shells to an external backend must be able
# to say THE BACKEND IS MISSING, rather than diagnosing its subject.
#
# THE CLASS, found 2026-08-24 by continuing the assert-least ranking past agos-calc. Each of these
# hands wraps its backend in `if ! out=$(backend ...)`, which is correct for a real failure — but
# "the tool is not installed" and "the subject is broken" arrive through the SAME branch, and the
# branch names the subject. Measured, not inferred, with the backend stripped from PATH:
#
#     agos-doc   info <a valid PDF>   -> {ok:false, error:"not a readable PDF"}
#     agos-media info <a valid mp4>   -> {ok:false, error:"not a readable media file"}
#     agos-web   fetch <a live url>   -> {ok:false, error:"fetch failed or empty"}
#
# Every one of those is a confident, specific, FALSE diagnosis of a subject the hand never reached.
# This is the far end of a severity ordering the repo built up over one day: agos-notes and agos-cal
# left the caller UNINFORMED (a bare value under a failing rc); agos-calc refused with no reason at
# all; agos-files stamped ok:true on an empty answer, MISINFORMING the caller; and these three
# misinform it with a diagnosis it has every reason to believe. agos-web is the worst placed of the
# three, because "fetch failed" and "no readable content" are things that genuinely happen all the
# time — the false version is indistinguishable from the true one.
#
# WHY THIS IS STATIC. The runtime probe needs the hand's CLI on PATH, and each hand is checked in
# its own derivation, so no single lane has them all. A static sweep runs everywhere, costs nothing,
# and — the actual point — catches the NEXT hand somebody adds, which is the only version of this
# check that keeps working after today.
#
# Run: python3 tests/backend-absence-contract.py
import os, re, sys, textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKGS = os.environ.get("AGOS_PKGS_DIR") or os.path.join(ROOT, "modules", "pkgs")

EX = 0
def check(name, cond, detail=""):
    global EX
    print(("  PASS " if cond else "  FAIL ") + name + (("  — " + detail) if detail else ""))
    if not cond: EX = 1

# The external backends these hands shell out to. Shell builtins and the coreutils/jq/sed/grep
# family are deliberately NOT here: they come from the same nixpkgs closure as the wrapper itself,
# so "jq is missing" is not a state a built hand can reach, and guarding it would be noise.
BACKENDS = ("pdfinfo", "pdftotext", "ffprobe", "ffmpeg", "curl", "trafilatura", "qalc", "khal",
            "wpctl", "brightnessctl", "nmcli")

# THIS TUPLE WAS SILENTLY INCOMPLETE FOR ITS FIRST DAY, and finding that is what produced the
# check below. It shipped without wpctl/brightnessctl/nmcli, so agos-sys — a hand with THREE
# external backends — was swept and reported clean, because the sweep was looking for names it
# had never been told about. A hand-maintained denylist fails in the direction that produces a
# green, which is the one direction an instrument must never fail in.
#
# So the list is now RATCHETED against something the repo already declares and cannot forget to
# update: `runtimeInputs`. Nix must be told every package a hand can reach, or the hand does not
# run at all — it is the one enumeration in this repo that cannot go stale, because the build
# breaks first. Every package there is either AMBIENT (the coreutils/jq/sed/grep family, from the
# same closure as the wrapper — "jq is missing" is not a state a built hand can reach) or it
# provides a backend this file must know by name. A package in neither set is a FAILURE, and the
# fix is to add it to one of them.
#
# Package names are not binary names (popplerUtils -> pdfinfo+pdftotext, wireplumber -> wpctl,
# networkmanager -> nmcli, libqalculate -> qalc), so the mapping is explicit. Writing it down is
# the point: it is the step that cannot be skipped when a hand grows a new backend.
AMBIENT_PKGS = {"coreutils", "gnugrep", "gnused", "gawk", "findutils", "jq", "pkgs.coreutils",
                "pkgs.gnused", "pkgs.gnugrep", "pkgs.jq", "pkgs.findutils", "pkgs.gawk"}
PKG_BINARIES = {
    "popplerUtils": ("pdfinfo", "pdftotext"),
    "ffmpegHeadless": ("ffprobe", "ffmpeg"),
    "wireplumber": ("wpctl",),
    "networkmanager": ("nmcli",),
    "brightnessctl": ("brightnessctl",),
    "libqalculate": ("qalc",),
    "trafilatura": ("trafilatura",),
    "pkgs.khal": ("khal",),
    "pkgs.curl": ("curl",),
}

def runtime_inputs(src):
    """The packages a hand declares to Nix. Returns [] if the hand has no runtimeInputs."""
    m = re.search(r"runtimeInputs\s*=\s*(?:with\s+pkgs;\s*)?\[(.*?)\]", src, re.S)
    if not m:
        return []
    body = re.sub(r"#[^\n]*", "", m.group(1))          # drop the trailing "# nmcli — ..." notes
    return [t for t in body.split() if t and t != ";"]

def declared_backends(src):
    """Backends a hand's runtimeInputs promise it can reach, plus the packages we cannot classify."""
    known, unknown = set(), []
    for pkg in runtime_inputs(src):
        if pkg in AMBIENT_PKGS:
            continue
        if pkg in PKG_BINARIES:
            known.update(PKG_BINARIES[pkg])
        else:
            unknown.append(pkg)
    return known, unknown

def hand_bodies():
    """Each hand's shell body, keyed by filename. Same extraction as hand-degrade-contract.py."""
    out = {}
    for fn in sorted(os.listdir(PKGS)):
        if not fn.startswith("agos-") or not fn.endswith(".nix"):
            continue
        src = open(os.path.join(PKGS, fn), encoding="utf-8").read()
        m = re.search(r"text\s*=\s*''\n(.*?)\n\s*''\s*;", src, re.S)
        if not m:
            continue
        out[fn] = textwrap.dedent(m.group(1))
    return out

def strip_comments_and_heredocs(body):
    """Drop comment lines and heredoc bodies. Usage text NAMES the backends in prose ('PDF via
    poppler', 'via ffprobe'), and a sweep that counted those would report guards as present in
    files that have none — a false green, which is the shape this whole file is about."""
    out, in_heredoc, delim = [], False, None
    for line in body.split("\n"):
        if in_heredoc:
            if line.strip() == delim:
                in_heredoc = False
            continue
        m = re.search(r"<<-?'?([A-Za-z_][A-Za-z0-9_]*)'?", line)
        if m:
            in_heredoc, delim = True, m.group(1)
            continue
        if line.lstrip().startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)

def invoked_and_guarded(code):
    """(set of backends this hand invokes, set it guards).

    A backend can be named two ways: bare (`ffprobe -v ...`) or through an override variable
    (`FFPROBE="${AGOS_MEDIA_FFPROBE:-ffprobe}"` then `"$FFPROBE" -v ...`). BOTH must count as an
    invocation, and INDEPENDENTLY of whether it is guarded — the first version of this function
    resolved variables only for backends that were already guarded, which made the test circular:
    a fixed hand's backend stopped being visible as an invocation at all, and the file reported
    ALL PASS by seeing nothing. Its own vacuity arm caught that on the first run, which is the
    entire argument for having vacuity arms."""
    varfor = {}
    for var, dflt in re.findall(r'([A-Z_][A-Z0-9_]*)="\W*\$\{[A-Z_][A-Z0-9_]*:-([a-z0-9_.-]+)\}"', code):
        varfor[var] = dflt
    invoked, guarded = set(), set()
    for b in BACKENDS:
        # At a COMMAND POSITION, and NOT required to carry a `-flag`. The first version demanded
        # one, so `"$PDFINFO" "$path"` — a real invocation with a bare argument — was invisible and
        # agos-doc's pdfinfo never appeared in the sweep at all.
        if re.search(r"(?:^|[|(;&]|\|\||&&)\s*" + re.escape(b) + r"(?=[\s;|&)]|$)", code, re.M):
            invoked.add(b)
    for var, b in varfor.items():
        if b in BACKENDS and re.search(r'"\$' + var + r'"\s', code):
            invoked.add(b)
    for var in re.findall(r'require_backend\s+"\$([A-Z_][A-Z0-9_]*)"', code):
        if var in varfor:
            guarded.add(varfor[var])
    for b in re.findall(r"require_backend\s+([a-z0-9_.-]+)\b", code):
        guarded.add(b)
    # AN INLINE `command -v` IS A GUARD TOO. The first version of this recognised only calls to a
    # `require_backend` helper, and flagged agos-calc — which guards its backend correctly, inline,
    # because it has exactly one. That is this repo's fourth scar in miniature: a check encoding an
    # assumption about the SPELLING of the thing it verifies rather than the property. What the
    # contract requires is that absence be DETECTED before the backend is used; which shape does
    # the detecting is not the contract's business.
    for var in re.findall(r'command -v "\$([A-Z_][A-Z0-9_]*)"', code):
        if var in varfor:
            guarded.add(varfor[var])
    for b in re.findall(r"command -v ([a-z0-9_.-]+)\b", code):
        guarded.add(b)
    return invoked, guarded

def tolerated(code, backend, varfor):
    """Backends whose ABSENCE is already handled without a guard, by falling back to a default.

    `net_state=$(nmcli ... 2>/dev/null) || net_state="unknown"` needs no guard: an absent nmcli
    produces a NULL FIELD, which is this hand's documented contract for `status`, not an error
    about the subject. Adding a guard there would convert a working degrade into a refusal — the
    contract would have forced a WRONG fix, which is the failure mode of a rule that knows only
    one shape of correctness.

    The distinction is narrow on purpose and it is the one that separates this from the four
    hands 619d3a9 fixed: those wrote `if ! out=$(pdfinfo ...)` and then EMITTED a diagnosis. A
    `|| var=` tolerance emits nothing and blames no one. Only the second form is excused."""
    names = [backend] + [v for v, d in varfor.items() if d == backend]
    for line in code.split("\n"):
        if not any(re.search(r"[\"$]?\b%s\b" % re.escape(n), line) for n in names):
            continue
        if re.search(r"\|\|\s*[A-Za-z_][A-Za-z0-9_]*=", line):
            return True
    return False

def findings():
    """(file, backend) for every backend a hand invokes with no absence guard."""
    bad = []
    for fn, body in hand_bodies().items():
        code = strip_comments_and_heredocs(body)
        invoked, guarded = invoked_and_guarded(code)
        varfor = dict(re.findall(r'([A-Z_][A-Z0-9_]*)="\W*\$\{[A-Z_][A-Z0-9_]*:-([a-z0-9_.-]+)\}"', code))
        for b in sorted(invoked - guarded):
            if tolerated(code, b, varfor):
                continue
            bad.append((fn, b))
    return bad

def main():
    bodies = hand_bodies()
    # VACUITY ARMS. A sweep whose extraction regex stops matching iterates over nothing and prints
    # a confident ALL PASS about a repo it never read — the same false-answer shape this file
    # exists to catch, pointed at itself.
    check("the sweep actually read the hands", len(bodies) >= 5,
          "found %d hand bodies in %s" % (len(bodies), PKGS))
    seen = sorted(set().union(*(invoked_and_guarded(strip_comments_and_heredocs(b))[0]
                                for b in bodies.values())))
    check("the sweep actually saw backend invocations", len(seen) >= 4,
          "invoked: " + ", ".join(seen))
    all_pkgs, all_declared = [], set()
    for fn in sorted(bodies):
        src = open(os.path.join(PKGS, fn), encoding="utf-8").read()
        all_pkgs += runtime_inputs(src)
        all_declared |= declared_backends(src)[0]
    # The ratchet has its own vacuity arms: a runtimeInputs regex that stops matching would make
    # every "is classified" arm pass on an EMPTY list, which is the exact green-by-seeing-nothing
    # this file was written about — one level up, inside the check that was supposed to prevent it.
    check("the ratchet actually parsed runtimeInputs", len(all_pkgs) >= 20,
          "%d packages across %d hands" % (len(all_pkgs), len(bodies)))
    check("the ratchet actually resolved packages to backends", len(all_declared) >= 5,
          "declared: " + ", ".join(sorted(all_declared)))

    # THE RATCHET. Every package a hand declares to Nix must be classified — ambient, or a
    # named backend. An unclassifiable package means this file has been outgrown, and it says so
    # instead of sweeping for names it was never told about.
    for fn in sorted(bodies):
        src = open(os.path.join(PKGS, fn), encoding="utf-8").read()
        known, unknown = declared_backends(src)
        check("%s: every runtimeInputs package is classified" % fn, not unknown,
              "unclassified: %s — add to AMBIENT_PKGS or PKG_BINARIES" % ", ".join(unknown))
        # And the classification must reach BACKENDS, or a hand could declare a backend this
        # sweep still cannot look for.
        missing = sorted(b for b in known if b not in BACKENDS)
        check("%s: every declared backend is one this sweep looks for" % fn, not missing,
              "declared but not in BACKENDS: " + ", ".join(missing))

    bad = findings()
    for fn, b in bad:
        check("%s guards `%s` against being absent" % (fn, b), False,
              "an absent `%s` is reported as a defect of the SUBJECT" % b)
    if not bad:
        print("OK: every hand that shells to an external backend can say the backend is missing "
              "(%d hands, %d backends invoked)." % (len(bodies), len(seen)))
    print("backend-absence-contract: " + ("ALL PASS" if EX == 0 else "FAILURES"))
    return EX

sys.exit(main())
