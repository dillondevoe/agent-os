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
# MUTATES_SHARED_STATE — Geist's law, 2026-09-05: a box-runnable battery never mutates
# human-shared state. Read as DATA by tests/vm-matrix-contract.py, not as a comment.
MUTATES_SHARED_STATE = False

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
    # The startup secret preflight (2026-09-01) RESOLVES the escalate provider's api_key_ref at
    # import and marks the provider UNAVAILABLE when it cannot. Fixtures below therefore have to
    # say, deliberately, whether the secret resolves — otherwise the arms test the floor path
    # under escalate names. This env var backs the resolvable `env://` fixture ref.
    env["AGENT_OS_TEST_ESCALATE_KEY"] = "sk-fixture-not-a-real-key"
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
          cloud-claude: {kind: claude, cost_tier: metered, api_key_ref: env://AGENT_OS_TEST_ESCALATE_KEY}
        roles:
          floor: local-ollama
          escalate: cloud-claude
    """))
    yml.close()
    b = load_brain(yml.name)
    check("config MODEL == floor model key", b.MODEL == "qwen3.5:9b-agentos")
    check("config ACTIVE_PROVIDER == floor name", b.ACTIVE_PROVIDER == "local-ollama")
    # ESCALATE_STATUS (task 287, escalate-role resolution slice, 2026-08-13): a status-only
    # signal, distinct from the deferred Anthropic shim — proves the escalate role resolves
    # to its configured provider without touching chat_stream's wire protocol at all.
    check("escalate configured when role present + reachable", b.ESCALATE_STATUS["configured"] is True)
    check("escalate provider name == cloud-claude", b.ESCALATE_STATUS["provider"] == "cloud-claude")
except Exception as e:
    check("config load", False); print("    " + repr(e))

# 3a) escalate role present but its SECRET does not resolve → NOT configured.
# This fixture used to be the one above: it referenced `op://v/cloud/k`, a scheme the resolver
# refuses by design, while asserting configured is True. That arm passed only because nothing
# resolved the ref at startup. Once the preflight did, the arm inverted — and the honest reading
# is that the OLD assertion was wrong, not the new behaviour: an escalate provider whose key
# cannot be resolved is UNAVAILABLE, and the status surface has to agree with the router standing
# next to it. So the unresolvable case keeps its own arm rather than being deleted.
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
    check("unresolvable escalate ref → NOT configured", b.ESCALATE_STATUS["configured"] is False)
    check("unresolvable escalate ref → provider marked unavailable",
          "cloud-claude" in b._ESCALATE_UNAVAILABLE)
    check("unresolvable escalate ref → floor still resolves normally",
          b.MODEL == "qwen3.5:9b-agentos" and b.ACTIVE_PROVIDER == "local-ollama")
    _r = b.ESCALATE_STATUS.get("reason") or ""
    check("unresolvable escalate ref → reason names the scheme, not a secret", "op" in _r)
except Exception as e:
    check("unresolvable-ref load", False); print("    " + repr(e))

# 3b) valid config with NO escalate role → ESCALATE_STATUS reports unconfigured, not an error
try:
    yml = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yml.write(textwrap.dedent("""
        providers:
          local-ollama: {kind: ollama, cost_tier: free, model: qwen3.5:9b-agentos}
        roles:
          floor: local-ollama
    """))
    yml.close()
    b = load_brain(yml.name)
    check("no-escalate-role → ESCALATE_STATUS.configured is False", b.ESCALATE_STATUS["configured"] is False)
    check("no-escalate-role → ESCALATE_STATUS.provider is None", b.ESCALATE_STATUS["provider"] is None)
except Exception as e:
    check("no-escalate-role load", False); print("    " + repr(e))

# 3c) no providers.yaml at all → ESCALATE_STATUS reports unconfigured (legacy env-only mode)
try:
    b = load_brain()
    check("no-config → ESCALATE_STATUS.configured is False", b.ESCALATE_STATUS["configured"] is False)
except Exception as e:
    check("no-config ESCALATE_STATUS", False); print("    " + repr(e))

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


# ── ABSENT vs BROKEN (2026-09-02) ────────────────────────────────────────────────────────
# `os.path.exists()` answers False for a DANGLING SYMLINK, and /etc/agent-os/providers.yaml is
# a store symlink on every open build. So a GC'd or half-switched target used to read as "no
# config at all": the brain degraded silently to legacy OLLAMA_MODEL and the spend-gated
# escalate role vanished without a word. Separately, an UNREADABLE file raised PermissionError
# straight past `except ProviderConfigError` and crash-looped the brain at import with no
# reason printed. Absence is legitimate (sealed imports no escalate module and runs floor-only),
# so absence must stay quiet — but broken must be as loud as malformed.
import contextlib, io, stat, tempfile

_d = tempfile.mkdtemp()

# PERMITTING ARM — without it, a brain that refused to start on EVERYTHING would pass the two
# negative arms below for entirely the wrong reason.
try:
    b = load_brain()
    check("ABSENT config still starts quietly on the legacy path (sealed runs floor-only)",
          b.ACTIVE_PROVIDER == "env-default")
except SystemExit:
    check("ABSENT config still starts quietly on the legacy path (sealed runs floor-only)", False)

_dang = os.path.join(_d, "dangling.yaml")
os.symlink(os.path.join(_d, "gone.yaml"), _dang)
_err = io.StringIO()
try:
    with contextlib.redirect_stderr(_err):
        load_brain(providers_yaml=_dang)
    check("a DANGLING config symlink refuses to start (not a silent legacy fallback)", False)
except SystemExit as e:
    check("a DANGLING config symlink refuses to start (not a silent legacy fallback)", e.code == 1)
    check("the dangling-symlink refusal says WHY, naming the path",
          "broken symlink" in _err.getvalue() and _dang in _err.getvalue())

_unread = os.path.join(_d, "unreadable.yaml")
open(_unread, "w").write("providers:\n  a: {kind: ollama, cost_tier: floor, model: m}\nroles: {floor: a}\n")
os.chmod(_unread, 0)
if os.access(_unread, os.R_OK):
    # Running as a user that bypasses the mode bits (root in a container). Say so out loud —
    # a silently-skipped arm is indistinguishable from a passing one, which is the whole
    # failure mode this battery exists to prevent.
    print("  SKIP unreadable-config arm: this user bypasses mode 0000 (uid=%d)" % os.getuid())
else:
    _err = io.StringIO()
    try:
        with contextlib.redirect_stderr(_err):
            load_brain(providers_yaml=_unread)
        check("an UNREADABLE config refuses cleanly (no PermissionError traceback)", False)
    except SystemExit as e:
        check("an UNREADABLE config refuses cleanly (no PermissionError traceback)", e.code == 1)
        check("the unreadable refusal is a provider config error, not a raw OSError",
              "cannot read config" in _err.getvalue())
    except PermissionError:
        check("an UNREADABLE config refuses cleanly (no PermissionError traceback)", False)

print("wiring-battery: " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
