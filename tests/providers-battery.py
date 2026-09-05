#!/usr/bin/env python3
# tests/providers-battery.py — the CONTRACT BATTERY for modules/providers.py (Phase 1.5).
#
# Proves the acceptance criteria for the pluggable brain-provider config:
#   A. load_providers parses a valid providers.yaml and returns {"providers", "roles"}.
#   B. resolve(config, "floor") returns the floor provider (the offline guarantee); the OS must
#      always have a floor — missing-floor is a hard error (Geist spine).
#   C. resolve(config, "escalate") returns the cloud provider when configured; absent role is
#      NOT an error (escalate is optional — the floor alone is a complete OS).
#   D. resolve returns (name, cfg, degraded): degraded=True when the requested role points at a
#      different provider than the floor (so the caller can shape its fallback narrative).
#   E. provider config enforces required fields (kind, cost_tier) per provider.
#   F. api_key_ref, when present, MUST be a secret reference (op://... or similar); a literal key
#      is rejected — the fleet-bleed scar (2026-08-06) promoted this to an OS design rule.
#   G. unknown role names are rejected (only floor / escalate are valid).
#   H. cost_tier(config, name) returns the provider's cost_tier (default "unknown").
#   I. a present-but-invalid yaml FAILS LOUD (a broken provider config is a real error, not a
#      silent degrade) — mirrors the genesis-lock "refuse, don't repoint" rule.
#
# Zero external deps beyond pyyaml (already a NixOS/nixpkgs dependency; the module itself is
# stdlib+yaml). Exits 0 on all-pass, non-zero (AssertionError) on any failure.
# Run:  PYTHONPATH=modules python3 tests/providers-battery.py
# (pyyaml ships with the Nix closure; locally ensure pyyaml is importable.)

# MUTATES_SHARED_STATE — Geist's law, 2026-09-05: a box-runnable battery never mutates
# human-shared state. Read as DATA by tests/vm-matrix-contract.py, not as a comment.
MUTATES_SHARED_STATE = False

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "modules"))

import providers as P  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover — dev-machine affordance only
    yaml = None


def _write(cfg):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    yaml.dump(cfg, f)
    f.close()
    return f.name


