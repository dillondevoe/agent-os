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

import re
import yaml

REQUIRED_PROVIDER_FIELDS = {"kind", "cost_tier"}
VALID_ROLES = {"floor", "escalate"}
# Cost-cap breaker knobs (HARNESS-MAP guardrail 3). Both are per-TURN ceilings enforced
# by agent-brain's turn() loop; absent keys mean "keep the built-in default" there, so an
# existing providers.yaml with no limits block changes nothing.
VALID_LIMIT_KEYS = {"max_hops_per_turn", "max_output_tokens_per_turn"}
# Think-Twice Stream Rules (HARNESS-MAP guardrail 4, TTSR). Each entry is a regex watched
# against the assistant's STREAMING content buffer by agent-brain's chat_stream; a match
# aborts that attempt and retries (bounded by max_retries, default 1) with `rule` injected
# as an ephemeral role:system message at request-build time — never into the transcript.
# Yaml-only on purpose: a regex in an env var is a quoting hazard, and an invalid rule must
# fail boot here, not fire a re.error mid-stream.
VALID_STREAM_RULE_KEYS = {"id", "pattern", "rule", "max_retries"}
REQUIRED_STREAM_RULE_KEYS = {"id", "pattern", "rule"}


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
    limits = raw.get("limits") or {}
    if not isinstance(limits, dict):
        raise ProviderConfigError(f"'limits' must be a mapping, got {type(limits).__name__}")
    unknown = set(limits) - VALID_LIMIT_KEYS
    if unknown:
        raise ProviderConfigError(
            f"unknown limits keys: {sorted(unknown)} (valid: {sorted(VALID_LIMIT_KEYS)})")
    for k, v in limits.items():
        # bool is an int subclass — `max_hops_per_turn: true` must not validate as 1.
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            raise ProviderConfigError(f"limits.{k} must be a positive integer, got {v!r}")
    stream_rules = raw.get("stream_rules") or []
    if not isinstance(stream_rules, list):
        raise ProviderConfigError(f"'stream_rules' must be a list, got {type(stream_rules).__name__}")
    seen_ids = set()
    for i, r in enumerate(stream_rules):
        where = f"stream_rules[{i}]"
        if not isinstance(r, dict):
            raise ProviderConfigError(f"{where} must be a mapping, got {type(r).__name__}")
        missing = REQUIRED_STREAM_RULE_KEYS - r.keys()
        if missing:
            raise ProviderConfigError(f"{where} missing fields: {sorted(missing)}")
        unknown = set(r) - VALID_STREAM_RULE_KEYS
        if unknown:
            raise ProviderConfigError(
                f"{where} unknown keys: {sorted(unknown)} (valid: {sorted(VALID_STREAM_RULE_KEYS)})")
        for k in ("id", "pattern", "rule"):
            if not isinstance(r[k], str) or not r[k].strip():
                raise ProviderConfigError(f"{where}.{k} must be a non-empty string, got {r[k]!r}")
        if r["id"] in seen_ids:
            raise ProviderConfigError(f"{where}: duplicate id {r['id']!r}")
        seen_ids.add(r["id"])
        try:
            re.compile(r["pattern"])
        except re.error as e:
            raise ProviderConfigError(f"{where} ({r['id']!r}): pattern does not compile: {e}")
        mr = r.get("max_retries", 1)
        if isinstance(mr, bool) or not isinstance(mr, int) or mr <= 0:
            raise ProviderConfigError(f"{where}.max_retries must be a positive integer, got {mr!r}")
    return {"providers": providers, "roles": roles, "limits": limits, "stream_rules": stream_rules}


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
