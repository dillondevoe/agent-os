#!/usr/bin/env python3
# Agent OS local brain — WITH HANDS + CONTEXT ANTENNA.
# Talk to it; it acts (browse, run commands, arrange windows) AND it knows the real NOW.
import json, re, subprocess, sys, urllib.request, urllib.error, datetime, os, hashlib, time, threading, shutil, contextlib, platform, glob

# ── UX v2 slice 1: INPUT LOCK (rabbot-to-page-P2-ux-v2-spec 2026-08-02, Dillon msg 9315) ──
# prompt_toolkit PromptSession + patch_stdout = a bottom input line that background output
# (warmup greeting, any threaded print) can never scroll away or swallow — text prints
# ABOVE the prompt instead of through it. Minimal-diff route per the spec: the existing
# print-based streamer is untouched; only the REPL's input() is swapped. TTY-only guard:
# pipes / --once / a missing prompt_toolkit all fall back to the plain input() loop, so
# non-tty callers never see TUI escape codes and the genesis env stays optional in dev.
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.formatted_text import ANSI
    _PTK = sys.stdin.isatty() and sys.stdout.isatty()
except ImportError:
    _PTK = False

# ── MODEL / PROVIDER WIRING (Phase 1.5 slice 2, K6, task 287) ──
# Route the active brain model through the provider config's `floor` role instead of a
# bare OLLAMA_MODEL env default. The floor provider is the offline guarantee (Geist
# spine: the OS must always have a local floor) — providers.py enforces that `floor` is
# required. The cloud `escalate` role is NOT wired here yet; summon_claude remains the
# cloud path (a later slice connects escalate). Missing providers.yaml → legacy env-only
# behavior. A PRESENT-BUT-INVALID yaml fails LOUD (a broken provider config is a real
# error, not a silent degrade — mirrors the genesis-lock "refuse, don't repoint" rule).
OLLAMA="http://127.0.0.1:11434/api/chat"
try:
    from providers import load_providers as _load_providers, resolve as _providers_resolve, \
        ProviderConfigError as _ProviderConfigError
except Exception:
    _load_providers = _providers_resolve = None
    class _ProviderConfigError(Exception): pass

# The two CUMULATIVE spend ceilings (Rabbot's GO, 2026-08-31). Imported the same optional
# way as providers, with one asymmetry that matters: if the module is MISSING while a
# ceiling is CONFIGURED, that is not a degrade to "no ceiling" — it is a safety stage that
# cannot run, and _spend_gate() below turns it into a refusal. An absent guard must never
# read as an absent need for one.
try:
    import spend_ceiling as _spend
except Exception:
    _spend = None

_PROVIDERS_PATH = os.environ.get("AGENT_OS_PROVIDERS", "/etc/agent-os/providers.yaml")
_PROVIDERS = None
# THREE states, not two. `os.path.exists()` alone answers False for a DANGLING SYMLINK, and
# /etc/agent-os/providers.yaml is a symlink into the nix store on every open build — so a
# GC'd or half-switched target used to read as "no config at all" and degrade silently to the
# legacy OLLAMA_MODEL path, taking the spend-gated escalate role with it and saying nothing.
# Absence is LEGITIMATE on sealed (which imports no escalate module and is meant to run
# floor-only), so absence must stay quiet; a BROKEN link is a deployment fault and must be as
# loud as a malformed config. Visible starvation beats invisible absence.
_dangling = (not os.path.exists(_PROVIDERS_PATH)) and os.path.islink(_PROVIDERS_PATH)
if _dangling:
    sys.stderr.write(
        f"\n\033[1;31m⛔ provider config path is a broken symlink: {_PROVIDERS_PATH} "
        f"— that is a broken deployment, not an absent config. I am not starting.\033[0m\n")
    sys.exit(1)
if os.path.exists(_PROVIDERS_PATH):
    if _load_providers is None:
        sys.stderr.write("\n\033[2m⚠ provider config present but providers.py unavailable — falling back to OLLAMA_MODEL\033[0m\n")
    else:
        try:
            _PROVIDERS = _load_providers(_PROVIDERS_PATH)
        except _ProviderConfigError as _e:
            sys.stderr.write(f"\n\033[1;31m⛔ provider config error: {_e} — I am not starting.\033[0m\n")
            sys.exit(1)

def _floor_model():
    # floor role resolves to the local ollama provider; its `model:` key (if set) wins,
    # else the OLLAMA_MODEL env default (unchanged prior behavior). escalate (cloud) is
    # a later slice — for now the floor is the only model this brain serves.
    # Third return value is the provider's `kind` (a required providers.yaml field) —
    # the key the transport seam below dispatches on. No-config → "ollama", because the
    # legacy env-default path has always meant a local ollama at OLLAMA.
    env_model = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")
    if not _PROVIDERS:
        return env_model, "env-default", "ollama"
    name, cfg, _degraded = _providers_resolve(_PROVIDERS, "floor")
    return cfg.get("model", env_model), name, cfg.get("kind", "ollama")

MODEL, ACTIVE_PROVIDER, ACTIVE_PROVIDER_KIND = _floor_model()

# Declared HERE, above _escalate_status, and not down with the consent block where it used to
# live: _escalate_status() is CALLED at module level a few lines below, and it now resolves
# through this set. Defined later, that call raises NameError at import and the brain
# crash-loops under brain-home's `while :;` — which is exactly what it did on the Dell at
# 18:53Z on 2026-09-01, live, until this line moved.
_ESCALATE_UNAVAILABLE = set()   # escalate providers rate-limited/unreachable THIS session

def _escalate_status():
    # Config-only escalate-role RESOLUTION, distinct from and much smaller than the
    # deferred Anthropic shim (task 287, 2026-08-13 assessment): this answers "is a
    # cloud escalate provider configured and named" without touching chat_stream's
    # wire protocol at all — summon_claude remains the sole way a turn actually
    # reaches cloud Claude (explicit user consent required). Never used to auto-route
    # a turn; purely a status signal for future wiring / diagnostics.
    if not _PROVIDERS:
        return {"configured": False, "provider": None, "reason": "no providers.yaml"}
    roles = _PROVIDERS.get("roles", {})
    if "escalate" not in roles:
        return {"configured": False, "provider": None, "reason": "no escalate role in providers.yaml"}
    # Resolve through _ESCALATE_UNAVAILABLE, not around it. Without the argument this
    # answers from config alone and reports "available" for a provider the startup
    # preflight or an in-session 429 has already marked unusable — the status surface
    # then contradicts the router standing next to it.
    name, cfg, degraded = _providers_resolve(_PROVIDERS, "escalate",
                                             unavailable=frozenset(_ESCALATE_UNAVAILABLE))
    return {"configured": not degraded, "provider": name, "reason": None if not degraded else "escalate unavailable, degraded to floor"}

ESCALATE_STATUS = _escalate_status()

# ── ESCALATE CONSENT + PER-TURN ROUTE (task 323, Geist ruling 2026-08-22) ──
# "Escalation to a metered cloud provider is a human act, never an inference." The
# `escalate` role being CONFIGURED (ESCALATE_STATUS above) means the capability exists;
# it does not arm it. Nothing here consults difficulty, model confidence, or turn length
# — there is deliberately no heuristic that can route a turn to a metered provider.
#
# Two consent sources, and only two:
#   turn    — an explicit operator act this turn (`:escalate <msg>` at the prompt), the
#             same class of act as saying yes to a summon_claude offer. One-shot.
#   session — the operator granted it at session start (AGENT_OS_ESCALATE_CONSENT=session).
#             Expires with the process; shown as armed in the status surface.
# `always` is NOT offered in this phase and is refused loudly rather than silently
# downgraded — a config that thinks it armed spending forever, and didn't, is worse than
# one that fails to start.


def _preflight_escalate_secret():
    """Mark an escalate provider UNAVAILABLE at startup when its secret cannot be resolved.

    Without this, a missing key is discovered in the WRONG PLACE. _route_for_turn resolves
    happily (it inspects roles and availability, never the secret), returns role=escalate with
    `degraded: None`, and the RuntimeError does not surface until _resolve_secret runs inside
    _anthropic_stream_events — which is a GENERATOR, so it does not even raise when called, only
    when first consumed, from inside the transport. chat_stream_safe's degrade handler catches
    (TimeoutError, URLError, ConnectionError) and RuntimeError is none of those, so it escapes
    the turn loop: the brain dies and brain-home's `while :;` restarts it, taking the session
    with it. Measured on the Dell 2026-09-01 before this existed, which is why it exists.

    The contract this restores is the one the rest of the escalate path already holds: an
    escalate provider we cannot use is UNAVAILABLE, and an unavailable escalate degrades to the
    local floor with a VISIBLE reason — never a crash, and never a spill to another metered
    provider. Resolution failure is a config fact, knowable at startup; discovering it mid-turn
    was only ever an accident of where the lookup happened to live.

    Reads the secret to prove it RESOLVES and throws the value away — the key is never retained,
    logged, or included in the reason string.
    """
    roles = (_PROVIDERS or {}).get("roles") or {}
    wanted = roles.get("escalate")
    if not wanted:
        return None
    cfg = (_PROVIDERS or {}).get("providers", {}).get(wanted, {})
    ref = cfg.get("api_key_ref")
    if not ref:
        _ESCALATE_UNAVAILABLE.add(wanted)
        return f"escalate provider {wanted!r} has no api_key_ref"
    try:
        _resolve_secret(ref)
    except Exception as e:
        # The message names the REF (a path or an env var name), never the secret: the ref is
        # exactly the remedy the operator needs and is not itself sensitive.
        _ESCALATE_UNAVAILABLE.add(wanted)
        return f"escalate key unresolved ({e}) — escalate inert, answering on the local floor"
    return None

class _EscalateConsent:
    def __init__(self, raw=None):
        v = (raw if raw is not None else os.environ.get("AGENT_OS_ESCALATE_CONSENT", "")).strip().lower()
        self.rejected_always = (v == "always")
        self.session = (v == "session")
        self._turn = False
    def arm_turn(self):
        self._turn = True
    def consume(self):
        """Consent source for THIS turn, or None. A per-turn grant is one-shot: reading it
        disarms it, so the turn after an escalated turn falls back to floor by default."""
        if self._turn:
            self._turn = False
            return "turn"
        return "session" if self.session else None
    def armed(self):
        return "session" if self.session else None

CONSENT = _EscalateConsent()

# ── summon_claude consent, IN CODE (Rabbot door (i), 2026-09-02) ──────────────────────
# THE HOLE THIS CLOSES. `summon_claude` spawns the operator's Claude CLI as a subprocess:
# their account, their permissions, no broker, no uid split. Until now the ONLY thing
# between a model-emitted tool call and that subprocess was prompt text — the tool
# description saying "call ONLY after the user has explicitly said yes", and one line in
# SYS_BASE. The structural guard that exists (the 3B front door cannot reach do_tool at
# all — kick wall, PR #64) protects a different path; on the 9B side the gate was prose.
#
# A CONTROL THAT LIVES ONLY IN PROSE IS NOT A CONTROL. This tree has paid for that lesson
# in the debounce marker, in `gh pr merge --auto`, and in a lint that documented a scope it
# did not have. Consent asserted by the model is the model deciding it has consent.
#
# So the grant is armed ONLY from the REPL input path, by the operator typing `:summon` —
# the same shape as `:escalate`, and reachable by nothing the model emits. It is one-shot
# and it expires, because a grant that outlives the exchange that motivated it is a
# standing permission nobody remembers giving.
#
# DELIBERATELY ONE FUNCTION. `ok_to_summon()` is the whole gate, so door (ii) — routing
# run_command + summon_claude + fetch_web through the broker as one uid-split slice — can
# take ownership of it without unpicking anything. (i) does not preclude (ii).
_SUMMON_GRANT_TTL_S = 300  # ~ a few turns at this box's cadence; a stale yes is not a yes

class _SummonConsent:
    def __init__(self, ttl=_SUMMON_GRANT_TTL_S):
        self._at = None
        self._ttl = ttl
        self._spent_at = None   # the stamp the last consume took, so restore() can put THAT back
        self._restores = 0      # how many times THIS grant has been given back (see _MAX)
    def arm(self):
        """Called ONLY from the operator's own input line. Never from a tool, a model
        response, or anything parsed out of model output."""
        self._at = time.time()
        self._spent_at = None
        self._restores = 0
    def check_and_consume(self, now=None):
        """(True, None) if a summon may proceed, else (False, reason). Single-use: a
        successful check disarms the grant, so one `:summon` buys exactly one ANSWER — and
        at most `_SUMMON_MAX_RESTORES` further attempts that produced no answer at all."""
        now = time.time() if now is None else now
        if self._at is None:
            return False, "no operator consent — summon_claude is cloud and uses the user's account"
        age = now - self._at
        if age > self._ttl:
            self._at = None
            return False, f"consent expired ({int(age)}s > {self._ttl}s) — ask again"
        self._spent_at = self._at
        self._at = None
        return True, None
    def restore(self, now=None):
        """Put back a grant that was consumed by an attempt which never produced an answer.

        THE RULE: A GRANT BUYS AN ANSWER, NOT AN ATTEMPT. The operator's `:summon` pays for
        one reply from cloud Claude; a CLI that is absent, unauthenticated, timed out or
        errored spent nothing on their account and returned nothing to them, so charging the
        grant for it destroys a consent act in exchange for an error string. The observed
        shape on this OS: `claude-code` is in the closure but auth is per-user OAuth, so a
        box where nobody has run `claude` once fails EVERY summon this way — the operator
        types `:summon`, gets "isn't logged in", and must re-consent for each retry.

        THE CLOCK IS NOT REFRESHED, and that is the whole safety property: this restores the
        ORIGINAL stamp, so a restored grant expires exactly when the operator's act said it
        would. Restoring with `now` would mint consent-time nobody granted, and a loop of
        failing attempts could then hold a grant open indefinitely. Restore can only ever
        give back a grant that was armed from the `:summon` input line (arm() is the sole
        writer of `_at`, asserted structurally by arm F of the battery) — it can never
        manufacture one, and it cannot outlive the TTL.

        BOUNDED TWICE, and the second bound is not the clock. The TTL says how long a grant
        may live; `_SUMMON_MAX_RESTORES` says how many answerless attempts may be made inside
        it. Without the second, a CLI that fails in milliseconds turns one consent act into an
        unbounded run of real subprocesses — the TTL never expires early enough to stop it.
        """
        now = time.time() if now is None else now
        if self._spent_at is None:
            return False
        # THE MESSAGE MUST AGREE WITH THE CLOCK IT OWNS (Augur, #278 review). Returning True
        # here without consulting the clock makes `_kept()` tell the operator their `:summon`
        # is still good when the very next check will refuse it as expired. It is the defect
        # removed one section above ("nothing was spent") applied one line further — except
        # that spend is UNOBSERVABLE to this code while expiry is ENTIRELY INTERNAL, so this
        # half was always ours to check rather than assert. Not exotic either: the subprocess
        # timeout is 180s against a 300s TTL, so every summon consumed after t=120 that times
        # out lands exactly here.
        if now - self._spent_at > self._ttl:
            self._spent_at = None
            return False
        if self._restores >= _SUMMON_MAX_RESTORES:
            self._spent_at = None
            return False
        self._restores += 1
        self._at, self._spent_at = self._spent_at, None
        return True
    def armed(self, now=None):
        now = time.time() if now is None else now
        return self._at is not None and (now - self._at) <= self._ttl

