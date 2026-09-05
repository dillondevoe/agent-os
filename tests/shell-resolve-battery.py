#!/usr/bin/env python3
# SIDE_EFFECTS — Geist's law as amended 2026-09-05T13:05Z: a box-runnable battery declares
# every effect that leaves the machine or outlives the run. Read as DATA by
# tests/vm-matrix-contract.py, not as a comment.
SIDE_EFFECTS = []

"""shell-resolve-battery.py — the brain's hand, resolved at BUILD time.

`run_command` is the brain's only hand, and it reached it through
`subprocess.run(["bash", "-c", ...])` — a NAME, resolved against the PATH of
whatever process happens to be running. Measured twice on a running deployment:
`brain-home.service` carries an explicit five-store-dir PATH with NO shell on it,
so every shell-out died `FileNotFoundError: 'bash'`. Note the mode — an errno
raised out of `subprocess.run` before any process starts, NOT an rc=127 a caller
could inspect.

The runtime fallback (`_resolve_shell`'s PATH probe, then absolute NixOS paths)
fixed that day and is a SECOND name-resolution assumption: it still asks the
machine a question at run time. Geist's ruling (2026-09-05T11:37Z) is that the
end state is the interpreter substituted at BUILD time, the same discipline as
`@GENESIS_PATH@` — so `genesis-open.nix` replaces `@SH@` with `${pkgs.bash}/bin/bash`
and the built brain never resolves a shell by name at all.

Both halves have to be armed, because each one passes the other's test:

  A  UNSUBSTITUTED (`@SH@` intact — a hand-deployed source brain) still resolves a
     shell via the fallback. The build-time literal must not break running from source.
  B  SUBSTITUTED wins, and wins WITHOUT CONSULTING THE PATH. Armed by emptying PATH
     entirely: if the answer still comes back, no name was resolved.
  C  SUBSTITUTED BUT ABSENT does not get trusted. A store path that is not there is
     not a shell, and returning it would move the ENOENT from resolve-time to run-time
     — a worse place, because by then the caller has been told it has a hand.
  D  PRE-FIX ARM — the resolver as it stood BEFORE the build literal, run against the
     real measured unit PATH recorded below, returns nothing findable by name. Without
     this arm, A and B could both be passing on a machine where `bash` is on every PATH
     and the whole defect is invisible. This is the arm that shows the fix was needed.
  E  NO SHELL AT ALL is reported as a cause, not an errno: `_sh` raises RuntimeError
     and never FileNotFoundError. The failure mode is the finding — an errno is what
     the model then narrates at 20s a turn.
  F  THE SUBSTITUTION IS STILL WIRED. A-E all run against the SOURCE tree, where `@SH@`
     is unsubstituted by definition, so not one of them would notice if the
     `--replace-fail '@SH@'` line were deleted from genesis-open.nix — the build would
     go on succeeding and the built brain would silently fall back to name resolution,
     which is the defect, restored, with every arm still green. `--replace-fail` makes
     a RENAMED placeholder loud; only this arm makes a REMOVED substitution loud.

Pure: no network, no writes outside a TemporaryDirectory, nothing outliving the run.
"""
import importlib.util, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MOD  = os.path.join(os.path.dirname(HERE), "modules", "agent-brain.py")
NIX  = os.path.join(os.path.dirname(HERE), "modules", "genesis-open.nix")

# A real brain-home.service PATH, read off a running deployment with
# `systemctl --user --machine=agent@ show brain-home -p Environment` and measured twice,
# independently, on 2026-09-05. Store hashes are pinned deliberately: this is a RECORD of an
# environment that existed, not a live lookup, and a live lookup would defeat the arm anyway
# by asking the machine running the tests instead of the machine that had the defect. Not one
# of these five dirs carries a shell — which is the entire defect, and D asserts it rather
# than describing it.
DELL_UNIT_PATH = ":".join([
    "/nix/store/mp8s10fwm685azvvv1qq7zyf7iajjlj8-coreutils-9.11/bin",
    "/nix/store/m1p6gxgxis75rn0d549ny2y8gpxjv9pd-findutils-4.10.0/bin",
    "/nix/store/xj9dgyqrcq8hrf4mrkvbcp4pa3hgrbhy-gnugrep-3.12/bin",
    "/nix/store/x2kfd3hnhqk8d6kxkyffpk7lyaz8iy76-gnused-4.10/bin",
    "/nix/store/axx9bvf0dmah41f39ds9xdkds1lsz6z9-systemd-261/bin",
])

FAILURES = []

