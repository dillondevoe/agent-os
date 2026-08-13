#!/usr/bin/env python3
# Agent OS Phase 1.5A — pluggable brain provider config + role routing.
# Standalone module: parses a providers.yaml, validates the schema, and resolves
# which provider answers a `floor`/`escalate` role turn. WIRED into agent-brain.py
# as the floor-role resolver since PR #77 (2026-08-10, task 287 slice 2): agent-brain.py
# reads providers.yaml at startup and resolves `floor` through this module, falling back
# to the legacy OLLAMA_MODEL env default when providers.yaml is absent; a present-but-
# invalid yaml fails loud rather than silently degrading. The cloud `escalate` role is
# not wired yet — that remains a follow-up slice.
# Spec: jarvis-sync/spec-agentos-phase-1.5-pluggable-brain-portable-identity-2026-08-06.md §1.
#
# Hard rule from the spec (2026-08-06 fleet overnight-bleed scar, promoted to an
# OS design rule): each provider is its own metered bucket. A rate-limited/
# unreachable cloud provider degrades to the local floor — it NEVER silently
# spills onto another metered provider. Crossing buckets is a human's config
# edit, never a fallback chain's decision.

import yaml

REQUIRED_PROVIDER_FIELDS = {"kind", "cost_tier"}
VALID_ROLES = {"floor", "escalate"}


class ProviderConfigError(ValueError):
    pass


def load_providers(path):
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    providers = raw.get("providers") or {}
    if not providers:
        raise ProviderConfigError(f"{path}: no 'providers' block")
    for name, cfg in providers.items():
        missing = REQUIRED_PROVIDER_FIELDS - cfg.keys()
        if missing:
            raise ProviderConfigError(f"provider '{name}' missing fields: {sorted(missing)}")
        if "api_key_ref" in cfg and not str(cfg["api_key_ref"]).count("://"):
            raise ProviderConfigError(
                f"provider '{name}': api_key_ref must be a secret reference "
                f"(e.g. op://vault/item), never a literal key"
            )
    roles = raw.get("roles") or {}
    for role, provider_name in roles.items():
        if role not in VALID_ROLES:
            raise ProviderConfigError(f"unknown role '{role}' (valid: {sorted(VALID_ROLES)})")
        if provider_name not in providers:
            raise ProviderConfigError(f"role '{role}' points to undefined provider '{provider_name}'")
    if "floor" not in roles:
        raise ProviderConfigError("roles must define 'floor' — the OS must always have an offline floor")
    return {"providers": providers, "roles": roles}


def resolve(config, role, unavailable=frozenset()):
    """Pick the provider for a role, given a set of provider names known to be
    unreachable/rate-limited this turn. Never crosses metered buckets: if the
    role's own provider is unavailable, degrade straight to 'floor' and report
    it — do not try other metered providers first."""
    if role not in VALID_ROLES:
        raise ProviderConfigError(f"unknown role '{role}'")
    roles = config["roles"]
    providers = config["providers"]
    wanted = roles.get(role, roles["floor"])
    if wanted not in unavailable:
        return wanted, providers[wanted], False
    floor_name = roles["floor"]
    degraded = wanted != floor_name
    return floor_name, providers[floor_name], degraded


def cost_tier(config, provider_name):
    return config["providers"].get(provider_name, {}).get("cost_tier", "unknown")