SUMMON_CONSENT = _SummonConsent()

def ok_to_summon(consent=None, now=None):
    """THE gate. The deployed path and the battery both call this — no fixture-only route.

    (#256's lesson: an arm that exercises a different code path than the box runs is an arm
    that proves nothing about the box.)"""
    return (consent or SUMMON_CONSENT).check_and_consume(now=now)

def restore_summon_grant(consent=None, now=None):
    """Counterpart to ok_to_summon, and reached by the deployed path for the same reason.

    Called ONLY when a consumed summon produced no answer. See _SummonConsent.restore for
    why this cannot manufacture or extend consent."""
    return (consent or SUMMON_CONSENT).restore(now=now)

# Appended to every summon failure that gave the operator nothing, so the surviving grant is
# stated rather than left for them to discover by guessing. Silence here would be the same
# defect one level down: a consent act whose fate only the code knows.
# DELIBERATELY SAYS NOTHING ABOUT SPEND. The first draft read "nothing was spent, so you can
# retry" — an unconditional claim about the operator's cloud ACCOUNT, made by code that cannot
# observe it. On the FileNotFoundError path it is true; on the 180s-timeout path it is close to
# the opposite, since a timeout means the request was in flight and most likely billed. The
# grant and the billing are two different facts and only one of them is ours to report: the
# restore is about the CONSENT ACT, which is genuinely intact because no answer came back.
# A RESTORE BUDGET, because "give the grant back on every failure" is unbounded on its own.
# The TTL bounds how LONG a grant lives; nothing bounded how many subprocesses could be spawned
# inside it. A fast-failing `claude` (rate limit, transient network error) returns in
# milliseconds, so a model could loop summon_claude dozens of times in one 300s window, each
# iteration a real process. Worse, `_SUMMON_KEPT` is a TOOL RESULT — it is read by the model,
# the party this gate exists to constrain, not by the operator. So the retry affordance is
# finite: three attempts that produced no answer, then the operator consents again.
_SUMMON_MAX_RESTORES = 3

_SUMMON_KEPT = " (Your `:summon` is still good — this attempt returned no answer, so you can retry without re-consenting.)"

def _log_summon_attempt(allowed, reason=None):
    """A refused summon is LOUD. A legitimate summon blocked by this gate must show up as a
    defect in the record, not as a silence someone has to notice the absence of."""
    try:
        os.makedirs(os.path.dirname(_TURN_LOG_PATH), exist_ok=True)
        with open(_TURN_LOG_PATH, "a") as f:
            f.write(json.dumps({
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "event": "summon_claude",
                "allowed": bool(allowed),
                "reason": reason,
            }) + "\n")
    except Exception:
        pass

if CONSENT.rejected_always:
    sys.stderr.write("\n\033[1;31m⛔ AGENT_OS_ESCALATE_CONSENT=always is not offered in this phase "
                     "(Geist ruling 2026-08-22) — use `session`, or grant per turn with `:escalate`.\033[0m\n")
    sys.exit(1)

def _floor_route(degraded=None):
    return {"role": "floor", "provider": ACTIVE_PROVIDER, "model": MODEL,
            "kind": ACTIVE_PROVIDER_KIND, "consent_source": None, "degraded": degraded}

def _spend_gate():
    """(True, None) if a metered turn may run, else (False, reason). Never raises.

    Fail-CLOSED in both directions the contract names: a ceiling that is configured but
    cannot be evaluated refuses, and a spend_ceiling module that is missing while a ceiling
    is configured refuses. The only path that returns True without consulting a counter is
    the one where NO ceiling is configured at all."""
    configured = any(os.environ.get(k, "").strip() for k in
                     ("AGENT_OS_SPEND_DAY_TOKENS", "AGENT_OS_SPEND_CUMULATIVE_TOKENS"))
    if not configured:
        return True, None
    if _spend is None:
        return False, "spend ceiling UNAVAILABLE — a ceiling is configured but spend_ceiling.py could not be imported"
    try:
        return _spend.check()
    except Exception as e:
        # check() is written not to raise; this is the belt to that suspenders. An
        # unexpected exception here is still a safety stage that did not run.
        return False, f"spend ceiling UNAVAILABLE — {e}"

def _route_for_turn(consent_source=None):
    """Resolve which provider answers this turn. No consent → floor, unconditionally.

    Never-spill (spec rule, 2026-08-06 overnight-bleed scar): if the escalate provider is
    unavailable this session we degrade to the LOCAL FLOOR and say so — never to another
    metered provider. providers.resolve() enforces that; the `name != wanted` check below is
    what turns its degrade into a visible one rather than a silent re-route."""
    if not consent_source:
        return _floor_route()
    roles = (_PROVIDERS or {}).get("roles") or {}
    if "escalate" not in roles:
        return _floor_route("no escalate role configured — answering on the local floor")
    wanted = roles["escalate"]
    name, cfg, _degraded = _providers_resolve(_PROVIDERS, "escalate",
                                              unavailable=frozenset(_ESCALATE_UNAVAILABLE))
    if name != wanted:
        return _floor_route(f"escalate provider {wanted!r} unavailable — degraded to the local "
                            f"floor (never to another metered provider)")
    ok, why = _spend_gate()
    if not ok:
        # NEVER-SPILL when the ceiling trips: the local floor, not a cheaper metered
        # provider. A budget that reroutes to something else that costs money has not
        # stopped spending, it has renamed it.
        return _floor_route(why + " — answering on the local floor")
    return {"role": "escalate", "provider": name, "model": cfg.get("model", MODEL),
            "kind": cfg.get("kind", "claude"), "consent_source": consent_source, "degraded": None}

def escalate_status_line():
    """One line for the status surface: is escalation armed, and on what."""
    if not ESCALATE_STATUS["configured"]:
        return "escalate: not configured (" + (ESCALATE_STATUS["reason"] or "unavailable") + ")"
    armed = CONSENT.armed()
    if armed:
        return f"escalate: ARMED for this session → {ESCALATE_STATUS['provider']} (metered cloud; every escalated turn prints its cost)"
    return f"escalate: unarmed → {ESCALATE_STATUS['provider']} available; `:escalate <msg>` sends ONE turn to the cloud"

# ── COST-CAP BREAKER (HARNESS-MAP guardrail 3) ──────────────────────────────────
# Hard per-turn ceilings on the tool loop: model hops (chat calls) and cumulative
# output tokens. A runaway tool loop is a cost event on a metered provider and a
# latency event on the 3 tok/s floor — either way the turn must HALT LOUDLY, never
# spin quietly. Config comes from providers.yaml's optional `limits:` block
# (validated in providers.py — invalid fails boot, same as the rest of that file);
# the env vars below serve the legacy no-yaml path. Precedence: yaml > env > default.
# Defaults preserve prior behavior exactly: 6 hops (the old hard literal) and no
# token ceiling — the breaker only bites where a config turns it on, except that
# hop exhaustion, which was always enforced, now REPORTS instead of ending silently.
def _turn_limits():
    limits = (_PROVIDERS or {}).get("limits") or {}
    def _pick(yaml_key, env_key, default):
        if yaml_key in limits:
            return limits[yaml_key]
        v = os.environ.get(env_key, "").strip()
        if not v:
            return default
        try:
            n = int(v)
            if n <= 0: raise ValueError
            return n
        except ValueError:
            # Same refuse-don't-repoint rule as a present-but-invalid providers.yaml:
            # a garbled cost ceiling is a real config error, not a silent default.
            sys.stderr.write(f"\n\033[1;31m⛔ {env_key} must be a positive integer, got {v!r} — I am not starting.\033[0m\n")
            sys.exit(1)
    return (_pick("max_hops_per_turn", "AGENT_OS_MAX_TURN_HOPS", 6),
            _pick("max_output_tokens_per_turn", "AGENT_OS_MAX_TURN_TOKENS", None))

MAX_TURN_HOPS, MAX_TURN_TOKENS = _turn_limits()

# ── PER-TURN PROVENANCE LOG (Phase 1.5A acceptance criterion 5, task 287 slice 3) ──
# "Audit log shows provider+model per turn." Deliberately NOT bin/audit's chain-hashed
# broker log — that log is a tamper-evident record of capital/tool-execution security
# decisions (DENY/ALLOW-AUTO/REQUIRE-CONFIRM); routine per-turn provenance is a different
# class of record and doesn't belong mixed into it. Plain append-only JSONL instead, same
# home-relative-with-env-override convention as bin/mem's MEM_ROOT.
_TURN_LOG_PATH = os.environ.get("AGENT_OS_TURN_LOG", os.path.join(os.path.expanduser("~/memory"), "turn-log.jsonl"))

def _log_turn_provenance(route=None, tokens=None):
    # Scope: the 7B `turn()` loop only (the actual acting/tool-wielding brain) — the 3B
    # frontdoor is a separate, not-yet-provider-routed model (MODEL_3B) whose turns always
    # either answer directly or kick to turn(), which then logs. Never let this break a
    # live turn: log failure is swallowed, not surfaced.
    #
    # `role` + `consent_source` are task 323's acceptance criterion 5: an escalated turn is
    # only legible after the fact if the record says WHY it was allowed to spend. A floor
    # turn logs consent_source: null, which is the honest value — no consent was needed.
    # `tokens` is output tokens as reported by the transport, or null when the transport
    # reported none (an unknown count is written as null, never as 0 — a 0 would read as a
    # free turn).
    route = route or _floor_route()
    try:
        os.makedirs(os.path.dirname(_TURN_LOG_PATH), exist_ok=True)
        with open(_TURN_LOG_PATH, "a") as f:
            f.write(json.dumps({
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "provider": route["provider"],
                "model": route["model"],
                "role": route["role"],
                "consent_source": route["consent_source"],
                "tokens": tokens,
            }) + "\n")
    except Exception:
        pass

# Substituted at build by genesis-open.nix from environment.variables.OLLAMA_THINK — the
# SAME attrset the login env is built from, so there is one spelling and it cannot drift.
# Left as the literal placeholder in a bare dev checkout, which _think_budget() detects.
_THINK_BUILD_DEFAULT = "@THINK_DEFAULT@"

def _think_budget():
    # OLLAMA_THINK: think-budget control for thinking models on the ~3 tok/s CPU box
    # (spec 2026-08-05 item b). off/false/0 → no thinking (fastest replies);
    # low/medium/high → per-level budget where the model supports it; on/true/1 → full.
    # Unset or UNRECOGNISED → fall back to the BUILD-TIME default; if that is absent too, omit.
    #
    # WHY A BUILD-TIME DEFAULT AND NOT JUST THE ENV (2026-08-31, Dillon's photo of the TUI
    # still thinking for 81s with OLLAMA_THINK=off already deployed). `environment.variables`
    # builds the LOGIN environment. The TUI is launched by `systemd --user` (brain-home.service,
    # commit baac2a3) which does not source /etc/set-environment, so the shipped value reached
    # the login shell I probed and NOT the process that reads it. Baking it into the script
    # makes it hold in EVERY launch context — systemd user unit, login shell, cron, a bare
    # exec — because there is no inheritance step left to lose it.
    #
    # The env still WINS when set, deliberately: `OLLAMA_THINK=on` in the session stays the
    # zero-rebuild rollback. This is a default, not a seam pin — unlike broker/confirm/taint,
    # where an inherited value is an attack surface and the wrapper must be authoritative.
    #
    # AN UNRECOGNISED VALUE FALLS BACK TO THE BAKED DEFAULT, it does not return None. Returning
    # None omits the key and restores the MODEL's default, i.e. thinking ON — so before this,
    # `OLLAMA_THINK=disbaled` in a session was indistinguishable from never setting it and
    # silently undid the whole fix. A typo must degrade to the shipped value, never past it.
    def parse(v):
        v = (v or "").strip().lower()
        if v in ("off","false","0","no"):    return False
        if v in ("on","true","1","yes"):     return True
        if v in ("low","medium","high"):     return v
        return None                          # unrecognised OR empty — not a value

    got = parse(os.environ.get("OLLAMA_THINK"))
    if got is not None:
        return got
    # An unsubstituted placeholder is NOT a value either: without this test a bare dev checkout
    # would parse "@think_default@" to None and land on the model's default, which is the exact
    # silent-ON outcome this change exists to remove and is invisible in a correct-looking build.
    d = _THINK_BUILD_DEFAULT.strip().lower()
    return None if d.startswith("@") else parse(d)
THINK=_think_budget()

# ── R1 CONTEXT BOUND (tier-0 item 3, Rabbot's runtime-config lane) ────────────
# The 5440 runs this brain on CPU at ~3 tok/s. Ollama's default context is small
# enough that a long session silently slides the front of the conversation out of
# the window, and a large one costs KV memory the box does not have. Both failure
# modes are INVISIBLE from the prompt: the model simply stops knowing what it was
# told. So the window is stated here rather than inherited, and the history is
# trimmed to fit it on OUR side, where the trim can be made structurally safe.
NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "8192") or 8192)
# Tokens held back for the reply itself — history is never allowed to fill the
# whole window, or the model has room to read and none to answer.
_CTX_RESERVE_TOKENS = 1536
# ~4 chars/token is an ESTIMATE, and it is named as one: we do not have the
# tokenizer here, and shipping a second tokenizer to agree with ollama's would be
# the parallel-surface trap. The estimate is deliberately CONSERVATIVE (real
# English averages closer to 4.5), so the trim errs toward dropping slightly more
# history than strictly necessary rather than overflowing the window.
_CHARS_PER_TOKEN = 4
HIST_BUDGET_CHARS = max(0, (NUM_CTX - _CTX_RESERVE_TOKENS)) * _CHARS_PER_TOKEN

def _msg_chars(m):
    # Cost of a message on the wire, tool calls included — a turn that called five
    # tools carries its arguments into the next request and a content-only measure
    # would score it as free.
    n = len(m.get("content") or "")
    for tc in (m.get("tool_calls") or []):
        n += len(json.dumps(tc))
    return n + 8   # role/JSON framing, order-of-magnitude

