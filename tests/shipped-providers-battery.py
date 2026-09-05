# SIDE_EFFECTS — Geist's law as amended 2026-09-05T13:05Z: a box-runnable battery declares
# every effect that leaves the machine or outlives the run. Read as DATA by
# tests/vm-matrix-contract.py, not as a comment.
SIDE_EFFECTS = []

import os, sys
sys.path.insert(0, "modules")
from providers import load_providers, resolve

EX = 0
def check(name, cond, detail=""):
    global EX
    print(("  PASS " if cond else "  FAIL ") + name + (("\n      " + detail) if detail and not cond else ""))
    if not cond: EX = 1

# The bytes under test are the ones environment.etc actually installs on the box — pulled from
# the EVALUATED nixos configuration, not a copy of them kept in step by hand. Until this check
# existed, nothing in the tree ever parsed the file the Dell boots with: the batteries all
# author their own fixtures, so a typo in the module shipped green and only surfaced as a brain
# that could not read its own config. That is the shape of #243 — CI green on a package that
# cannot arm a budget — pointed at providers.yaml instead.
path = os.environ["SHIPPED_PROVIDERS"]
print("shipped-providers-battery: %s" % path)

try:
    cfg = load_providers(path)
    check("the shipped providers.yaml parses through the real load_providers", True)
except Exception as e:
    check("the shipped providers.yaml parses through the real load_providers", False, repr(e))
    print("  FAILURES"); sys.exit(1)

roles = cfg.get("roles", {})
check("declares a floor role", "floor" in roles, repr(roles))
check("declares an escalate role", "escalate" in roles, repr(roles))

fname, fcfg, fdeg = resolve(cfg, "floor")
check("floor resolves and is not degraded", fname is not None and not fdeg, repr((fname, fdeg)))
check("floor carries an explicit model, so resolution is off the OLLAMA_MODEL default",
      bool(fcfg.get("model")), repr(fcfg))

ename, ecfg, edeg = resolve(cfg, "escalate")
check("escalate resolves to a provider distinct from the floor",
      ename is not None and ename != fname, repr((fname, ename)))
check("escalate carries an explicit model", bool(ecfg.get("model")), repr(ecfg))

ref = ecfg.get("api_key_ref") or ""
check("escalate declares an api_key_ref", bool(ref), repr(ecfg))
scheme = ref.split("://", 1)[0] if "://" in ref else ""
# agent-brain's _resolve_secret is the authority on which schemes exist. op:// is refused by
# design (2026-09-01), and a module shipping one would deploy an escalate role that can never
# arm — green here, dead on the box.
check("escalate api_key_ref uses a scheme the resolver supports",
      scheme in ("file", "env"), repr(ref))
check("escalate api_key_ref is a REFERENCE, never a literal key",
      "://" in ref and not ref.startswith("sk-"), repr(ref))
# The secret must live outside the repo AND outside the image: a store path would be
# world-readable in /nix/store, which is the whole reason for the out-of-tree convention.
if scheme == "file":
    p = ref.split("://", 1)[1]
    check("a file:// key path is absolute", p.startswith("/"), repr(p))
    check("a file:// key path is NOT in the nix store (world-readable)",
          not p.startswith("/nix/store"), repr(p))

# ── NEGATIVE ARMS ─────────────────────────────────────────────────────────────────────────
# Everything above is a permitting arm: it passes because the shipped file is currently correct.
# A battery made only of those cannot tell "the config is good" from "the assertions are vacuous",
# and it would go on printing ALL PASS if a later refactor silently stopped reading the file.
# These arms feed DELIBERATELY BROKEN configs through the same predicates and require rejection.
import tempfile, textwrap

def _verdict(yaml_text):
    """Return the list of failing predicate names for a config, using the same rules as above."""
    fh = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    fh.write(textwrap.dedent(yaml_text)); fh.close()
    bad = []
    try:
        c = load_providers(fh.name)
    except Exception:
        return ["parse"]
    r = c.get("roles", {})
    if "escalate" not in r: bad.append("escalate-role")
    if "floor" not in r: bad.append("floor-role")
    try:
        _fn, _fc, _fd = resolve(c, "floor")
        if not _fc.get("model"): bad.append("floor-model")
    except Exception:
        bad.append("floor-resolve")
    if "escalate" in r:
        _en, _ec, _ed = resolve(c, "escalate")
        _ref = _ec.get("api_key_ref") or ""
        _sc = _ref.split("://", 1)[0] if "://" in _ref else ""
        if not _ref: bad.append("no-ref")
        elif _sc not in ("file", "env"): bad.append("bad-scheme")
        elif _sc == "file" and _ref.split("://", 1)[1].startswith("/nix/store"): bad.append("store-path")
    return bad

GOOD = """
    providers:
      local-ollama: {kind: ollama, cost_tier: floor, model: qwen3.5:9b}
      cloud-claude: {kind: claude, cost_tier: escalate, model: claude-sonnet-5,
                     api_key_ref: file:///var/lib/agos-escalate/claude-api-key}
    roles: {floor: local-ollama, escalate: cloud-claude}
"""
# N0 is the permitting arm for the negative harness itself — without it, a _verdict() that
# returned a failure for EVERYTHING would make every arm below pass for the wrong reason.
check("N0: a well-formed config is accepted by the negative harness", _verdict(GOOD) == [],
      repr(_verdict(GOOD)))
check("N1: an op:// ref is rejected — the scheme the resolver refuses by design",
      "bad-scheme" in _verdict(GOOD.replace("file:///var/lib/agos-escalate/claude-api-key",
                                            "op://agent-os/claude/api_key")))
check("N2: a key inside the nix store is rejected — /nix/store is world-readable",
      "store-path" in _verdict(GOOD.replace("/var/lib/agos-escalate/claude-api-key",
                                            "/nix/store/abc-claude-api-key")))
check("N3: a floor provider with no model is rejected — it would silently fall back to the "
      "OLLAMA_MODEL default", "floor-model" in _verdict(GOOD.replace(", model: qwen3.5:9b", "")))
check("N4: an escalate provider with no api_key_ref is rejected",
      "no-ref" in _verdict(GOOD.replace(",\n                     api_key_ref: "
                                        "file:///var/lib/agos-escalate/claude-api-key", "")))
check("N5: a config with no escalate role is reported as such, not crashed on",
      "escalate-role" in _verdict("""
    providers:
      local-ollama: {kind: ollama, cost_tier: floor, model: qwen3.5:9b}
    roles: {floor: local-ollama}
"""))
check("N6: unparseable yaml is rejected rather than read as empty",
      _verdict("providers: [this is: not: a mapping") == ["parse"])

print("  " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
