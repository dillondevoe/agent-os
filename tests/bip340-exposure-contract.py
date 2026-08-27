#!/usr/bin/env python3
"""Enforce ruling condition 3: `modules/bip340.py` MUST NOT be reachable from network-facing code.

WHY THIS EXISTS. modules/bip340.py opens with a binding condition, in prose:

    INTERIM SIGNER — MUST be replaced by libsecp256k1 (Path B) before any network exposure of
    these keys.

Geist's 2026-08-19 Path-A ruling turns entirely on that clause. The vendored BIP-340 reference
implementation is not timing-hardened, and the ruling accepts it *because* Phase 1.5B has zero
network surface: a local same-UID adversary can read the private key file outright, which is
strictly stronger than timing the signer, so hardening buys nothing. The moment a remote party
can invoke and time this code the argument inverts and Path B becomes mandatory.

The condition was enforced by that paragraph and by nobody reading it. This file enforces it.

WHAT IT ASSERTS, and the two failures are different:

  * EXPOSURE — a module that does network I/O can reach `bip340`, directly or transitively.
    This is the binding condition. It is the day the ruling's premise stops being true, and it
    should be red before the code is merged, not discovered afterwards.
  * ALLOWLIST DRIFT — a new importer appears that is NOT network-facing. Not a violation; the
    ruling does not forbid new local callers. But every new caller widens the surface that must
    stay local, so it lands as a red that a human clears by adding one line here, deliberately,
    with the ruling in front of them. Reported with its own reason string so the two are never
    confused in a CI log.

IMPORTLIB IS NOT OPTIONAL, and the repo proves it. `bin/audit` reaches both `identity` and
`bip340` through `importlib.import_module(...)` (bin/audit:114) and appears in NO static import
grep. A `grep -rn 'import identity'` over modules/ and bin/ returns ZERO hits on a tree where
`bin/audit` demonstrably imports it — the instrument is blind to the exact caller that matters
most, the one holding the signing key. Arms A3 and A6 of the selftest are built on that: A6 runs
the static-only extractor against the importlib edge and asserts it MISSES.

`urllib.parse` IS NOT NETWORK I/O. It is string surgery over URLs and appears in `bin/confirm`
and `bin/broker` for splitting, not fetching. A checker that flagged it would be red on a tree
that is fine, and a check that cries wolf is uninstalled long before the day it is right.
Control arm A5 pins this.

DELIBERATELY NOT SILENT-SKIPPABLE. If discovery finds no sources, or finds no `bip340` module at
all, this exits non-zero. A check that degrades to a no-op when its input vanishes is precisely
the class of bug it exists to catch — docs/cancelled-boundaries.md, members 3, 8 and 10.

Local use:
    python3 tests/bip340-exposure-contract.py
    python3 tests/bip340-exposure-selftest.py     # 8 arms, 3 of them controls

Control arms (each MUST be red — if one is not, that rule is not a rule):
    see tests/bip340-exposure-selftest.py, which runs them all in-process.
"""
import argparse
import ast
import pathlib
import sys

# The module under protection.
TARGET = "bip340"

# Modules that perform, or directly wrap, network I/O. `urllib.parse` is deliberately absent —
# see the docstring. Kept as top-level package names; `imports_of` compares on the first segment
# for dotted forms so `http.client` and `urllib.request` are caught by `http` / `urllib` entries
# only where those entries are listed as I/O-capable below.
NETWORK_ROOTS = frozenset({
    "socket", "ssl", "asyncio", "selectors", "requests", "aiohttp", "httpx",
    "ftplib", "smtplib", "imaplib", "poplib", "telnetlib", "xmlrpc", "socketserver",
    "websockets", "paramiko",
})
# Dotted modules that are network-capable even though their ROOT is not (urllib.parse is not).
NETWORK_DOTTED = frozenset({
    "urllib.request", "urllib.error", "http.client", "http.server", "http.cookiejar",
})

# Modules permitted to reach the interim signer. Adding a name here is a deliberate act:
# it asserts the new caller is local-only and stays local-only.
ALLOWLIST = frozenset({"bip340", "identity", "audit"})