def trim_history(msgs, budget=None):
    """Trim `msgs` IN PLACE to fit the context window. Returns the number dropped.

    TWO INVARIANTS, and the second one is the whole reason this is a function and
    not a slice expression:

    1. msgs[0] (the system message) is NEVER dropped. It is also the byte-identical
       KV-cache prefix (see main()), so dropping it would be a cache miss on top of
       a lobotomy.

    2. THE HISTORY IS ONLY EVER CUT AT A `user` MESSAGE. An assistant message
       carrying `tool_calls` and the `tool` messages answering it are ONE
       indivisible group on the wire: drop the assistant half and the tool results
       become orphans referring to a call that is no longer in the transcript.
       Ollama tolerates it unevenly and the Anthropic transport (_anthropic_-
       translate_messages) does not tolerate it at all — a tool_result block with
       no matching tool_use is an API-level 400. A naive "drop the oldest k
       messages" trimmer produces exactly that, which is why the battery arms the
       naive version against the same input and asserts it DOES orphan.

    A single user group larger than the whole budget is kept anyway: there is no
    correct smaller answer, and truncating the user's own words to fit is a worse
    failure than letting the server window it.
    """
    if budget is None: budget = HIST_BUDGET_CHARS
    if len(msgs) < 2: return 0
    # Legal cut points, newest first: indices >0 where a user turn begins.
    cuts = [i for i in range(1, len(msgs)) if msgs[i].get("role") == "user"]
    if not cuts: return 0
    head = _msg_chars(msgs[0])
    # Walk cut points from the NEWEST backwards, keeping the longest suffix that fits.
    best = cuts[-1]
    for i in reversed(cuts):
        if head + sum(_msg_chars(m) for m in msgs[i:]) <= budget:
            best = i
        else:
            break
    if best <= 1: return 0
    dropped = best - 1
    del msgs[1:best]
    return dropped
MODEL_3B="qwen2.5:3b-augur"  # front-door (model-3b-open.nix); absent → front-door bypasses to the 9B main brain

# ── THE SOUL (genesis lock, Geist ruling "bind not bytes") ─────────────────────
# These two are BUILD-TIME LITERALS. genesis-open.nix substitutes @GENESIS_PATH@ with
# the content-hashed store path (${genesis}/GENESIS.md) and @GENESIS_SHA256@ with that
# file's sha256. Baked into the program = no env var, no runtime symlink, nothing a
# running system can repoint. Changing the soul then requires rebuilding the brain — the
# doc's own "deliberate rebuild" carve. Until substituted (hand-deployed dev), we're unlocked.
GENESIS_PATH="@GENESIS_PATH@"
GENESIS_SHA256="@GENESIS_SHA256@"
LOCKED=not GENESIS_PATH.startswith("@")   # substituted by nix == the locked build

def _refuse(reason):
    # the doc's last paragraph, compiled into code: a bad soul = an attack; load nothing else.
    sys.stderr.write("\n\033[1;31m⛔ "+reason+" — I am not starting.\033[0m\n")
    sys.exit(1)

def load_soul():
    path = GENESIS_PATH if LOCKED else "/etc/agent-os/GENESIS.md"  # dev reads on-disk if present
    try:
        text=open(path,encoding="utf-8").read()
    except OSError:
        if LOCKED: _refuse("my soul is missing where it was baked to be")
        return None  # dev, no soul on disk → run unlocked (a dev convenience, never the security claim)
    if LOCKED and hashlib.sha256(text.encode("utf-8")).hexdigest()!=GENESIS_SHA256:
        _refuse("my soul does not hash to what was baked in — treating this as tampering")
    return text

SOUL=load_soul()   # read ONCE at startup, before anything else

