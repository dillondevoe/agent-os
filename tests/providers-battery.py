#!/usr/bin/env python3
# tests/providers-battery.py — CONTRACT BATTERY for modules/providers.py
# (Phase 1.5A brain-adapter config core, task 287 slice 1).
#
# Proves: schema validation, floor-role requirement, literal-key rejection,
# and the never-spill degrade rule (unavailable cloud provider -> floor, not
# another metered provider).
#
# Zero external deps beyond PyYAML (already vendored in this repo's venv).
#   PYTHONPATH=modules python3 tests/providers-battery.py

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "modules"))

import providers as P  # noqa: E402

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
        print(f"FAIL: {msg}")
    else:
        print(f"ok: {msg}")


def write_yaml(text):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    f.write(text)
    f.close()
    return f.name


GOOD = """
providers:
  local-floor:
    kind: openai-compatible
    base_url: http://127.0.0.1:11434/v1
    model: qwen2.5:7b
    cost_tier: free
  claude:
    kind: anthropic
    model: claude-sonnet-5
    api_key_ref: op://vault/claude-key
    cost_tier: metered
roles:
  floor: local-floor
  escalate: claude
"""

path = write_yaml(GOOD)
cfg = P.load_providers(path)
check(cfg["roles"]["floor"] == "local-floor", "parses good config, floor role resolved")
check(set(cfg["providers"]) == {"local-floor", "claude"}, "both providers loaded")

name, provcfg, degraded = P.resolve(cfg, "escalate")
check(name == "claude" and not degraded, "escalate resolves to claude when available")

name, provcfg, degraded = P.resolve(cfg, "escalate", unavailable={"claude"})
check(name == "local-floor" and degraded, "unavailable cloud provider degrades to floor, flagged")

name, provcfg, degraded = P.resolve(cfg, "escalate", unavailable={"claude", "local-floor"})
check(name == "local-floor", "never spills to a different metered provider even if floor also listed unavailable")

check(P.cost_tier(cfg, "claude") == "metered", "cost_tier lookup works")

# --- third provider, zero code (acceptance criterion 2) ---
THIRD = """
providers:
  local-floor:
    kind: openai-compatible
    base_url: http://127.0.0.1:11434/v1
    model: qwen2.5:7b
    cost_tier: free
  claude:
    kind: anthropic
    model: claude-sonnet-5
    api_key_ref: op://vault/claude-key
    cost_tier: metered
  groq:
    kind: openai-compatible
    base_url: https://api.groq.com/openai/v1
    model: llama-3.1-70b
    api_key_ref: op://vault/groq-key
    cost_tier: metered
roles:
  floor: local-floor
  escalate: claude
"""
path3 = write_yaml(THIRD)
cfg3 = P.load_providers(path3)
check("groq" in cfg3["providers"], "third provider added via config block alone")

# --- validation failures ---
try:
    P.load_providers(write_yaml("providers: {}\n"))
    check(False, "empty providers block should raise")
except P.ProviderConfigError:
    check(True, "empty providers block raises ProviderConfigError")

try:
    P.load_providers(write_yaml("providers:\n  bad:\n    kind: anthropic\n"))
    check(False, "missing cost_tier should raise")
except P.ProviderConfigError:
    check(True, "missing required field raises ProviderConfigError")

try:
    P.load_providers(write_yaml(
        "providers:\n  bad:\n    kind: anthropic\n    cost_tier: metered\n"
        "    api_key_ref: sk-literal-not-a-reference\n"
    ))
    check(False, "literal api key should raise")
except P.ProviderConfigError:
    check(True, "literal (non-reference) api_key_ref raises ProviderConfigError")

try:
    P.load_providers(write_yaml(
        "providers:\n  claude:\n    kind: anthropic\n    cost_tier: metered\n"
        "roles:\n  escalate: claude\n"
    ))
    check(False, "missing floor role should raise")
except P.ProviderConfigError:
    check(True, "config with no 'floor' role raises ProviderConfigError (offline guarantee)")

try:
    P.load_providers(write_yaml(
        "providers:\n  claude:\n    kind: anthropic\n    cost_tier: metered\n"
        "roles:\n  floor: nonexistent\n"
    ))
    check(False, "role pointing to undefined provider should raise")
except P.ProviderConfigError:
    check(True, "role pointing to undefined provider raises ProviderConfigError")

print()
if FAILS:
    print(f"{len(FAILS)} FAILURE(S)")
    sys.exit(1)
print("all providers-battery checks passed")