def check(name, ok, detail="", why=""):
    """`detail` is what was OBSERVED and prints either way; `why` is what the failure MEANS
    and prints only on FAIL. Keeping them apart is not tidiness: a green line carrying a
    failure explanation is a label that disagrees with its own verdict, which is the exact
    class of defect this battery's subject was reported for."""
    line = ("  ok   " if ok else "  FAIL ") + name
    if detail: line += "  — " + detail
    if not ok and why: line += "  — " + why
    print(line)
    if not ok:
        FAILURES.append(name)

def load():
    """Import agent-brain.py fresh. It is a program, not a module: importing it must not
    start a brain, so this mirrors the loader the other batteries use."""
    spec = importlib.util.spec_from_file_location("agent_brain_under_test", MOD)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod

def with_path(path, fn):
    old = os.environ.get("PATH")
    os.environ["PATH"] = path
    try:
        return fn()
    finally:
        if old is None: os.environ.pop("PATH", None)
        else: os.environ["PATH"] = old

def main():
    mod = load()

    if not hasattr(mod, "SH_BUILD"):
        check("SH_BUILD exists as a build-time literal", False,
              "agent-brain.py has no SH_BUILD — the interpreter is still resolved by name only")
        print("\nshell-resolve: 1 FAILURE (the build literal is absent, so B/C cannot run)")
        return 1

    # A — unsubstituted source brain still finds a shell through the fallback.
    a = mod._resolve_shell(build="@SH@")
    check("A  unsubstituted @SH@ falls back and still resolves a shell",
          bool(a) and os.path.exists(a), "got %r" % (a,))

    # B — substituted wins with an EMPTY PATH. If a name were being resolved, "" resolves nothing.
    with tempfile.TemporaryDirectory() as d:
        baked = os.path.join(d, "bash")
        with open(baked, "w") as fh: fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(baked, 0o755)
        b = with_path("", lambda: mod._resolve_shell(build=baked))
        check("B  substituted @SH@ wins with PATH emptied (no name is resolved)",
              b == baked, "got %r, wanted %r" % (b, baked))

        # C — a baked path that is not there is not trusted.
        gone = os.path.join(d, "not-installed")
        c = with_path("", lambda: mod._resolve_shell(build=gone))
        check("C  substituted-but-ABSENT path is not returned", c != gone, "got %r" % (c,))

    # D — PRE-FIX ARM. The name-only resolver, against the recorded unit PATH.
    import shutil
    pre_fix = with_path(DELL_UNIT_PATH,
                        lambda: shutil.which("bash") or shutil.which("sh"))
    # D IS A RECORD, NOT AN ARM — geist's ruling on #282, and it is worth being exact about why.
    # `shutil.which` here runs over five store directories that exist on the Dell and on no other
    # machine. Off the Dell they are simply absent, so the lookup returns None for a reason that
    # has nothing to do with the defect, and D CANNOT GO RED anywhere this battery normally runs.
    # A green D therefore certifies nothing about the code; counting it as coverage would inflate
    # this battery by one arm that is structurally incapable of objecting.
    #
    # It stays because it is a good record: it pins the exact PATH the deployed unit had when the
    # defect was observed, so a future reader can see the environment rather than take my sentence
    # for it. Read it as evidence, not as a detector. Arm G (flake check
    # `sh-is-baked-into-the-built-brain`) is the detector that stands behind this file.
    check("D  RECORD (cannot fail off the Dell): name-only resolution finds NO shell on the recorded unit PATH",
          pre_fix is None, "found %r — if this is not None the defect is not reproduced here" % (pre_fix,))

    # E — no shell anywhere is a cause, not an errno.
    saved = mod.SHELL
    mod.SHELL = None
    try:
        try:
            mod._sh("echo hi", 2)
            check("E  no shell raises RuntimeError, never FileNotFoundError", False, "it did not raise")
        except RuntimeError:
            check("E  no shell raises RuntimeError, never FileNotFoundError", True)
        except FileNotFoundError:
            check("E  no shell raises RuntimeError, never FileNotFoundError", False,
                  "raised FileNotFoundError — the errno reaches the model again")
    finally:
        mod.SHELL = saved

    # F — the substitution is still wired. Read out of the nix file, not assumed.
    try:
        nix = open(NIX).read()
    except FileNotFoundError:
        check("F  genesis-open.nix substitutes @SH@", False,
              detail="genesis-open.nix is not beside the battery",
              why="the arm could not run, and an arm that does not run passes")
    else:
        wired = "--replace-fail '@SH@'" in nix
        check("F  genesis-open.nix still substitutes @SH@ at build time", wired,
              detail=("found the --replace-fail line" if wired else "no --replace-fail '@SH@' line"),
              why=("the built brain would fall back to resolving a shell by NAME, which is "
                   "the whole defect — restored silently, with A-E still green"))

    print("\nshell-resolve: %d checks, %d FAILURES" % (6, len(FAILURES)))
    return 1 if FAILURES else 0

if __name__ == "__main__":
    sys.exit(main())