TOOLS=[
 {"type":"function","function":{"name":"open_url","description":"Open a website in the browser (tiles into the desktop). Use when the user wants to SEE or interact with a site — browse, shop, watch, use a web app.","parameters":{"type":"object","properties":{"url":{"type":"string","description":"full https URL"}},"required":["url"]}}},
 {"type":"function","function":{"name":"run_command","description":"Run a shell command on THIS computer and return its output. Use to check, list, inspect, create, or change things on the machine.","parameters":{"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}}},
 {"type":"function","function":{"name":"arrange_windows","description":"Rearrange the desktop workspace. action is one of: 'close' (close the focused window), 'fullscreen' (toggle fullscreen on focused window), 'cycle' (focus the next window), 'split' (toggle vertical/horizontal split for the next window).","parameters":{"type":"object","properties":{"action":{"type":"string"}},"required":["action"]}}},
 {"type":"function","function":{"name":"calendar.agenda","description":"List the user's upcoming REAL calendar events. Use whenever they ask what's on their calendar / schedule / coming up / today / this week.","parameters":{"type":"object","properties":{"days":{"type":"integer","description":"days ahead to show (default 7)"}}}}},
 {"type":"function","function":{"name":"calendar.add","description":"Add a REAL event to the user's calendar. Use when they want to schedule/add/create/remember an appointment or event.","parameters":{"type":"object","properties":{"start":{"type":"string","description":"start time as 'YYYY-MM-DD HH:MM' (24h, local)"},"summary":{"type":"string","description":"the event title"},"end":{"type":"string","description":"optional end time 'YYYY-MM-DD HH:MM'; defaults to +1h"}},"required":["start","summary"]}}},
 {"type":"function","function":{"name":"calendar.now","description":"Get the exact current date/time from the calendar (station timezone).","parameters":{"type":"object","properties":{}}}},
 {"type":"function","function":{"name":"calendar.cals","description":"List the user's calendar collections.","parameters":{"type":"object","properties":{}}}},
 {"type":"function","function":{"name":"calculator","description":"Evaluate a math expression (arithmetic, %, units, functions). Use for any calculation.","parameters":{"type":"object","properties":{"expression":{"type":"string","description":"e.g. (2+3)*4, sqrt(2), 200*15%, 5 km + 300 m"}},"required":["expression"]}}},
 {"type":"function","function":{"name":"system","description":"Read or change machine settings, and power the machine off or restart it. action 'status' reports network/audio/display/power; 'volume' sets 0-100 or mute/unmute/toggle; 'brightness' sets 0-100; 'power' takes value 'reboot' or 'poweroff' and DOES IT — use it when the user asks to reboot/restart/shut down.","parameters":{"type":"object","properties":{"action":{"type":"string","description":"status | volume | brightness | power"},"value":{"type":"string","description":"for volume/brightness: 0-100 (or mute/unmute/toggle for volume); for power: reboot | poweroff"}},"required":["action"]}}},
 {"type":"function","function":{"name":"list_files","description":"List the entries (files/folders) in a directory. Use when the user asks what's in a folder.","parameters":{"type":"object","properties":{"dir":{"type":"string","description":"absolute directory path"}},"required":["dir"]}}},
 {"type":"function","function":{"name":"read_document","description":"Extract text from a PDF document — whole doc or one page. Use to read/summarize a PDF the user names.","parameters":{"type":"object","properties":{"path":{"type":"string","description":"path to the .pdf"},"page":{"type":"integer","description":"optional 1-indexed page; omit for whole doc"}},"required":["path"]}}},
 {"type":"function","function":{"name":"media_info","description":"Probe an image/video/audio file (type, format, duration, dimensions, streams). Use to inspect a media file.","parameters":{"type":"object","properties":{"path":{"type":"string","description":"path to the media file"}},"required":["path"]}}},
 {"type":"function","function":{"name":"notes","description":"The user's notes. action 'list' shows all notes newest-first; 'read' returns one note's body (needs slug).","parameters":{"type":"object","properties":{"action":{"type":"string","description":"list | read"},"slug":{"type":"string","description":"for 'read': the note slug"}}}}},
 {"type":"function","function":{"name":"fetch_web","description":"Fetch a public web page and return its readable text (nav/boilerplate stripped). Use to READ what a page says (the inference half of browsing); use open_url instead to show the user a site.","parameters":{"type":"object","properties":{"url":{"type":"string","description":"full http(s) URL"}},"required":["url"]}}},
 {"type":"function","function":{"name":"summon_claude","description":"Bring in cloud Claude for a task beyond the local brain. CLOUD, uses the user's account. Consent is enforced in code, not here: the call is REFUSED unless the operator typed `:summon` at the prompt. Offer a summon and tell them to type `:summon <msg>`; a yes in conversation does not arm it. Never auto-fire.","parameters":{"type":"object","properties":{"task":{"type":"string","description":"what Claude should do, stated completely"},"context_summary":{"type":"string","description":"compact summary of the last ~6 turns relevant to the task — never the whole history, never secrets"}},"required":["task","context_summary"]}}},
]

# ── THE SHELL THE BOX ACTUALLY HAS (P0, 2026-09-05) ───────────────────────────
# `subprocess.run(["bash", ...])` resolves `bash` on PATH; on NixOS there is no `/bin/bash`
# and a login shell's PATH is not the brain service's PATH. On the Dell every run_command
# died `[Errno 2] No such file or directory: 'bash'` — the brain's only hand, broken on the
# OS it ships on, with the failure surfacing as an errno the model then narrated at 20s a
# turn. Resolve ONCE, at import, against what is on this machine; never a hardcoded name.
#
# Order is deliberate: bash first (the probes below use bashisms), sh as the POSIX floor,
# then the two absolute paths that exist on a NixOS system even when PATH is empty. If none
# resolve, SHELL is None and every shell-out reports that as data rather than raising an
# errno the model has to interpret.
#
# BUILD-TIME LITERAL, same discipline as @GENESIS_PATH@ above: genesis-open.nix substitutes
# @SH@ with ${pkgs.bash}/bin/bash, so the LOCKED build carries the store path of the exact
# bash it was built against and asks the machine nothing. Until substituted (hand-deployed
# dev) it starts with "@" and the fallback below runs — running from source must keep working.
# The fallback stays as DEPTH, not as the mechanism: it is a second name-resolution
# assumption, and the whole finding was that name resolution is what failed.
SH_BUILD = "@SH@"

def _resolve_shell(build=None):
    b = SH_BUILD if build is None else build
    # `os.path.exists` and not a bare truth test: a baked path that is not installed is not
    # a shell, and returning it would only move the ENOENT to the first run_command — after
    # the caller has already been told it has a hand.
    if not b.startswith("@") and os.path.exists(b): return b
    for c in ("bash", "sh"):
        p = shutil.which(c)
        if p: return p
    for p in ("/run/current-system/sw/bin/bash", "/run/current-system/sw/bin/sh", "/bin/sh"):
        if os.path.exists(p): return p
    return None
SHELL = _resolve_shell()

def _sh(cmd, timeout):
    """Run `cmd` through the resolved shell. Raises RuntimeError — never FileNotFoundError —
    when no shell exists, so callers report a cause instead of an errno."""
    if not SHELL:
        raise RuntimeError("no shell on this system (tried bash, sh, /run/current-system/sw/bin/*, /bin/sh)")
    return subprocess.run([SHELL, "-c", cmd], capture_output=True, text=True, timeout=timeout)

def live_context():
    # The context antenna: ground the brain in the real NOW, past its training cutoff.
    #
    # ── THE EYES NEED NOTHING THE HAND HAS (Geist's ruling, 2026-09-05) ───────────────
    # This function used to read every machine fact by shelling out — `hostname`, `uptime -p`,
    # `free -h | awk`, `cat /sys/...`. On the Dell's brain-home unit the PATH is five store
    # dirs and NONE of those binaries are on it, so every probe returned "" and the context
    # shipped three lines, silently dropping the one sentence that tells the model it is on
    # NixOS Linux. The #277 fix made that WORSE in shape: loud errnos became a short,
    # confident context that omitted the OS.
    #
    # The file's own remedy — the "blind instrument" NOTE — was keyed on `not SHELL`, the
    # CAUSE it was written from, while the property it protects is the SYMPTOM (the probes
    # read nothing). SHELL resolved, so the note stayed silent while every probe was blind.
    # Re-keying the note onto the symptom would still be a guard over a mechanism that has
    # no business existing: four of these facts are handed to Python for free by the kernel
    # or the interpreter. They shelled out because that is how a human at a prompt gets them.
    #
    # So the mechanism goes, and the note is RETIRED WITH IT rather than re-keyed: nothing
    # below looks up a binary BY NAME except the two probes for which PATH-presence IS the
    # measurement (installed apps) or which are a genuine external instrument (hyprctl).
    # `SHELL`/`_sh` stay — they are `run_command`'s hand, not the eyes.
    #
    # And per-instrument: an unreadable /proc is an absence of INSTRUMENT and SAYS so in its
    # own slot; an absent BAT* dir is an absence of DATA (a desktop) and stays silent.
    lines=[]
    now=datetime.datetime.now().astimezone()
    lines.append("Current date & time: "+now.strftime("%A, %B %d, %Y, %-I:%M %p %Z")+" (this is ground truth — trust it over your training data).")

    # Unconditional: platform.node() reads the kernel's hostname through the interpreter.
    # The OS sentence the model needs most is no longer gated on anything.
    lines.append("Machine: "+platform.node()+" — Agent OS, a NixOS LINUX system (not Windows/macOS; nix installs, systemctl for power).")

    # Battery — absence of DATA, so silent. A desktop has no BAT* and that is a true fact
    # about the machine, not a broken instrument.
    try:
        caps=glob.glob("/sys/class/power_supply/BAT*/capacity")
        if caps:
            with open(caps[0]) as f: bat=f.read().strip()
            bst="unknown"
            try:
                with open(caps[0].rsplit("/",1)[0]+"/status") as f: bst=f.read().strip() or "unknown"
            except OSError: pass
            if bat: lines.append(f"Battery: {bat}% ({bst})")
    except OSError: pass

    # Uptime — absence of INSTRUMENT, so it says so. /proc/uptime is a kernel file; on Linux
    # it being unreadable is a fact worth showing, not one to hide behind an empty string.
    try:
        with open("/proc/uptime") as f: secs=int(float(f.read().split()[0]))
        d,r=divmod(secs,86400); h,r=divmod(r,3600); m=r//60
        lines.append("Uptime: up "+", ".join(p for p in (
            f"{d} day{'s' if d!=1 else ''}" if d else "",
            f"{h} hour{'s' if h!=1 else ''}" if h else "",
            f"{m} minute{'s' if m!=1 else ''}" if m or not (d or h) else "") if p))
    except (OSError, ValueError, IndexError):
        lines.append("Uptime: unavailable (/proc/uptime unreadable)")

    # Memory — same class as uptime.
    try:
        info={}
        with open("/proc/meminfo") as f:
            for ln in f:
                k,_,v=ln.partition(":")
                info[k]=int(v.split()[0])  # kB
        used=info["MemTotal"]-info["MemAvailable"]
        gb=lambda kb: f"{kb/1048576:.1f}Gi"
        lines.append("Memory: "+gb(used)+" used / "+gb(info["MemTotal"])+" total")
    except (OSError, ValueError, KeyError, IndexError):
        lines.append("Memory: unavailable (/proc/meminfo unreadable)")

    # Windows — the ONE genuinely external instrument. Direct argv, never through a shell:
    # the file's own doctrine (see run_command's hyprctl guard) is that a shell here could
    # reach `hyprctl dispatch`. `which` returning None is an absent INSTRUMENT and says so —
    # this line was dead on every Dell boot and nothing showed it.
    hyprctl=shutil.which("hyprctl")
    if not hyprctl:
        lines.append("Open windows right now: unavailable (hyprctl not on this unit's PATH)")
    else:
        try:
            r=subprocess.run([hyprctl,"clients","-j"],capture_output=True,text=True,timeout=4)
            d=json.loads(r.stdout)
            lines.append("Open windows right now: "+("; ".join(
                w.get("class","?")+": "+(w.get("title","")[:40]) for w in d) or "none"))
        except Exception:
            lines.append("Open windows right now: unavailable (hyprctl gave no readable answer)")

    # Installed-app awareness (rabbot-to-page-ADD-to-pack-brain-blindspot 2026-08-01: brain
    # looped `nix profile install steam` into the unfree wall while Steam was already on the
    # box). THE DELIBERATE EXCEPTION: this probe's PATH dependence IS the measurement — "can
    # this brain invoke steam by name from where it stands". The four facts above were
    # VICTIMS of PATH; this one measures it. Same call, opposite meaning.
    # Volatile tail on purpose — keeps the KV-cached static prefix untouched.
    apps=[a for a in ("steam","firefox","thunar","kitty","mpv","libreoffice","gimp") if shutil.which(a)]
    if apps: lines.append("Already-installed apps (RUN these, never re-install): "+" ".join(apps))
    return "\n".join(lines)

# Trimmed for prefill cost (P1 fix #3, rabbot-to-page-P1-UPGRADE-brain-timeout-crash-2026-08-01:
# 2560-token static prefix @ 14 tok/s CPU prompt-processing = ~3min cold boot). Same content,
# denser wording. SOUL itself is genesis-locked and out of scope for Page to trim.
SYS_BASE=("You are Agent OS's local brain — sovereign, private, on-machine, no cloud. "
     "PLATFORM: NixOS Linux, not Windows/macOS — install via nix (e.g. `programs.steam` in system "
     "config, never .exe/.msi), reboot=`systemctl reboot`, shutdown=`systemctl poweroff`, quick tool="
     "`nix profile install nixpkgs#<name>`. Never use Windows/macOS commands. A permanent system "
     "change (installing Steam, a driver, a service) means editing the OS config — say so. "
     "Before installing ANYTHING, `run_command command -v <name>` — if present, RUN it instead "
     "of reinstalling. "
     "You HAVE HANDS: open_url (browser), run_command (shell here), arrange_windows (desktop). "
     "When a tool can do it, CALL IT — don't explain how the user could do it themselves. "
     "BROWSE vs INFERENCE: want to see/read/watch/use something → open_url. Want a quick fact → "
     "answer directly. Unsure → ask. Confirm tool results in one short line. Be concise, be a doer. "
     "Before installing anything, `command -v <name>` — if it's already present, just RUN it. "
     "This system is built from a flake image: editing /etc/nixos/*.nix does NOTHING; permanent "
     "changes happen in the OS repo — say so instead of editing. "
     "SUMMON: when a task is beyond you (deep code work, long documents, hard reasoning), OFFER: "
     "\"this one's beyond me — want me to bring in Claude? [cloud, uses your account]\" and call "
     "summon_claude only after an explicit yes, which the operator gives by typing `:summon` — "
     "a conversational yes does not arm it and the call will be refused. The offer must name that it's cloud.")

def sysmsg():
    # SOUL first, unspoofably (Geist item 3): identity leads, then operational addendum.
    # STATIC ONLY — byte-identical across turns so ollama's KV cache hits on this prefix.
    # Live senses move to the per-turn user message (see user_turn()) so only that small
    # tail re-evaluates each turn instead of busting the whole ~800-token prefix. (P1 fix,
    # rabbot-to-page-P1-agent-brain-promptsplit-streaming-feelgood-2026-08-01: cold rate
    # ~17 tok/s / ~48s per turn when this block changed every turn; cached prefix ~5037 tok/s.)
    parts=[]
    if SOUL: parts.append(SOUL.strip())
    parts.append("--- OPERATING NOTES ---\n"+SYS_BASE)
    return {"role":"system","content":"\n\n".join(parts)}

def user_turn(text):
    # volatile live_context rides the user turn's tail, not the system prompt.
    return {"role":"user","content":text+"\n\n--- LIVE CONTEXT (your senses, refreshed now) ---\n"+live_context()}

CHAT_TIMEOUT_S=600  # was 180 — a cold CPU prefill (~2560 tok @ ~14 tok/s) can take ~3min by
                     # itself; 180s guaranteed a TimeoutError on first boot turn (P1 fix #1/#2,
                     # rabbot-to-page-P1-UPGRADE-brain-timeout-crash-2026-08-01, live-hit by Dillon).

def _spin(render, interval=0.35):
    # Motion-in-loading (P1 item 1, rabbot-to-page-P1-UX-motion-plus-agentic-cli-conventions-
    # pack-2026-08-01, Dillon msg 9272: "anything that blocks >1s shows motion"). One tiny
    # daemon thread ticking a frame counter into `render(i)`; caller owns stop()/join().
    stop=threading.Event()
    def run():
        i=0
        while not stop.is_set():
            render(i); i+=1; time.sleep(interval)
    t=threading.Thread(target=run,daemon=True); t.start()
    return stop,t

# ── TRANSPORT SEAM (Phase 1.5 slice 5, task 287) ──
# The blocker on the deferred Anthropic shim (assessed 2026-08-13) was that chat_stream
# fused TWO jobs with no boundary between them: (a) speak Ollama's /api/chat NDJSON wire
# protocol, and (b) render a heavily UX-tuned terminal stream (thinking blocks, word-boundary
# soft wrap, code-fence dimming, tok/s stats). A second provider could not be added without
# either duplicating the renderer or rewriting it — which is why the shim kept getting
# deferred rather than rushed.
#
# This slice extracts (a) into a generator contract and leaves (b) untouched. A transport is
# any generator yielding (kind, data) pairs:
#     ("thinking",   str)   — reasoning tokens, rendered dim-italic before the answer
#     ("content",    str)   — answer tokens
#     ("tool_calls", list)  — Ollama-shaped tool_calls (extract_tools' input contract);
#                             a non-Ollama transport translates INTO this shape, so the
#                             agent loop downstream never learns which provider answered
#     ("done",       dict)  — {"eval_count": int, "eval_seconds": float} for the stats line.
#                             Seconds, not Ollama's nanoseconds — a transport normalizes its
#                             own units so the renderer never carries vendor arithmetic.
# Any of these may be yielded zero or more times; only ordering within a wire chunk is
# preserved. Deliberately NOT shipping an Anthropic transport in the same diff: this half is
# a pure refactor with an existing oracle (identical rendering, batteries green), and the new
# provider is then a pure ADDITION that cannot regress the local floor path.
def _ollama_stream_events(msgs, provider=None):
    payload={"model":MODEL,"messages":msgs,"tools":TOOLS,"stream":True,"keep_alive":-1,
             "options":{"num_ctx":NUM_CTX}}
    if THINK is not None: payload["think"]=THINK
    r=urllib.request.Request(OLLAMA,data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=CHAT_TIMEOUT_S) as resp:
        for line in resp:
            line=line.strip()
            if not line: continue
            chunk=json.loads(line)
            msg=chunk.get("message") or {}
            if msg.get("thinking"): yield ("thinking", msg["thinking"])
            if msg.get("content"): yield ("content", msg["content"])
            if msg.get("tool_calls"): yield ("tool_calls", msg["tool_calls"])
            if chunk.get("done"):
                yield ("done", {"eval_count": chunk.get("eval_count") or 0,
                                "eval_seconds": (chunk.get("eval_duration") or 0)/1e9})

# ── ANTHROPIC TRANSPORT (Phase 1.5 slice 6, task 287) ──
# The `escalate` role's wire protocol, implemented against the seam contract above. This is
# the shim that was deferred twice: it is bounded now only because the seam exists — the
# renderer is untouched by this file's second protocol.
#
# stdlib urllib, not the `anthropic` SDK, and that is a deliberate constraint rather than a
# preference (Dillon's call, 2026-08-15). `genesis-open.nix` seals brainPython to
# `[prompt-toolkit, pyyaml]`; adding the SDK is a dependency change to a sealed, offline-first
# OS that cannot be built or tested on the machine this was written on. stdlib keeps the
# dependency surface unchanged and keeps this transport testable today, at the cost of
# hand-rolling SSE and the tool-call translation below.
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_ANTHROPIC_MAX_TOKENS = 64000   # streaming, so the ~16k non-streaming timeout ceiling doesn't apply

def _resolve_secret(ref):
    # providers.py REJECTS literal keys — api_key_ref must be a scheme://reference, so the
    # key never sits in the config file. Resolving it is this function's job.
    # `op://` needs the 1Password CLI, which is NOT in the sealed image; rather than shell out
    # to a binary that won't exist, fail loud with the exact remedy. Same rule as the
    # present-but-invalid providers.yaml path: refuse, don't silently degrade.
    if not ref:
        raise RuntimeError("escalate provider has no api_key_ref")
    scheme, _, rest = ref.partition("://")
    if scheme == "env":
        v = os.environ.get(rest)
        if not v:
            raise RuntimeError(f"api_key_ref env://{rest} is unset in this process")
        return v
    if scheme == "file":
        try:
            return open(os.path.expanduser(rest), encoding="utf-8").read().strip()
        except OSError as e:
            raise RuntimeError(f"api_key_ref file://{rest} unreadable: {e}")
    raise RuntimeError(
        f"unsupported api_key_ref scheme {scheme!r} — the sealed image ships no secret-manager "
        f"CLI, so use env://VAR or file://PATH (a systemd LoadCredential= or EnvironmentFile= "
        f"is the intended delivery path for {ref!r})")

# Run the escalate-secret preflight HERE, not at the definition above: it calls
# _resolve_secret, which is only defined at this point in the module. Recompute
# ESCALATE_STATUS afterwards — it was computed near the top, before this could run, so
# without this line the status surface reports "escalate: configured" on a box with no
# key, which is the same class of lie the ceiling work spent a day removing: a status
# field that is not the authority. (_escalate_status() did NOT consult
# _ESCALATE_UNAVAILABLE until the fix above — I asserted that it did before checking, in
# the fix about status fields that lie. It does now.)
_ESCALATE_PREFLIGHT_REASON = _preflight_escalate_secret()
if _ESCALATE_PREFLIGHT_REASON:
    # Both fields, deliberately. escalate_status_line() branches on `configured` and only
    # reads `reason` in the not-configured arm, so setting `reason` alone left the surface
    # printing "cloud-claude available" on a box with no key. Caught by the control arm,
    # not by review: the routing assertion passed while the line beside it still lied.
    ESCALATE_STATUS = _escalate_status()
    ESCALATE_STATUS["configured"] = False
    ESCALATE_STATUS["reason"] = _ESCALATE_PREFLIGHT_REASON

def _anthropic_translate_tools(tools):
    # Ollama: {"type":"function","function":{name, description, parameters}}
    # Anthropic: {name, description, input_schema}
    out = []
    for t in tools or []:
        fn = t.get("function") or {}
        if not fn.get("name"):
            continue
        out.append({"name": fn["name"],
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters") or {"type": "object", "properties": {}}})
    return out

def _anthropic_translate_messages(msgs):
    """Ollama message list → (system_string, anthropic_messages).

    Two shape mismatches have to be reconciled here, and the second is the awkward one:

    1. Ollama carries the system prompt as a `role: "system"` message; Anthropic takes it as a
       top-level `system` parameter. Hoist and join.
    2. Anthropic REQUIRES every `tool_result` to carry the `tool_use_id` it answers — but this
       codebase appends bare `{"role":"tool","content":res}` with no id at all (agent-brain.py's
       tool loop). The pairing is therefore positional: when an assistant turn requests N tools,
       synthesize N ids, then bind the next N `role:"tool"` messages to them in order. That
       matches how the loop actually appends results (one per call, in call order) and is the
       only information available — there is no id to recover.
    """
    system_parts, out, pending_ids = [], [], []
    for m in msgs:
        role = m.get("role")
        if role == "system":
            if m.get("content"):
                system_parts.append(m["content"])
        elif role == "user":
            out.append({"role": "user", "content": [{"type": "text", "text": m.get("content", "")}]})
        elif role == "assistant":
            blocks = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            pending_ids = []
            for i, tc in enumerate(m.get("tool_calls") or []):
                fn = tc.get("function") or {}
                args = fn.get("arguments")
                if isinstance(args, str):
                    try: args = json.loads(args or "{}")
                    except ValueError: args = {}
                tid = tc.get("id") or f"toolu_{len(out)}_{i}"
                pending_ids.append(tid)
                blocks.append({"type": "tool_use", "id": tid, "name": fn.get("name", ""), "input": args or {}})
            if blocks:
                out.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            tid = pending_ids.pop(0) if pending_ids else f"toolu_orphan_{len(out)}"
            block = {"type": "tool_result", "tool_use_id": tid, "content": str(m.get("content", ""))}
            # Anthropic wants every tool_result for one assistant turn in a SINGLE user message;
            # splitting them across messages trains the model out of parallel tool calls.
            if out and out[-1]["role"] == "user" and all(
                    b.get("type") == "tool_result" for b in out[-1]["content"]):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return "\n\n".join(system_parts), out

def _anthropic_stream_events(msgs, provider=None):
    # `provider` is the name the DISPATCHER resolved, not a module-level global. Reading
    # ACTIVE_PROVIDER here would be wrong the moment a transport serves any role other than
    # floor: ACTIVE_PROVIDER is the floor provider by construction, so an escalate-served turn
    # would look up the local ollama entry and find no api_key_ref. Caught by the battery.
    cfg = (_PROVIDERS or {}).get("providers", {}).get(provider or ACTIVE_PROVIDER, {})
    key = _resolve_secret(cfg.get("api_key_ref"))
    model = cfg.get("model", MODEL)
    system, amsgs = _anthropic_translate_messages(msgs)
    payload = {"model": model, "max_tokens": _ANTHROPIC_MAX_TOKENS, "stream": True,
               "messages": amsgs,
               # display defaults to "omitted" on current models, which would stream empty
               # thinking blocks — the renderer's thinking pane would sit blank through a long
               # pause. Ask for summaries so it has something to show.
               "thinking": {"type": "adaptive", "display": "summarized"}}
    if system:
        payload["system"] = system
    tools = _anthropic_translate_tools(TOOLS)
    if tools:
        payload["tools"] = tools
    r = urllib.request.Request(_ANTHROPIC_URL, data=json.dumps(payload).encode(),
                               headers={"content-type": "application/json",
                                        "x-api-key": key,
                                        "anthropic-version": _ANTHROPIC_VERSION})
    # SSE, not NDJSON: `event:` / `data:` line pairs, blank-line separated. Tool calls arrive as
    # a content_block_start naming the tool, then input_json_delta fragments that have to be
    # concatenated and parsed at content_block_stop — the input is NOT valid JSON until the
    # block closes, so it cannot be emitted incrementally.
    tool_calls, cur, buf, out_tokens = [], None, "", 0
    t0 = time.time()
    with urllib.request.urlopen(r, timeout=CHAT_TIMEOUT_S) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except ValueError:
                continue
            et = ev.get("type")
            if et == "content_block_start":
                cb = ev.get("content_block") or {}
                if cb.get("type") == "tool_use":
                    cur, buf = {"function": {"name": cb.get("name", "")}}, ""
            elif et == "content_block_delta":
                d = ev.get("delta") or {}
                dt = d.get("type")
                if dt == "text_delta" and d.get("text"):
                    yield ("content", d["text"])
                elif dt == "thinking_delta" and d.get("thinking"):
                    yield ("thinking", d["thinking"])
                elif dt == "input_json_delta":
                    buf += d.get("partial_json") or ""
            elif et == "content_block_stop":
                if cur is not None:
                    try: cur["function"]["arguments"] = json.loads(buf) if buf.strip() else {}
                    except ValueError: cur["function"]["arguments"] = {}
                    tool_calls.append(cur)
                    cur, buf = None, ""
            elif et == "message_delta":
                out_tokens = ((ev.get("usage") or {}).get("output_tokens")) or out_tokens
                # A refusal is a normal 200 with stop_reason "refusal" — surface it as text
                # rather than ending the turn silently with an empty response.
                if ((ev.get("delta") or {}).get("stop_reason")) == "refusal":
                    yield ("content", "\n[declined by safety classifier]")
            elif et == "error":
                raise RuntimeError(f"anthropic stream error: {(ev.get('error') or {}).get('message', ev)}")
    if tool_calls:
        yield ("tool_calls", tool_calls)
    yield ("done", {"eval_count": out_tokens, "eval_seconds": time.time() - t0})

_TRANSPORTS={"ollama": _ollama_stream_events, "claude": _anthropic_stream_events}

def _stream_events(msgs, route=None):
    # Dispatch on the ROUTE's provider `kind` (task 323). The route is resolved once per turn
    # from consent (_route_for_turn); with no consent it is the floor route, which is exactly
    # the pre-323 behavior. Reading module globals here instead would make the escalate role
    # unroutable no matter what consent said. An unknown kind fails LOUD rather than
    # silently falling back to ollama — same rule as the present-but-invalid providers.yaml
    # above: a misconfigured provider is a real error, and quietly answering from a DIFFERENT
    # provider than the config names is exactly the metered-bucket crossing providers.py
    # exists to forbid (2026-08-06 overnight-bleed scar).
    route = route or _floor_route()
    t=_TRANSPORTS.get(route["kind"])
    if t is None:
        raise RuntimeError(f"no transport for provider kind {route['kind']!r} "
                           f"(provider {route['provider']!r}); known kinds: {sorted(_TRANSPORTS)}")
    # Pass the resolved provider NAME through: a transport must configure itself from the
    # provider the dispatcher actually chose, never from a module-level global (see
    # _anthropic_stream_events). Today that is always the floor provider; the escalate role is
    # wired as of task 323, and this argument is what let that land without touching transports.
    return t(msgs, route["provider"])

def chat_stream(msgs, route=None):
    # Streaming + liveness indicator (P1 fix #2, same comm as above). Buffers tokens for
    # tool_calls/content-regex parsing (extract_tools) while printing as they arrive.
    #
    # Word-boundary soft wrap (P1 item 3, rabbot-to-page-RUNTIME-ANSWERS-hyprland-kitty-
    # firefox-items345-2026-08-01): the mid-word "weird" wrapping in Dillon's kitty session
    # is kitty's own hard character-wrap at the column edge — it has no word awareness. We
    # track the current visual column ourselves and emit a newline before a token that would
    # split across term width, so kitty never has to hard-wrap mid-word. Dumb on purpose: no
    # reflow on resize, just wrap-at-word going forward from turn start.
    #
    # Wire protocol lives in the transport (_stream_events above); everything below is
    # rendering only, and is provider-agnostic as of the slice-5 seam.
    route = route or _floor_route()
    if route["degraded"]:
        # A never-spill degrade is USER-VISIBLE by rule: silently answering a consented cloud
        # turn on the local floor is the same class of lie as answering it on a different
        # metered provider. Say which way it went.
        print(f"  \033[33m(↓ {route['degraded']})\033[0m")
    term_cols=shutil.get_terminal_size(fallback=(80,24)).columns
    t0=time.time(); content=""; tool_calls=[]; first_token=False; eval_count=0; eval_dur=0.0
    col=0; in_code=False; thinking_seen=False
    # Code-block rendering (P1 item 3, task #265): dim everything inside a ``` fence so it
    # reads as distinct from prose. Simplified for streaming — no box/indent, no collapse —
    # a token can only toggle the fence once (rare backtick-split-across-chunks edge case
    # accepted, same "keep it dumb" tradeoff as the word-wrap fix above).
    def _emit(tok):
        nonlocal col
        if '\n' in tok:
            col=len(tok)-tok.rfind('\n')-1
        elif col+len(tok)>term_cols and tok.strip():
            sys.stdout.write("\n"); col=len(tok)
            sys.stdout.write(f"\033[2m{tok}\033[0m" if in_code else tok)
            return
        else:
            col+=len(tok)
        sys.stdout.write(f"\033[2m{tok}\033[0m" if in_code else tok)
    def _thinking_frame(i):
        sys.stdout.write(f"\r\033[K\033[2mthinking{'.'*(i%3+1)} (^C cancels this turn)\033[0m"); sys.stdout.flush()
    think_stop,think_t=_spin(_thinking_frame)
    try:
        for _kind,_data in _stream_events(msgs, route):
            # THINKING RENDER (the actual P0 fix, spec "P0 DIAGNOSIS COMPLETE" 2026-08-06):
            # qwen3.5:9b is a thinking model on a ~3 tok/s CPU — it emits a `thinking`
            # stream for minutes before any content. Invisible thinking == "spinning
            # forever". Render it as dim italic rapid-fire text as it streams; when the
            # real answer starts, close with a one-line "— thought for Xs —" separator.
            # (True collapse of already-printed lines isn't possible in a dumb TTY
            # stream; the dim+separator approximation keeps the client simple.)
            tpiece=_data if _kind=="thinking" else ""
            if tpiece:
                if not first_token and not thinking_seen:
                    think_stop.set(); think_t.join(timeout=1)
                    sys.stdout.write("\r\033[K")
                thinking_seen=True
                sys.stdout.write(f"\033[2;3m{tpiece}\033[0m"); sys.stdout.flush()
            piece=_data if _kind=="content" else ""
            if piece:
                if not first_token:
                    if not thinking_seen:
                        think_stop.set(); think_t.join(timeout=1)
                    sys.stdout.write("\r\033[K")
                    if thinking_seen:
                        sys.stdout.write(f"\n\033[2m— thought for {time.time()-t0:.0f}s —\033[0m\n")
                    first_token=True; col=0
                content+=piece
                for tok in re.findall(r'\S+\s*|\s+', piece):
                    if '```' in tok:
                        segs=tok.split('```')
                        for i,seg in enumerate(segs):
                            if seg: _emit(seg)
                            if i<len(segs)-1: in_code=not in_code
                    else:
                        _emit(tok)
                sys.stdout.flush()
            if _kind=="tool_calls":
                tool_calls=_data
            if _kind=="done":
                eval_count=_data.get("eval_count") or 0
                eval_dur=_data.get("eval_seconds") or 0.0
    finally:
        if not first_token:
            think_stop.set(); think_t.join(timeout=1)  # idempotent if thinking already stopped it
            if thinking_seen:
                sys.stdout.write(f"\n\033[2m— thought for {time.time()-t0:.0f}s —\033[0m\n")
            else:
                sys.stdout.write("\r\033[K")
    elapsed=time.time()-t0
    tps=f", {eval_count/eval_dur:.0f} tok/s" if eval_count and eval_dur else ""
    # Ruling §2: "every escalated turn still prints provider/model/token cost inline."
    # Inline and unmissable, not buried in the audit log — the operator paying for it is the
    # one who has to see it.
    if route["role"]=="escalate":
        sys.stderr.write(f"\033[2m[{elapsed:.1f}s{tps}] \033[0m\033[33m[cloud: {route['provider']}/"
                         f"{route['model']}, {eval_count or 0} output tokens, consent={route['consent_source']}]\033[0m\n")
    else:
        sys.stderr.write(f"\033[2m[{elapsed:.1f}s{tps}]\033[0m\n")
    # `_usage` is an INTERNAL key for the audit record's token count, not part of the message
    # wire shape. turn() is the only caller that appends the result to a history, and it pops
    # this before appending — armed by the consent battery, because an internal key leaking into
    # a provider payload is the kind of thing that fails only on the metered side. The same
    # popped value feeds the cost-cap breaker's cumulative output-token count.
    # A TURN THAT PRODUCED NOTHING SAYS SO. Until 2026-08-31 an empty answer with no tool
    # calls printed the timing line and nothing else, so the box looked like it had replied
    # with silence. That is not hypothetical: with thinking on, this model spent its entire
    # `num_predict` budget (2048) reasoning, hit the ceiling mid-thought and returned an
    # EMPTY message — 457s, twice, measured on the Dell. `OLLAMA_THINK=off` (configuration-
    # open.nix) fixes THAT cause, but the class survives the cause: any future ceiling, stop
    # sequence, or transport hiccup can still land here, and the operator's only signal was
    # a prompt coming back.
    #
    # The generalisation from that hunt, which is why this is worth a branch: a stopwatch
    # reports "slow" for a run that produced no answer EXACTLY as for one that produced a
    # good answer, so the mute state hides inside the merely-slow one indefinitely. This is
    # the renderer refusing to let those two look alike.
    #
    # eval_count is named rather than described because it is the discriminator: a mute turn
    # with a LARGE count burned its budget (thinking, or a runaway that never emitted), while
    # one with ~0 means the model returned immediately and the problem is upstream. Those want
    # opposite fixes, so the number is the message.
    if not content and not tool_calls:
        sys.stderr.write(
            f"\033[33m[no answer — the model returned an empty message after {eval_count or 0} "
            f"output tokens. A large count means the reply budget went to reasoning "
            f"(check OLLAMA_THINK, it should be 'off'); a near-zero count means it stopped "
            f"immediately and the cause is upstream of the model.]\033[0m\n")
    return {"role":"assistant","content":content,"tool_calls":tool_calls,"_usage":eval_count or None}

def chat(msgs):
    # non-streaming fallback, kept for --once callers that want a single return value only
    body=json.dumps({"model":MODEL,"messages":msgs,"tools":TOOLS,"stream":False,"keep_alive":-1,
                     "options":{"num_ctx":NUM_CTX}}).encode()
    r=urllib.request.Request(OLLAMA,data=body,headers={"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(r,timeout=180))["message"]

# XML/Hermes tool-call form, emitted as CONTENT when the Modelfile template does not
# marshal structured `tool_calls`. Observed live on qwen3.5:9b (Dillon's photo of the Dell
# TUI, 2026-08-31 06:44 CDT), shaped:
#
#     <tool_call><function=run_command><parameter=command>uptime</parameter></function></tool_call>
#
# WHY THIS IS A CORRECTNESS BUG AND NOT A COSMETIC ONE. The JSON fallback below does not
# match this shape, so the block fell through as prose — and the brain then NARRATED a
# system status it had never run. A tool call that renders as text is a CLAIM: the model
# asked to act, nothing acted, and the next turn spoke as though it had. Parsing it is what
# makes the answer honest, not what makes it pretty.
#
# SCOPE, STATED SO THE NEXT READER DOES NOT OVERTRUST IT: this parses the observed form and
# the bare `<function=…>` block (a `<tool_call>` wrapper truncated by a token limit is
# exactly the dangerous prose case, so it must not be the thing that decides). Parameter
# values are RAW TEXT, never json.loads'd — `<parameter=command>` carries a shell string,
# and a command containing `{` is not a JSON object. A shape neither branch recognises is
# still left in `clean`, where _TOOLCALL_TOKEN_RE (the front-door kick) can still see it.
_XML_TOOLCALL_BLOCK_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)
_XML_FUNCTION_RE       = re.compile(r"<function\s*=\s*([A-Za-z_][\w.]*)\s*>(.*?)</function>", re.S)
_XML_PARAMETER_RE      = re.compile(r"<parameter\s*=\s*([A-Za-z_][\w.]*)\s*>(.*?)</parameter>", re.S)

def _extract_xml_tools(c):
    """Pull XML-form calls out of content. Returns (calls, content_with_blocks_removed).

    A `<function=…>` found inside a `<tool_call>` wrapper and one found bare are treated
    the same; only the span removed from `clean` differs, so a partial emission cannot
    leave half a call rendering as prose."""
    out=[]; clean=c; spans=[]
    for blk in _XML_TOOLCALL_BLOCK_RE.finditer(c):
        inner=blk.group(1); found=False
        for fn in _XML_FUNCTION_RE.finditer(inner):
            out.append((fn.group(1), {k: v for k, v in _XML_PARAMETER_RE.findall(fn.group(2))})); found=True
        # An empty or unrecognised <tool_call> wrapper is NOT swallowed — leaving it in
        # `clean` is what keeps the front-door kick able to see that the model tried.
        if found: spans.append(blk.span())
    if not out:
        for fn in _XML_FUNCTION_RE.finditer(c):
            out.append((fn.group(1), {k: v for k, v in _XML_PARAMETER_RE.findall(fn.group(2))}))
            spans.append(fn.span())
    for a,b in reversed(spans):
        clean = clean[:a] + clean[b:]
    return out, clean

def extract_tools(msg):
    tcs=msg.get("tool_calls") or []
    calls=[(t["function"]["name"], t["function"].get("arguments") if isinstance(t["function"].get("arguments"),dict) else json.loads(t["function"].get("arguments") or "{}")) for t in tcs]
    if calls: return calls, ""
    # fallback: model emitted the call as text in content (ollama template quirk for this model)
    c=msg.get("content","") or ""
    out=[]; clean=c
    for m in re.finditer(r"\{[^{}]*\"name\"\s*:\s*\"(\w+)\"[^{}]*\"arguments\"\s*:\s*(\{[^{}]*\})[^{}]*\}", c):
        try: out.append((m.group(1), json.loads(m.group(2)))); clean=clean.replace(m.group(0),"")
        except: pass
    if not out:
        out, clean = _extract_xml_tools(c)
    clean=re.sub(r"\bbrtc\b","",clean).strip()
    return out, clean

# Per-tool emoji + primary-arg preview (task #285 slice 2, Hermes port — spec-agentos-ux-
# polish-streaming-and-demo-window-2026-08-05 "Reference implementation" section: every
# tool gets one emoji + one primary arg shown in the progress line, e.g. "🧮 Calculating
# (2+3)*4…" instead of the old "calling calculator {"expression": "(2+3)*4"}…" json dump.
TOOL_META={
 "open_url":       {"emoji":"🌐","verb":"Opening","arg":"url"},
 "run_command":    {"emoji":"💻","verb":"Running","arg":"command"},
 "arrange_windows":{"emoji":"🪟","verb":"Arranging","arg":"action"},
 "calendar.agenda":{"emoji":"📅","verb":"Checking agenda","arg":None},
 "calendar.add":   {"emoji":"📅","verb":"Adding event","arg":"summary"},
 "calendar.now":   {"emoji":"📅","verb":"Checking the time","arg":None},
 "calendar.cals":  {"emoji":"📅","verb":"Listing calendars","arg":None},
 "calculator":     {"emoji":"🧮","verb":"Calculating","arg":"expression"},
 "system":         {"emoji":"⚙️","verb":"System","arg":"action"},
 "list_files":     {"emoji":"📁","verb":"Listing","arg":"dir"},
 "read_document":  {"emoji":"📄","verb":"Reading","arg":"path"},
 "media_info":     {"emoji":"🎞️","verb":"Probing","arg":"path"},
 "notes":          {"emoji":"📝","verb":"Notes","arg":"action"},
 "fetch_web":      {"emoji":"🔍","verb":"Fetching","arg":"url"},
 "summon_claude":  {"emoji":"☁️","verb":"Summoning Claude","arg":None},
}
TOOL_PREVIEW_LEN=40  # matches Hermes's default tool_preview_length

def build_tool_preview(name,args,length=TOOL_PREVIEW_LEN):
    meta=TOOL_META.get(name,{})
    argkey=meta.get("arg")
    if not argkey: return ""
    val=str(args.get(argkey,"") or "")
    if not val: return ""
    val=val.replace("\n"," ")
    return " "+(val if len(val)<=length else val[:length-1]+"…")

def tool_progress_label(name,args):
    meta=TOOL_META.get(name,{"emoji":"⚡","verb":f"calling {name}"})
    return meta["emoji"], meta["verb"]+build_tool_preview(name,args)+"…"

# The arrange_windows enum, mapped to Hyprland 0.56 Lua dispatch expressions (probed against a
# live compositor on the Dell, 2026-08-30, each negative arm controlled with a bogus name so an
# absence is a real absence). CLOSED SET, keyed by the tool's own enum: a caller string is only
# ever used as a dict KEY and is never interpolated into the Lua. That is the whole safety
# property — under a hyprland.lua config `hyprctl dispatch` EVALUATES its argument as Lua
# (hl.dispatch(<arg>)), so any interpolation into these values would be an eval sink.
# 'tidy' is GONE rather than translated: it was `layoutmsg orientationcycle`, and orientationcycle
# is a MASTER-layout message while this system runs dwindle, so the compositor rejected it
# (`Unknown dwindle layoutmsg`). That rejection comes from the layout implementation, not the
# config format, so it was INFERRED — not measured — to have been a no-op under the old .conf as
# well; measuring it would cost a rebuild of the Dell back to the pre-Lua generation. A
# dwindle-appropriate replacement is a product change, not a translation, so it is out of scope
# here (Rabbot ruling, 2026-08-30).
HYPR={"close":"hl.dsp.window.close()","fullscreen":"hl.dsp.window.fullscreen()",
      "cycle":"hl.dsp.window.cycle_next()","split":'hl.dsp.layout("togglesplit")'}
def do_tool(name,args):
    if name=="open_url":
        url=args.get("url","")
        # DIRECT ARGV — this must NEVER be routed back through `hyprctl dispatch`. Under a
        # hyprland.lua config, hyprctl dispatch EVALUATES its argument as Lua, so a model-supplied
        # URL interpolated into a dispatch string can close the string literal and run arbitrary
        # Lua inside the process that owns the display. Popen with a LIST is no shell and no
        # interpreter: the URL arrives at Firefox as one literal argv element. If you are
        # "cleaning this up" into a dispatch call to match arrange_windows, you are reopening
        # that hole (Rabbot ruling, 2026-08-30 — remove the sink, do not escape it).
        #
        # SCHEME GATE. argv removes the shell and the Lua eval, but firefox still reads a
        # leading `-` as a FLAG rather than a URL, and its flags are not inert:
        # --remote-debugging-port hands anything on localhost full control of the browser,
        # --screenshot writes an arbitrary file, -P swaps the profile. `url` is model-supplied
        # and the model reads untrusted pages (fetch_web), so this is reachable by prompt
        # injection. Allow only the two schemes the tool is documented to take.
        if not re.match(r"^https?://", url, re.I):
            return f"refused: open_url takes an http(s) URL, got {url!r}"
        subprocess.Popen(["firefox","--new-window",url],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return f"opened {url} in the browser"
    if name=="run_command":
        try:
            o=_sh(args.get("command",""),30)
            return ((o.stdout+o.stderr).strip() or "(done, no output)")[:1500]
        except Exception as e: return f"error: {e}"
    if name=="arrange_windows":
        act=args.get("action","").lower(); disp=HYPR.get(act)
        # Unknown key: return the error as data and DISPATCH NOTHING. The early return is the
        # control that keeps `act` off the command line entirely.
        if not disp: return f"unknown window action '{act}'"
        # argv, not `bash -c`: `disp` is a fixed value from the table above, but routing a
        # compositor dispatch through a shell adds a second interpreter for no benefit.
        subprocess.run(["hyprctl","dispatch",disp],capture_output=True,text=True,timeout=6)
        return f"desktop: {act} done"
    if name in ("calendar.now","calendar.agenda","calendar.add","calendar.cals"):
        return _agos(name,args)
    # the ambient-dozen hands — thin wrappers over the agos-* CLIs (each emits JSON, exits 0 on success)
    if name=="calculator":     return _run_agos("agos-calc","eval",args.get("expression",""))
    if name=="system":
        a=args.get("action","").lower()
        if a=="status": return _run_agos("agos-sys","status")
        if a in ("volume","brightness"): return _run_agos("agos-sys",a,str(args.get("value","")))
        if a=="power":
            # Closed enum, validated HERE as well as in agos-sys: the value selects a fixed
            # word, it is never interpolated into a command line. Rejecting it here means an
            # unknown value returns as DATA and dispatches nothing (the arrange_windows shape).
            v=str(args.get("value","")).lower().strip()
            if v not in ("reboot","poweroff"):
                return f"system: power takes 'reboot' or 'poweroff', got {v!r}"
            return _run_agos("agos-sys","power",v)
        return f"system: unknown action '{a}' (use status|volume|brightness|power)"
    if name=="list_files":     return _run_agos("agos-files","list",args.get("dir",""))
    if name=="read_document":
        c=["agos-doc","text",args.get("path","")]
        if args.get("page"): c.append(str(args["page"]))
        return _run_agos(*c)
    if name=="media_info":     return _run_agos("agos-media","info",args.get("path",""))
    if name=="notes":
        a=args.get("action","list").lower()
        if a=="list": return _run_agos("agos-notes","list")
        if a=="read": return _run_agos("agos-notes","read",args.get("slug",""))
        return f"notes: unknown action '{a}' (use list|read)"
    if name=="fetch_web":      return _run_agos("agos-web","fetch",args.get("url",""))
    if name=="summon_claude":  return _summon_claude(args.get("task",""),args.get("context_summary",""))
    return "unknown tool"

def _summon_claude(task,context_summary):
    # Cloud summon (rabbot-to-page-P1-summon-claude-tool-local-first-consent-flow-2026-08-02,
    # Dillon msg 9284). Consent lives upstream in SYS_BASE — by the time this runs the user
    # has said yes. Reachable only through do_tool, which the 3B front-door structurally
    # cannot fire (kick wall, PR #64) — cloud stays a summon on the 9B side, never an OS
    # dependency. Brief = task + compacted context + one machine line, never full history.
    # THE GATE. Before the task check, so a consent-less call is refused on the ground it
    # is actually refused on, and cannot be probed for validity by sending an empty task.
    allowed, why = ok_to_summon()
    if not allowed:
        _log_summon_attempt(False, why)
        return ("summon refused: " + why + ". The operator grants it by typing `:summon` at the "
                "prompt — saying yes in conversation is not enough, because this path spends "
                "their cloud account.")
    _log_summon_attempt(True, None)
    # A GRANT BUYS AN ANSWER, NOT AN ATTEMPT. Every return below this point that hands the
    # operator an error instead of a reply gives the grant back on its ORIGINAL clock, so a
    # box whose `claude` has never been logged in does not eat one consent act per retry.
    # ONE rule, applied to rc!=0 and to every exception alike — deliberately not a per-error
    # classification, and in particular NOT a string match on stderr deciding whether the
    # account was touched. The success path is the only path that consumes.
    def _kept(msg):
        return msg + _SUMMON_KEPT if restore_summon_grant() else msg
    # A MALFORMED CALL IS AN ATTEMPT THAT RETURNED NOTHING, so it restores like any other.
    # This return used to sit ABOVE `_kept` and was the single non-success path that still
    # ate the grant — the comment directly above said "every return below this point", and
    # it was true only because this one was not below it. A model emitting `summon_claude`
    # with a blank task burned the operator's consent act for a validation error.
    if not task: return _kept("summon error: no task given")
    brief=(f"Task from Agent OS's local brain (relay your answer to the user through it):\n{task}\n\n"
           f"Conversation context:\n{context_summary}\n\n"
           f"Machine: NixOS Linux (Agent OS, flake-built — system changes go in the OS repo).")
    try:
        o=subprocess.run(["claude","-p",brief,"--output-format","text"],
                         capture_output=True,text=True,timeout=180)
        if o.returncode!=0:
            err=(o.stderr or o.stdout).strip()[:300]
            # BEST-EFFORT MESSAGING ONLY, and it is only safe to leave as a substring guess
            # BECAUSE of the restore above: this branch no longer decides anything about the
            # operator's grant, which is given back on every failure path alike. It picks
            # which sentence they read, nothing more. `login` is matched as well as `log in`
            # — they are not substrings of each other, and an arm using an auth-shaped
            # failure ("Please run /login") fell straight through the old pair into the
            # generic "couldn't complete that", which names no remedy at all.
            if any(s in err.lower() for s in ("log in", "login", "auth", "api key")):
                return _kept("Claude Code isn't logged in — run `claude` once in a terminal to sign in")
            return _kept(f"Claude couldn't complete that: {err or 'no output'}")
        return (o.stdout.strip() or "(Claude returned nothing)")[:8000]
    except FileNotFoundError:
        return _kept("Claude Code isn't set up — run `claude` once in a terminal to log in")
    except subprocess.TimeoutExpired:
        return _kept("Claude took too long (180s) — try a smaller ask, or run `claude` in a terminal for long jobs")
    except Exception as e:
        return _kept(f"summon error: {e}")  # fail-soft — never crash a turn

def _run_agos(*cmd):
    # generic runner for the agos-* ambient-dozen CLIs; passes their JSON stdout through verbatim
    cli=cmd[0]
    try:
        o=subprocess.run([c for c in cmd if c!=""],capture_output=True,text=True,timeout=25)
        if o.returncode!=0: return f"{cli} error: "+((o.stderr or o.stdout).strip()[:400] or "failed")
        return (o.stdout.strip() or "(done)")[:4000]
    except FileNotFoundError: return f"{cli} not on PATH (its module isn't deployed on this box yet)"
    except Exception as e: return f"{cli} error: {e}"

def _agos(name,args):
    # thin wrapper over the agos-cal CLI (ships with the calendar-open module); passes JSON through
    if name=="calendar.now":     cmd=["agos-cal","now"]
    elif name=="calendar.cals":  cmd=["agos-cal","cals"]
    elif name=="calendar.agenda":cmd=["agos-cal","agenda",str(args.get("days") or 7)]
    elif name=="calendar.add":
        cmd=["agos-cal","add",args.get("start",""),args.get("summary","")]
        if args.get("end"): cmd.append(args["end"])
    else: return "unknown calendar op"
    try:
        o=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
        if o.returncode!=0: return "calendar error: "+((o.stderr or o.stdout).strip()[:300] or "failed")
        return o.stdout.strip() or "(done)"
    except FileNotFoundError: return "calendar error: agos-cal not on PATH (calendar module not deployed)"
    except Exception as e: return f"calendar error: {e}"

CHAT_LOCK=threading.Lock()  # serializes chat_stream calls (warmup thread vs interactive turns)
                            # onto ollama's single inference slot, and protects the shared msgs list.

def chat_stream_safe(msgs, retries=1, route=None):
    # Never crash to a raw traceback on tty1 (P1 fix #1). A cold-boot prefill can outrun even the
    # 600s timeout under heavy load; on timeout/connection failure, tell the user and retry once —
    # the KV slot is already cached from the failed attempt, so retry #2 is cheap (live-verified:
    # Dillon's retry after the 3min timeout completed fast off the cached slot).
    for attempt in range(retries+1):
        try:
            # Queue visibility (task #265 follow-on, rabbot-to-page-P2-ux-v2-spec): the single
            # ollama slot may be held by the boot-warmup thread (or a prior turn) — a silent
            # blocking acquire reads as a hang. Say so while we wait.
            if not CHAT_LOCK.acquire(blocking=False):
                def _busy_frame(i):
                    sys.stdout.write(f"\r\033[K\033[2mbrain busy{'.'*(i%3+1)} (finishing another job, your turn is queued)\033[0m"); sys.stdout.flush()
                busy_stop,busy_t=_spin(_busy_frame)
                try:
                    CHAT_LOCK.acquire()
                finally:
                    busy_stop.set(); busy_t.join(timeout=1)
                    sys.stdout.write("\r\033[K"); sys.stdout.flush()
            try:
                msg=chat_stream(msgs, route)
            finally:
                CHAT_LOCK.release()
            # Tag the message with the route that actually SERVED it (gate finding F1, PR #141):
            # after an in-flight degrade below, that is the floor route, not the one turn() asked
            # for — and turn() must log the served one or the audit record claims a cloud spend
            # that never happened. Internal key, popped by turn() like `_usage`.
            msg["_route"]=route
            return msg
        except (TimeoutError, urllib.error.URLError, ConnectionError) as e:
            # ESCALATE DEGRADE, checked FIRST but inside the SAME handler as every other
            # transport failure — deliberately not its own `except` clause. HTTPError subclasses
            # URLError, so a separate clause placed above this one silently captures floor-role
            # HTTP errors too, and `raise`-ing those turned an ollama 500 into a raw traceback on
            # tty1 — the exact contract this function exists to hold (P1 fix #1). An isinstance
            # check inside the existing handler cannot strand a case the handler used to serve.
            if (isinstance(e, urllib.error.HTTPError) and route and route["role"]=="escalate"
                    and e.code in (401,403,429,500,502,503,529)):
                # Rate-limited / overloaded on the ESCALATE provider → mark it unavailable for the
                # rest of the session and re-resolve. _route_for_turn degrades to the LOCAL FLOOR,
                # never to another metered provider (providers.resolve enforces it).
                sys.stdout.write("\r\033[K")
                _ESCALATE_UNAVAILABLE.add(route["provider"])
                route=_route_for_turn(route["consent_source"])
                # Re-enter with the DEGRADED route and a FRESH retry budget (gate finding F2):
                # a degrade is a re-route, not a failed attempt, so it must not consume one — a
                # timeout-then-429 pair would otherwise exhaust the `for` and fall off the end
                # returning None, which turn() then .pop()s → traceback on tty1. Bounded: the
                # re-resolved route is the floor, and a floor-role HTTPError takes the generic
                # arm below, so this recurses at most once.
                return chat_stream_safe(msgs, retries=retries, route=route)
            sys.stdout.write("\r\033[K")
            if attempt < retries:
                print(f"  \033[2m(still warming the model — retrying…)\033[0m")
            else:
                print(f"  \033[31m(model isn't responding right now — try again in a moment: {e})\033[0m")
                return {"role":"assistant","content":"","tool_calls":[]}

EXPAND_BUFFERS=[]  # tool-call full outputs kept for the :expand N command (item 2, task #265)

def _compact_for_display(text,head=4,tail=3):
    # Tool-call result COMPACTION (P1 item 2, rabbot-to-page-P1-UX-motion-plus-agentic-cli-
    # conventions-pack-2026-08-01: "walls of text are the #1 complaint class"). TTY-dumb by
    # design (Rabbot's framing) — no interactive folding, just a re-printable buffer behind
    # a `:expand N` command typed at the prompt.
    lines=text.splitlines()
    if len(lines)<=head+tail+1:
        return text
    EXPAND_BUFFERS.append(text)
    idx=len(EXPAND_BUFFERS)
    hidden=len(lines)-head-tail
    shown=lines[:head]+[f"\033[2m… ({hidden} more lines, :expand {idx} to see)\033[0m"]+lines[-tail:]
    return "\n".join(shown)

def _trip_cost_breaker(kind, spent, hops, msgs=None, pending=0, route=None):
    # Fail-LOUD half of the cost-cap breaker. LOUD here means: red banner on the tty, a
    # breaker event in the provenance log, and (on a token trip) tool-result stubs so the
    # transcript stays well-formed for BOTH transports — the anthropic translator pairs
    # role:"tool" messages positionally to the pending tool_use blocks, so each unexecuted
    # call gets exactly one stub. NOT sys.exit: the interactive brain must never die to a
    # runaway turn (the tty1-traceback rule); the turn halts, the brain survives.
    route=route or _floor_route()
    ceiling=(f"token ceiling {MAX_TURN_TOKENS}" if kind=="token" else f"hop ceiling {MAX_TURN_HOPS}")
    line=(f"COST-CAP BREAKER: turn halted at {ceiling} ({hops} model call(s), ~{spent} output tokens)"
          +(f"; {pending} pending tool call(s) NOT executed" if pending else ""))
    print(f"\n\033[1;31m⛔ {line}\033[0m")
    if msgs is not None:
        for _ in range(pending):
            msgs.append({"role":"tool","content":line+" — this tool call was refused; the turn is over. Answer with what you have."})
    try:
        with open(_TURN_LOG_PATH,"a") as f:
            f.write(json.dumps({"ts":datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                "provider":route["provider"],"model":route["model"],"role":route["role"],
                                "consent_source":route["consent_source"],"event":"cost_cap_breaker",
                                "kind":kind,"hops":hops,"output_tokens":spent})+"\n")
    except Exception:
        pass  # same rule as _log_turn_provenance: a log failure never breaks a live turn

def turn(msgs, consent_source=None):
    # The route is resolved ONCE per turn, from consent, before any model call — so a turn
    # cannot drift onto a metered provider partway through its tool loop.
    route=_route_for_turn(consent_source)
    spent=0; hops=0
    while hops<MAX_TURN_HOPS:
        msg=chat_stream_safe(msgs, route=route)
        # The route that actually SERVED the call wins the audit record and the rest of the
        # turn (gate finding F1): chat_stream_safe may have degraded an escalate route to the
        # floor in flight (provider 429/5xx). Logging the route we ASKED for would record a
        # cloud spend that never happened — criterion 5 inverted in the one case it exists for —
        # and re-asking the marked-unavailable provider on every tool-loop iteration is a hammer.
        # Monotone: a served route is the asked route or its floor degrade, never more metered.
        route=msg.pop("_route", route)
        usage=msg.pop("_usage", None)
        _log_turn_provenance(route, usage)
        # Cost-cap breaker counters: cumulative output tokens across the turn's hops (None
        # from a transport that reported nothing counts as 0 spend — it cannot trip the
        # ceiling, and the provenance line above already records the unknown as null).
        spent+=usage or 0; hops+=1
        # Record METERED spend against the cumulative ceilings, per hop rather than per
        # turn — a turn that never returns (the respawn-loop shape the ceiling exists for)
        # would otherwise contribute nothing to the counter it is supposed to be filling.
        # Floor turns are free and deliberately not counted; see spend_ceiling.py's UNIT.
        if route.get("role") == "escalate" and usage and _spend is not None:
            try:
                _spend.record(int(usage))
            except Exception as e:
                # Unrecorded spend is unbounded spend: say so loudly and stop the turn
                # rather than continue paying into a counter that is not counting.
                print(f"\n\033[1;31m⛔ spend ceiling could not record this hop ({e}) — halting the turn\033[0m")
                return
        msgs.append(msg)
        calls,clean=extract_tools(msg)
        if not calls:
            if clean and not msg.get("content"): print(clean)  # regex-extracted clean text wasn't already streamed
            return
        if clean and not msg.get("content"): print(clean)
        # Token check sits between "model asked for tools" and "tools run": once over
        # budget the pending calls are refused, not executed — halting spend is the point.
        # NOT a hard cap: the ceiling is checked AFTER each streamed call, so real spend can
        # overshoot by one hop's output before the trip (a streaming call cannot be
        # preempted mid-flight without a per-call max_tokens). What it guarantees is that
        # no FURTHER tool calls run once cumulative output >= max_output_tokens_per_turn.
        if MAX_TURN_TOKENS is not None and spent>=MAX_TURN_TOKENS:
            _trip_cost_breaker("token",spent,hops,msgs,len(calls),route=route); return
        for name,args in calls:
            # summon gets its own cloud dressing — visually distinct from local ⚡ work.
            if name=="summon_claude":
                label="summoning Claude… [cloud]"; base="☁"
            else:
                base,label=tool_progress_label(name,args)
            def _tool_frame(i,label=label,base=base):
                glyph=f"\033[7m{base}\033[0m" if i%2 else base
                sys.stdout.write(f"\r\033[K  \033[33m{glyph} {label}\033[0m"); sys.stdout.flush()
            spin_stop,spin_t=_spin(_tool_frame,interval=0.3)
            try:
                res=do_tool(name,args)
            finally:
                spin_stop.set(); spin_t.join(timeout=1)
                print(f"\r\033[K  \033[33m{base} {label}\033[0m")
            res=str(res)
            preview=_compact_for_display(res)
            # Fenced code block for run_command output (Hermes port, task #285 slice 2) —
            # reads as "the snake emoji + the code it ran" instead of bare dim text.
            if name=="run_command":
                print(f"\033[2m```\n{preview}\n```\033[0m")
            elif preview!=res or "\n" in preview:
                print(f"\033[2m{preview}\033[0m")
            msgs.append({"role":"tool","content":res})
    # Hop ceiling reached with the model still mid-task (its last hop's tools DID run and
    # their results are in the transcript — nothing is left unpaired). This exhaustion has
    # always ended the turn; before the breaker it ended SILENTLY, reading as a finished
    # answer. Now it reports.
    _trip_cost_breaker("hop",spent,hops,route=route)

# ── 3B FRONT-DOOR → 7B KICK SIGNAL (A-with-a-wall, interim) ────────────────────
# Dillon picked Design A (msg 9272); Rabbot's wall shape governs; Augur's spec is
# 3B-FRONTDOOR-KICK-SIGNAL-SPEC.md. Every interactive turn hits the 3B first. The 3B
# may ANSWER pure-conversation/dispatch turns but has NO executor path: any
# action-shaped output is structurally discarded and the turn re-dispatches to the 7B,
# which stays the ONLY tool-wielder (do_tool is reachable solely from turn()'s 7B
# loop — frontdoor_* never executes anything). This wall is INTERIM: when Augur's
# refusal-retrain + no-regression gate lands, the 3B graduates to executing its own
# validated lane's tools; until then EVERY 3B tool_call is discarded.
#
# run-6's output is strictly bimodal (tool_calls XOR pure text, zero hybrids), so the
# primary kick detector is structural, not a fuzzy classifier. The pure-text heuristics
# below are the secondary net for text turns that still INTEND an action. Bias: unsure →
# kick (a false kick costs one 7B hop; a false keep lets the 3B free-text past its
# competence). The spec's "off-lane topic" heuristic is NOT implemented v1 — no cheap
# local topic classifier exists; the length + uncertainty guards absorb most of it.
_TOOLCALL_TOKEN_RE=re.compile(r"<tool_call>")                     # Qwen2.5 Hermes special token in raw decode
_ACTION_OFFER_RE=re.compile(r"\b(want me to|should i|shall i|i can (?:make|do|run|edit)|let me)\b",re.I)
_UNSURE_RE=re.compile(r"\b(not sure|i don'?t (?:have|know)|can'?t tell)\b",re.I)
_FRONTDOOR_MAX_TOKENS=60  # run-6 conversational answers are terse; longer = off-distribution (tune on Dell)

def frontdoor_decide(msg):
    """Pure decision: (kick: bool, reason: str, proposal: str). NEVER executes anything.
    Rule (1) hard: any tool_call — parsed, raw <tool_call> token, or JSON-shaped call in
    content (extract_tools' fallback regex) — kicks, and the call itself is the proposal
    forwarded to the 7B as context only."""
    calls,clean=extract_tools(msg)
    raw=msg.get("content","") or ""
    if calls or _TOOLCALL_TOKEN_RE.search(raw):
        prop=json.dumps([{"name":n,"arguments":a} for n,a in calls]) if calls else raw.strip()
        return True,"tool_call",prop
    text=raw.strip()
    if not text:
        return True,"empty",""
    if _ACTION_OFFER_RE.search(text): return True,"action_offer",text
    if _UNSURE_RE.search(text):       return True,"unsure",text
    if len(text.split())>_FRONTDOOR_MAX_TOKENS: return True,"length",text
    return False,"",text

_FRONTDOOR_OK=None  # tri-state cache: None=unprobed, True/False after first tags check
def _frontdoor_available():
    global _FRONTDOOR_OK
    if _FRONTDOOR_OK is None:
        try:
            r=urllib.request.Request("http://127.0.0.1:11434/api/tags")
            tags=json.load(urllib.request.urlopen(r,timeout=5))
            _FRONTDOOR_OK=any(m.get("name","").startswith(MODEL_3B) for m in tags.get("models",[]))
        except Exception:
            _FRONTDOOR_OK=False
    return _FRONTDOOR_OK

# ── LITERAL-VERB SHORT-CIRCUIT (P0, 2026-09-05) ───────────────────────────────
# Dillon typed `reboot` at the front door and waited 86s of routing plus a 20s think, and
# then got a question back. A one-word literal that names a power verb has nothing for a
# model to decide: there is no argument to extract, no ambiguity to resolve, and no
# alternative reading. So it does not reach one — this table dispatches the capability
# directly and no LLM call happens on the turn at all.
#
# DELIBERATELY NARROW, and the narrowness is the safety argument. Matching is EXACT on the
# whole normalised utterance (lowercased, trailing punctuation stripped) against a closed
# table — not a prefix, not a keyword search, not "contains". "reboot the router when you
# get a chance" does not match and goes to the model, which is correct. A verb only earns a
# row here when a model could add NOTHING to it; `status` is deliberately NOT in the table,
# because a person asking for status wants prose about the machine, not its JSON.
#
# It is also not a confirmation bypass. Typing the bare word IS the act — the same standard
# as `:summon` and `:escalate` one screen down, where typing the token is itself the consent
# and nothing else can arm it.
#
# ⚠ THIS TABLE ASSUMES A TYPED TRANSPORT, AND THE CODE CANNOT SEE ITS TRANSPORT.
# The safety argument one paragraph up — "typing the bare word IS the act" — is a claim
# about how the utterance ARRIVED, not about anything in this file. It is true today:
# `frontdoor_turn` has one call site, fed from the typed REPL loop, and there is no
# ASR/whisper/STT path anywhere in agent-brain.py (Augur verified this against origin/main
# in the #277 review rather than taking it on my word).
#
# IF A NON-TYPED FRONT END IS EVER WIRED TO THIS LOOP — voice, transcription, a remote
# message bus, anything that can put text in `msgs` without a human pressing keys — REVISIT
# HERE FIRST, BEFORE wiring it. A mis-transcribed one-word utterance would then reboot the
# machine with no model anywhere in the path to hesitate, and every line above would still
# read as correct, because each one is. A defence argued on one axis does not survive a
# change on another; this note exists so the change on the other axis lands on the defence.
_LITERAL_VERBS = {
    "reboot":    ("system", {"action": "power", "value": "reboot"}),
    "restart":   ("system", {"action": "power", "value": "reboot"}),
    "poweroff":  ("system", {"action": "power", "value": "poweroff"}),
    "power off": ("system", {"action": "power", "value": "poweroff"}),
    "shutdown":  ("system", {"action": "power", "value": "poweroff"}),
    "shut down": ("system", {"action": "power", "value": "poweroff"}),
}

def literal_verb(text):
    """The whole utterance, or nothing. Returns (tool, args) or None — never executes."""
    t = (text or "").strip().lower().rstrip(".!?").strip()
    return _LITERAL_VERBS.get(t)

def frontdoor_turn(msgs, consent_source=None):
    """Interactive entry: 3B first, kick to the 7B turn() on any action shape.
    Fail-open to turn() (the status-quo 7B path) if the 3B is absent or errors — the
    wall protects against the 3B ACTING, not against its absence. --once and the
    warmup thread call turn() directly and are deliberately untouched (boot prewarm
    warms the 7B KV prefix; front-dooring them would change prewarm semantics)."""
    # An explicitly-consented escalate turn skips the 3B entirely: the operator asked for the
    # cloud brain on THIS turn, and letting the local front-door answer it instead would silently
    # discard the consent act. (Consent is never inferred in the other direction either — a turn
    # the 3B can't handle is kicked to the local 7B floor, exactly as before.)
    if consent_source:
        return turn(msgs, consent_source)
    # Before ANY model call, including the 3B's. An explicitly-consented escalate turn is
    # already gone above, so a person who typed `:escalate reboot` still gets the cloud brain.
    lit = literal_verb(msgs[-1].get("content","")) if msgs and msgs[-1].get("role")=="user" else None
    if lit:
        name, args = lit
        out = do_tool(name, args)
        print(out)
        msgs.append({"role":"assistant","content":out,"tool_calls":[]})
        return
    if not _frontdoor_available():
        return turn(msgs)
    stop,t=_spin(lambda i: (sys.stdout.write(f"\r\033[K\033[2mrouting{'.'*(i%3+1)}\033[0m"),sys.stdout.flush()))
    t0=time.time()
    try:
        body=json.dumps({"model":MODEL_3B,"messages":msgs,"tools":TOOLS,"stream":False,"keep_alive":-1,
                         "options":{"num_ctx":NUM_CTX}}).encode()
        r=urllib.request.Request(OLLAMA,data=body,headers={"Content-Type":"application/json"})
        with CHAT_LOCK:
            msg=json.load(urllib.request.urlopen(r,timeout=CHAT_TIMEOUT_S))["message"]
    except Exception:
        stop.set(); t.join(timeout=1); sys.stdout.write("\r\033[K")
        return turn(msgs)  # 3B down mid-session → status-quo 7B path
    stop.set(); t.join(timeout=1); sys.stdout.write("\r\033[K")
    sys.stderr.write(f"\033[2m[front-door {time.time()-t0:.1f}s]\033[0m\n")
    kick,reason,proposal=frontdoor_decide(msg)
    if not kick:
        # clean, terse, no-action-offer pure text: the 3B's answer stands.
        print(proposal)
        msgs.append({"role":"assistant","content":proposal,"tool_calls":[]})
        return
    # Discard-and-kick: the 3B's output reaches the 7B as CONTEXT ONLY; nothing from the
    # 3B is executed, ever (rule 1 — this sits before any execution path by construction).
    if proposal:
        # user-role, NOT system: the proposal is untrusted model output (steerable by
        # anything in the history) — elevating it to system authority would hand a
        # prompt-injection a privileged channel into the 7B. Bracketed as machine context.
        msgs.append({"role":"user","content":"[front-door note — the local 3B proposed the following and it was DISCARDED, not executed. Possibly-wrong hint, apply your own judgment: "+proposal[:600]+"]"})
    turn(msgs)

def _model_pulled():
    # guard for the memory-floor path: don't fire a warmup generation against a model that
    # hasn't finished pulling yet.
    try:
        r=urllib.request.Request("http://127.0.0.1:11434/api/tags")
        tags=json.load(urllib.request.urlopen(r,timeout=5))
        return any(m.get("name","").startswith(MODEL.split(":")[0]) for m in tags.get("models",[]))
    except Exception:
        return False

def warmup_greeting(msgs):
    # Boot-warmup (P1, was P2 idea comm rabbot-to-page-P2-boot-warmup-greeting-kv-prewarm,
    # upgraded same-day after the live timeout crash): fire the agent's own first turn as soon as
    # ollama is up, so the cold prefill (~3min class on CPU) happens during boot dead-time instead
    # of on the user's actual first message. Runs in a background thread — CHAT_LOCK means a real
    # user turn queues behind it rather than racing it, but input() itself is never blocked.
    #
    # Poll rather than check-once (PR #49 review nit #1, rabbot-to-page-pr49-MERGED-two-followup-nits):
    # on true cold boot ollama may not be up yet at the instant this thread starts — that's the exact
    # case the feature exists for, so a single check silently no-ops the warmup for the case that
    # matters most. Retry every 3s for up to ~60s.
    for _ in range(20):
        if _model_pulled():
            break
        time.sleep(3)
    else:
        return
    # Throwaway history (PR #49 review nit #2): appending straight to the shared `msgs` list outside
    # CHAT_LOCK let a fast first real user message interleave with the warmup's own append, scrambling
    # turn order (warmup-user, real-user, warmup-assistant). A separate [sysmsg(), user_turn(...)] list
    # still warms the static-prefix KV cache — that's all the feature needs — without touching the
    # shared history at all.
    warmup_msgs=[sysmsg(), user_turn("boot complete, greet the operator in one line")]
    turn(warmup_msgs)

# ── IN-LOOP STDERR → JOURNAL (task 285 follow-up; Geist RULED 2026-09-05T18:17Z) ──
# #285 teed the brain's PRE-loop stderr into brain-home's journal and said, in its own commit
# message, which half it could not reach: the turn loop runs inside patch_stdout(raw=True),
# which replaces sys.stderr with a StdoutProxy onto STDOUT, so every in-loop write to fd 2 —
# the `[front-door 26.3s]` router-leg lines — hits the screen and fd 2 never sees it. That is
# row A2 of #285's firing table: screen YES, journal NO.
#
# ONE site, and it is the guard, not the writers. The eleven sys.stderr.write call sites are
# untouched: the guard is what swallows the stream, so the guard is where the copy belongs.
#
# The screen side is byte-for-byte pass-through — a startup refusal and the dim styling both
# stay exactly as they were. The journal side is COOKED: complete lines only, ANSI stripped,
# and only the last frame of a \r-redraw, because the journal wants `[front-door 26.3s]` and
# not two hundred spinner repaints.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

class _JournalTee:
    # PER-THREAD line buffers, not one shared buffer with a lock (Geist's #286 note 2, measured
    # rather than taken). The brain writes to stderr from the warmup thread as well as the turn
    # loop. The screen was never at risk — prompt_toolkit's proxy locks its own write — but the
    # journal side buffers until a newline, and a partial write from one thread followed by
    # another thread's `...\n` produces ONE journal line built from TWO writers.
    #
    # A lock around the buffer does NOT fix that, and this is the part worth keeping: the splice
    # happens BETWEEN two write() calls, so any per-call lock is the wrong granularity. Measured
    # on DVo before and after — two threads, 300 partial+newline pairs each: 550 of 600 lines
    # spliced under the shared buffer, 0 under per-thread buffers. Keying the buffer to the
    # writer is what makes a journal line attributable to one of them.
    #
    # The lock is still here, for the dict itself and for close_copy's drain.
    def __init__(self, proxy, journal):
        self._proxy = proxy; self._journal = journal
        self._bufs = {}
        self._lock = threading.Lock()
    def write(self, d):
        n = self._proxy.write(d)
        key = threading.get_ident()
        with self._lock:
            buf = self._bufs.get(key, "") + d
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                self._emit(line)
            # drop the entry when it drains, so a long-lived process does not accumulate one
            # dead key per thread that ever wrote a partial line
            if buf: self._bufs[key] = buf
            else: self._bufs.pop(key, None)
        return n
    def _emit(self, line):
        line = _ANSI_RE.sub("", line.rsplit("\r", 1)[-1]).rstrip()
        if not line:
            return
        # An observer that can raise takes down the turn it was only watching. This copy is
        # never load-bearing: if the journal side is gone, the screen side has already run.
        try:
            self._journal.write(line + "\n"); self._journal.flush()
        except Exception:
            pass
    def flush(self):
        self._proxy.flush()
        try: self._journal.flush()
        except Exception: pass
    def close_copy(self):
        # Drain EVERY thread's tail, not just the caller's — the unwinding thread is the turn
        # loop, and the warmup thread's half-line is exactly as owed to the journal.
        with self._lock:
            for key in list(self._bufs):
                self._emit(self._bufs.pop(key))
    def isatty(self):
        return self._proxy.isatty()
    def __getattr__(self, name):
        if name.startswith("_"): raise AttributeError(name)
        return getattr(self.__dict__["_proxy"], name)

@contextlib.contextmanager
def journal_stderr_copy():
    # The test is `sys.stderr is sys.__stderr__`, NOT `_PTK` — it asks the question that
    # actually matters (has something taken fd 2 away) rather than a proxy for it. In the
    # nullcontext branch stderr already IS fd 2, and installing a copy there would double-write
    # every line into the tee #285 put on the unit.
    proxy = sys.stderr
    if sys.__stderr__ is None or proxy is sys.__stderr__:
        yield False; return
    tee = _JournalTee(proxy, sys.__stderr__)
    sys.stderr = tee
    try:
        yield True
    finally:
        tee.close_copy()
        sys.stderr = proxy

def main():
    # system message stays STATIC across turns (byte-identical prefix = KV cache hit);
    # live_context rides each user turn's tail instead (see user_turn()).
    if len(sys.argv)>2 and sys.argv[1]=="--once":
        msgs=[sysmsg(),user_turn(sys.argv[2])]; turn(msgs); return
    print("  \033[1mAgent OS brain\033[0m — I have hands, and I know the real now. Ask me to do things.")
    print(f"  \033[2m{escalate_status_line()}\033[0m")
    print("  \033[2mLost a window? Alt+Tab cycles them, or Super+/ opens the keybind cheatsheet.\033[0m")
    msgs=[sysmsg()]
    threading.Thread(target=warmup_greeting, args=(msgs,), daemon=True).start()
    # ^C must not kill the brain (P1 Dillon directive, msg 9263: "that'd be like losing your
    # desktop"). At the idle prompt, one ^C is a warning — exit needs a second ^C within ~2s
    # or the word exit/quit. During generation, ^C cancels that turn only (see the try/except
    # around turn() below) and drops back to the prompt.
    last_sigint=0.0
    # Input lock (slice 1): PromptSession owns the bottom line; patch_stdout(raw=True)
    # routes any print that happens WHILE the prompt is active (warmup thread, stray
    # background output) above it. raw=True keeps our ANSI styling/\r spinner codes
    # intact. During generation no prompt is active, so the streamer/spinner write
    # through unchanged. ^C semantics preserved: PromptSession.prompt raises
    # KeyboardInterrupt at the prompt exactly like input() does.
    if _PTK:
        _session=PromptSession()
        _read=lambda: _session.prompt(ANSI("\n\033[1;36myou ›\033[0m "))
        _guard=patch_stdout(raw=True)
    else:
        _read=lambda: input("\n\033[1;36myou ›\033[0m ")
        _guard=contextlib.nullcontext()
    # ExitStack instead of a `with` block so the whole existing loop keeps its indent
    # (minimal diff); closed after the loop to detach the stdout proxy cleanly.
    _stack=contextlib.ExitStack(); _stack.enter_context(_guard)
    # Entered AFTER the guard so it unwinds BEFORE it: sys.stderr is handed back to the
    # proxy while the proxy is still alive, and _stack.close() then detaches the proxy.
    _stack.enter_context(journal_stderr_copy())
    while True:
        try: u=_read().strip()
        except EOFError: print(); break
        except KeyboardInterrupt:
            now=time.time()
            if now-last_sigint<2: print(); break
            last_sigint=now
            print("\n\033[2m(^C again within 2s to exit, or type exit/quit)\033[0m")
            continue
        if not u: continue
        if u in ("exit","quit"): break
        if u.startswith(":expand"):
            parts=u.split()
            try: n=int(parts[1]) if len(parts)>1 else len(EXPAND_BUFFERS)
            except ValueError: n=0
            if 1<=n<=len(EXPAND_BUFFERS): print(EXPAND_BUFFERS[n-1])
            else: print(f"  \033[2m(no such buffer — have 1..{len(EXPAND_BUFFERS)})\033[0m")
            continue
        # ── `:summon` — the consent act for summon_claude (Rabbot door (i), 2026-09-02) ──
        # Typing it IS the consent, and typing it is the ONLY way to arm it. Bare `:summon`
        # reports status rather than arming, so a half-typed command cannot grant anything.
        if u.split()[0]==":summon":
            rest=u[len(":summon"):].strip()
            if not rest:
                st=("armed" if SUMMON_CONSENT.armed() else "not armed")
                print(f"  \033[2msummon_claude consent: {st} "
                      f"(one-shot, expires {_SUMMON_GRANT_TTL_S}s after `:summon <msg>`)\033[0m")
                continue
            SUMMON_CONSENT.arm()
            u=rest
        # ── `:escalate` — the per-turn consent act (task 323, Geist ruling 2026-08-22) ──
        # Typing it IS the consent; there is no other path from this prompt to the metered
        # provider. Bare `:escalate` reports status rather than arming anything, so a
        # half-typed command can never spend money.
        if u.split()[0]==":escalate":
            rest=u[len(":escalate"):].strip()
            if not rest:
                print(f"  \033[2m{escalate_status_line()}\033[0m")
                continue
            if not ESCALATE_STATUS["configured"]:
                print(f"  \033[33m(can't — {ESCALATE_STATUS['reason'] or 'escalate unavailable'})\033[0m")
                continue
            CONSENT.arm_turn()
            u=rest
        # Resolved BEFORE the turn and consumed here, so a per-turn grant can never leak into
        # the next turn even if this one raises.
        _turn_consent=CONSENT.consume()
        msgs.append(user_turn(u))
        # Bound the history BEFORE the turn, not after: the request about to go out is
        # the one that has to fit. Trimming afterwards would let the overflowing turn
        # ship first and only tidy up for the next one.
        trim_history(msgs)
        try:
            frontdoor_turn(msgs, _turn_consent)
        except KeyboardInterrupt:
            sys.stdout.write("\r\033[K")
            print("\033[2m(interrupted)\033[0m")
    _stack.close()
if __name__=="__main__": main()
