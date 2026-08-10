#!/usr/bin/env python3
# wiring-battery.py — Phase 1.5 slice 2 (K6) integration check.
# Verifies agent-brain.py resolves its active MODEL through the provider config's
# `floor` role, falls back to OLLAMA_MODEL when no config exists, and fails loud on a
# present-but-invalid config. Mirrors providers-battery.py (standalone, py_compile + logic).
#
# Run: PYTHONPATH=modules python3 tests/wiring-battery.py
#
# HARD REQUIREMENT (K6 post-merge bug, PR #77): pyyaml MUST be present in the shipped
# brainPython env. providers.py does `import yaml`, and agent-brain.py's `except Exception`
# guard swallows any ImportError — so a missing pyyaml does NOT crash; it silently degrades
# every boot to legacy OLLAMA_MODEL with an unseen stderr warning. A test that SKIPs on
# missing pyyaml would HIDE that exact regression (the old SKIP branches were dead code for
# this reason). So: we FAIL LOUD if pyyaml is absent. Same genesis-lock parity the runtime now
# has via ps.pyyaml in brainPython.
import importlib.util, os, sys, tempfile, textwrap, py_compile

# ── pre-flight: pyyaml required, not optional ──
try:
    import yaml  # noqa: F401  (imported only to assert the shipped env carries it)
except ImportError:
    print("  FAIL pyyaml present in brainPython — providers.py needs it; a missing pyyaml "
          "silently degrades agent-brain to legacy OLLAMA_MODEL (K6 bug, PR #77). Add ps.pyyaml "
          "to genesis-open.nix's brainPython.")
    sys.exit(1)

MOD = "modules/agent-brain.py"
EX = 0

def check(name, cond):
    global EX
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond: EX = 1

# 1) syntax: both modules compile clean
for f in (MOD, "modules/providers.py"):
    try:
        py_compile.compile(f, doraise=True); print("  PASS compile " + f)
    except py_compile.PyCompileError as e:
        print("  FAIL compile " + f + ": " + str(e)); EX = 1

# make modules/ importable for the importlib load below
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "modules"))

def load_brain(providers_yaml=None):
    env = dict(os.environ)
    if providers_yaml is None:
        env.pop("AGENT_OS_PROVIDERS", None)
        # point at a non-existent path so the missing-file fallback triggers
        env["AGENT_OS_PROVIDERS"] = "/nonexistent/providers.yaml"
    else:
        env["AGENT_OS_PROVIDERS"] = providers_yaml
    env["OLLAMA_MODEL"] = "qwen3.5:9b"   # deterministic env default
    os.environ.update(env)
    spec = importlib.util.spec_from_file_location("agent_brain_test", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

# 2) no config → MODEL == env default, ACTIVE_PROVIDER == "env-default"
try:
    b = load_brain()
    check("no-config MODEL == OLLAMA_MODEL env", b.MODEL == "qwen3.5:9b")
    check("no-config ACTIVE_PROVIDER == env-default", b.ACTIVE_PROVIDER == "env-default")
except Exception as e:
    check("no-config load", False); print("    " + repr(e))

# 3) valid config → MODEL == floor provider's `model:` key, ACTIVE_PROVIDER == floor name
try:
    yml = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yml.write(textwrap.dedent("""
        providers:
          local-ollama: {kind: ollama, cost_tier: free, model: qwen3.5:9b-agentos}
          cloud-claude: {kind: claude, cost_tier: metered, api_key_ref: op://v/cloud/k}
        roles:
          floor: local-ollama
          escalate: cloud-claude
    """))
    yml.close()
    b = load_brain(yml.name)
    check("config MODEL == floor model key", b.MODEL == "qwen3.5:9b-agentos")
    check("config ACTIVE_PROVIDER == floor name", b.ACTIVE_PROVIDER == "local-ollama")
except Exception as e:
    check("config load", False); print("    " + repr(e))

# 4) missing-model-key in floor → falls back to env default, still names the floor provider
try:
    yml = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yml.write(textwrap.dedent("""
        providers:
          local-ollama: {kind: ollama, cost_tier: free}
        roles:
          floor: local-ollama
    """))
    yml.close()
    b = load_brain(yml.name)
    check("floor-without-model key → env default model", b.MODEL == "qwen3.5:9b")
    check("floor-without-model key → provider named", b.ACTIVE_PROVIDER == "local-ollama")
except Exception as e:
    check("floor-without-model load", False); print("    " + repr(e))

print("wiring-battery: " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
