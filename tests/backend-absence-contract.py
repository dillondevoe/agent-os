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
BACKENDS = ("pdfinfo", "pdftotext", "ffprobe", "ffmpeg", "curl", "trafilatura", "qalc", "khal")

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

def findings():
    """(file, backend) for every backend a hand invokes with no absence guard."""
    bad = []
    for fn, body in hand_bodies().items():
        code = strip_comments_and_heredocs(body)
        invoked, guarded = invoked_and_guarded(code)
        for b in sorted(invoked - guarded):
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