ROOTS = ("modules", "bin")


def _is_network(name: str) -> bool:
    if name in NETWORK_DOTTED:
        return True
    return name.split(".")[0] in NETWORK_ROOTS


def imports_of_static_only(src: str) -> set:
    """Static `import x` / `from x import y` only. The NAIVE extractor, kept because the
    selftest's pre-fix arm (A6) runs it against the importlib edge to show it misses."""
    out = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and not node.level:
                out.add(node.module)
    return out


def imports_of(src: str) -> set:
    """Static imports PLUS `importlib.import_module("literal")`. See the docstring: bin/audit
    reaches the signer this way and is invisible to the static half."""
    out = imports_of_static_only(src)
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name not in ("import_module", "__import__"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            out.add(node.args[0].value)
    return out


def evaluate(sources, allowlist=ALLOWLIST, _imports_of=None):
    """sources: {module_name: source_text}. Returns (rc, report_lines)."""
    get = _imports_of or imports_of
    rep = []

    if not sources:
        return 1, ["VACUOUS: no sources discovered — the instrument found nothing to check, "
                   "which is not the same as finding nothing wrong."]
    if TARGET not in sources:
        return 1, [f"VACUOUS: no `{TARGET}` module among {len(sources)} discovered sources — "
                   f"either it moved or discovery is broken; either way this check is not "
                   f"checking what it claims to."]

    edges = {name: get(src) for name, src in sources.items()}

    # Reverse closure: everything that can reach TARGET.
    reaches = {TARGET}
    changed = True
    while changed:
        changed = False
        for name, imps in edges.items():
            if name in reaches:
                continue
            if any(i.split(".")[0] in reaches for i in imps):
                reaches.add(name)
                changed = True

    exposed = sorted(n for n in reaches
                     if n != TARGET and any(_is_network(i) for i in edges.get(n, ())))
    drift = sorted(n for n in reaches if n not in allowlist)

    rep.append(f"reachers of `{TARGET}`: {', '.join(sorted(reaches))}")

    rc = 0
    if exposed:
        rc = 1
        rep.append("")
        rep.append("EXPOSURE — ruling condition 3 is VIOLATED. These modules do network I/O and "
                   f"can reach the interim signer `{TARGET}`:")
        for n in exposed:
            nets = sorted(i for i in edges.get(n, ()) if _is_network(i))
            rep.append(f"  {n}  (network: {', '.join(nets)})")
        rep.append("modules/bip340.py's own header states the condition: the interim signer MUST "
                   "be replaced by libsecp256k1 (Path B) before any network exposure of these "
                   "keys. Either revert the reach, or do Path B.")
    if drift:
        rc = 1
        rep.append("")
        rep.append("ALLOWLIST drift — these reach the interim signer and are not allowlisted:")
        for n in drift:
            rep.append(f"  {n}")
        rep.append("Not a violation by itself. Every new caller widens the surface that must stay "
                   "local; add it to ALLOWLIST in this file, deliberately, having checked it is "
                   "local-only.")
    if rc == 0:
        rep.append(f"OK — condition 3 holds: no network-facing module reaches `{TARGET}`.")
    return rc, rep


def discover(repo: pathlib.Path):
    """Module-name -> source, over modules/ and bin/. bin/ scripts carry no .py suffix, so they
    are taken by python shebang; a bin script that stops being python simply drops out, which is
    the safe direction (it can no longer import anything)."""
    out = {}
    for root in ROOTS:
        d = repo / root
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if not p.is_file():
                continue
            if p.suffix == ".py":
                out[p.stem] = p.read_text(encoding="utf-8", errors="replace")
            elif p.suffix == "":
                try:
                    head = p.open("rb").readline()
                except OSError:
                    continue
                if head.startswith(b"#!") and b"python" in head:
                    out[p.name] = p.read_text(encoding="utf-8", errors="replace")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=str(pathlib.Path(__file__).resolve().parent.parent))
    args = ap.parse_args()
    rc, rep = evaluate(discover(pathlib.Path(args.repo)))
    print("\n".join(rep))
    return rc


if __name__ == "__main__":
    sys.exit(main())