def _cleanup(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_valid_load_and_floor():
    path = _write({
        "providers": {
            "local": {"kind": "ollama", "cost_tier": "free", "model": "qwen3.5:9b"},
        },
        "roles": {"floor": "local"},
    })
    try:
        cfg = P.load_providers(path)
        check(isinstance(cfg, dict), "load_providers must return a dict")
        check(set(cfg.keys()) == {"providers", "roles", "limits"}, "top-level keys wrong: %r" % cfg.keys())
        check(cfg["limits"] == {}, "absent limits block must load as {}: %r" % cfg["limits"])
        check(cfg["roles"] == {"floor": "local"}, "roles wrong: %r" % cfg["roles"])
        name, prov, degraded = P.resolve(cfg, "floor")
        check(name == "local", "floor name: %r" % name)
        check(prov["model"] == "qwen3.5:9b", "floor model: %r" % prov)
        check(degraded is False, "floor should not be degraded: %r" % degraded)
    finally:
        _cleanup(path)
    print("A. valid load + floor resolve — PASS")


def test_missing_floor_is_hard_error():
    path = _write({
        "providers": {
            "cloud": {"kind": "openai", "cost_tier": "paid", "api_key_ref": "op://vault/key"},
        },
        "roles": {"escalate": "cloud"},
    })
    try:
        try:
            P.load_providers(path)
            raise AssertionError("missing floor must raise ProviderConfigError")
        except P.ProviderConfigError as e:
            check("floor" in str(e).lower(), "error should mention floor: %r" % e)
    finally:
        _cleanup(path)
    print("B. missing floor is a hard error — PASS")


def test_escalate_optional():
    # A floor-only config is a complete OS — escalate may be absent.
    path = _write({
        "providers": {
            "local": {"kind": "ollama", "cost_tier": "free"},
        },
        "roles": {"floor": "local"},
    })
    try:
        cfg = P.load_providers(path)
        name, prov, degraded = P.resolve(cfg, "escalate")
        # unspecified role resolves to floor (the only configured role) and is NOT degraded
        check(name == "local", "absent escalate resolves to floor: %r" % name)
        check(degraded is False, "absent escalate should not be degraded")
    finally:
        _cleanup(path)
    print("C. escalate is optional (floor-only is complete) — PASS")


def test_escalate_configured():
    path = _write({
        "providers": {
            "local": {"kind": "ollama", "cost_tier": "free"},
            "cloud": {"kind": "claude", "cost_tier": "paid", "api_key_ref": "op://vault/key"},
        },
        "roles": {"floor": "local", "escalate": "cloud"},
    })
    try:
        cfg = P.load_providers(path)
        name, prov, degraded = P.resolve(cfg, "escalate")
        check(name == "cloud", "escalate name: %r" % name)
        check(prov["kind"] == "claude", "escalate kind: %r" % prov)
        check(degraded is False, "escalate should not be degraded when it IS the escalate role")
        # floor is still local, not degraded
        fname, fprov, fdeg = P.resolve(cfg, "floor")
        check(fdeg is False, "floor should not be degraded")
    finally:
        _cleanup(path)
    print("D. configured escalate resolves correctly — PASS")


def test_degraded_when_role_is_unavailable():
    # Criterion E: when the role's own provider is in the `unavailable` set AND differs from
    # floor, resolve degrades to floor and reports degraded=True. (When available, degraded is
    # always False even if the role points elsewhere — degraded means "fell back to floor", not
    # "points at a different provider".)
    path = _write({
        "providers": {
            "local": {"kind": "ollama", "cost_tier": "free"},
            "cloud": {"kind": "claude", "cost_tier": "paid", "api_key_ref": "op://vault/key"},
        },
        "roles": {"floor": "local", "escalate": "cloud"},
    })
    try:
        cfg = P.load_providers(path)
        # escalate's provider (cloud) is unavailable -> degrade to floor, degraded=True
        _, _, deg = P.resolve(cfg, "escalate", unavailable={"cloud"})
        check(deg is True, "escalate should be degraded when cloud is unavailable: %r" % deg)
        name, prov, _ = P.resolve(cfg, "escalate", unavailable={"cloud"})
        check(name == "local", "degraded escalate should resolve to floor: %r" % name)
        # When available, degraded is False even though escalate != floor
        _, _, deg2 = P.resolve(cfg, "escalate", unavailable=set())
        check(deg2 is False, "available escalate should not be degraded: %r" % deg2)
    finally:
        _cleanup(path)
    print("E. degraded flag when role's provider is unavailable — PASS")


def test_required_fields_enforced():
    # Each provider MUST carry kind + cost_tier. Missing one -> ProviderConfigError.
    for missing_field in ("cost_tier", "kind"):
        body = {"kind": "ollama", "cost_tier": "free"}
        del body[missing_field]
        path = _write({
            "providers": {"p": body},
            "roles": {"floor": "p"},
        })
        try:
            try:
                P.load_providers(path)
                raise AssertionError("missing %r should raise" % missing_field)
            except P.ProviderConfigError as e:
                check("p" in str(e), "error should name the provider: %r" % e)
                check(missing_field in str(e), "error should name the missing field: %r" % e)
        finally:
            _cleanup(path)
    # A complete body loads fine (sanity that the rejection is field-specific, not a blast radius).
    path = _write({
        "providers": {"p": {"kind": "ollama", "cost_tier": "free"}},
        "roles": {"floor": "p"},
    })
    try:
        cfg = P.load_providers(path)
        check("p" in cfg["providers"], "complete provider should load")
    finally:
        _cleanup(path)
    print("F. required fields (kind, cost_tier) enforced per provider — PASS")


def test_api_key_ref_must_be_secret_reference():
    path = _write({
        "providers": {
            "bad": {"kind": "ollama", "cost_tier": "free", "api_key_ref": "sk-literal-key"},
        },
        "roles": {"floor": "bad"},
    })
    try:
        try:
            P.load_providers(path)
            raise AssertionError("literal api_key_ref must raise")
        except P.ProviderConfigError as e:
            check("api_key_ref" in str(e), "error should mention api_key_ref: %r" % e)
            check("op://" in str(e) or "secret" in str(e).lower(),
                  "error should point at secret-reference form: %r" % e)
    finally:
        _cleanup(path)
    # A proper secret ref is accepted.
    path2 = _write({
        "providers": {
            "good": {"kind": "claude", "cost_tier": "paid", "api_key_ref": "op://vault/claude-key"},
        },
        "roles": {"floor": "good"},
    })
    try:
        cfg = P.load_providers(path2)
        name, prov, _ = P.resolve(cfg, "floor")
        check(name == "good" and prov["api_key_ref"] == "op://vault/claude-key",
              "secret-ref provider should load: %r" % prov)
    finally:
        _cleanup(path2)
    print("G. api_key_ref must be a secret reference (literal rejected) — PASS")


def test_unknown_role_rejected():
    path = _write({
        "providers": {
            "local": {"kind": "ollama", "cost_tier": "free"},
        },
        "roles": {"floor": "local", "weird": "local"},
    })
    try:
        try:
            P.load_providers(path)
            raise AssertionError("unknown role 'weird' must raise")
        except P.ProviderConfigError as e:
            check("weird" in str(e), "error should name the bad role: %r" % e)
    finally:
        _cleanup(path)
    print("H. unknown role names rejected — PASS")


def test_cost_tier():
    path = _write({
        "providers": {
            "local": {"kind": "ollama", "cost_tier": "free"},
            "cloud": {"kind": "claude", "cost_tier": "paid"},
            "noop": {"kind": "mock", "cost_tier": "unknown"},
        },
        "roles": {"floor": "local"},
    })
    try:
        cfg = P.load_providers(path)
        check(P.cost_tier(cfg, "local") == "free", "cost_tier local")
        check(P.cost_tier(cfg, "cloud") == "paid", "cost_tier cloud")
        check(P.cost_tier(cfg, "noop") == "unknown", "cost_tier explicit unknown")
        check(P.cost_tier(cfg, "absent") == "unknown", "cost_tier absent provider defaults unknown")
    finally:
        _cleanup(path)
    print("I. cost_tier reads provider cost_tier (default unknown) — PASS")


def test_invalid_yaml_fails_loud():
    # A present-but-unparseable yaml is a real error, not a silent degrade. The module uses
    # yaml.safe_load and raises ProviderConfigError on missing providers block — confirm the
    # parse error propagates (not swallowed into "no providers").
    path = tempfile.mktemp(suffix=".yaml")
    try:
        with open(path, "w") as f:
            f.write("providers:\n  - broken\n    : yaml\n")
        # DO NOT reinstate `raise AssertionError(...)` inside a try whose handler is
        # `except Exception`. AssertionError IS an Exception, so the handler swallowed the
        # arm's own failure signal and reported it as the rejection under test: this arm
        # printed PASS *precisely when* load_providers silently degraded. Reproduced
        # 2026-09-02 by stubbing load_providers to return {} — arm J passed, naming
        # 'AssertionError' as the exception that proved the module was loud.
        #
        # `else:` cannot be reached by an exception, so the failure signal has nowhere to hide.
        # And the handler names WHICH rejection: any exception at all would let an unrelated
        # error (a typo in the fixture path, an import failure) stand in for the parse refusal.
        try:
            P.load_providers(path)
        except P.ProviderConfigError as e:
            check("not valid YAML" in str(e),
                  "broken yaml must be refused AS a parse error, got: %s" % e)
        else:
            check(False, "broken yaml was ACCEPTED — silent degrade, the exact thing this arm exists to catch")
    finally:
        _cleanup(path)
    print("J. present-but-invalid yaml fails loud (not silent degrade) — PASS")


def test_empty_providers_rejected():
    path = _write({"providers": {}, "roles": {"floor": "x"}})
    try:
        try:
            P.load_providers(path)
            raise AssertionError("empty providers must raise")
        except P.ProviderConfigError as e:
            check("providers" in str(e).lower(), "error should mention providers: %r" % e)
    finally:
        _cleanup(path)

    # A file with no providers key at all.
    path2 = _write({"roles": {"floor": "x"}})
    try:
        try:
            P.load_providers(path2)
            raise AssertionError("no providers key must raise")
        except P.ProviderConfigError:
            pass
    finally:
        _cleanup(path2)
    print("K. empty / missing providers block rejected — PASS")


def main():
    if yaml is None:
        # NOT A SKIP, and flake.nix already said so before this file agreed with it. The
        # providers-contract check's own comment calls pyyaml "a HARD REQUIREMENT, not a skip",
        # because "a missing pyyaml previously let agent-brain silently degrade every boot to
        # legacy OLLAMA_MODEL with an unseen stderr warning (K6 post-merge bug, PR #77); a
        # battery that SKIPs on missing pyyaml would hide that exact regression."
        #
        # This battery then skipped on missing pyyaml. The derivation supplies pyyaml today, so
        # nothing was actually hidden — but the protection lived entirely in the derivation, and
        # a green check would have survived losing it. The comment stated the contract; only the
        # exit code enforces it.
        sys.exit("FAIL: pyyaml is not importable. Refusing to exit 0 — a battery that skips "
                 "itself when its dependency is missing is indistinguishable from one that "
                 "passed, and pyyaml's absence is the regression this file exists to catch.")
    test_valid_load_and_floor()
    test_missing_floor_is_hard_error()
    test_escalate_optional()
    test_escalate_configured()
    test_degraded_when_role_is_unavailable()
    test_required_fields_enforced()
    test_api_key_ref_must_be_secret_reference()
    test_unknown_role_rejected()
    test_cost_tier()
    test_invalid_yaml_fails_loud()
    test_empty_providers_rejected()
    print("\nproviders contract battery: ALL PASS")


if __name__ == "__main__":
    main()
