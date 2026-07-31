#!/usr/bin/env python3
"""broker-stub — a DETERMINISTIC stand-in for `bin/broker run`, for the agent-loop battery.

The loop battery proves LOOP MECHANICS, not wall SECURITY (bin/mcp and bin/broker each
carry their own property battery). So it drives the REAL bin/mcp — the structural JSON-RPC
front door — piped into THIS stub, which sits exactly where the real broker sits (downstream
of mcp's verdict stream) and returns SCRIPTED verdicts instead of making a real tier / taint /
registry / arg-schema decision. That lets the battery assert, with zero model and zero real
capability impls, that the loop:
  * marshals a well-formed JSON-RPC 2.0 tools/call that the REAL mcp accepts (a genuine
    integration signal: a loop bug that emitted a malformed request would be denied HERE),
  * discovers its tool surface from a `capabilities.list` data_result through the wall,
  * feeds a data_result back to the model as a content_type:"data" tool message, and
  * treats a broker deny as a fail-closed denial (the missing/garbled-wall arms of HARD
    REQ 4 are proven separately by the wall-smoke; here we prove the deny arm end-to-end).

It speaks the broker's EXACT wire contract (bin/broker: process / data_result / deny / emit):
read one mcp verdict dict per line; an mcp deny (ok:false) is passed through verbatim
(broker's "deny-passthrough"); a tools/call resolves to a data_result or a deny per the
config; every response is emitted as canonical compact-sorted JSON, one line each.

Config: AGENT_OS_BROKER_STUB names a JSON file:
  {"capabilities": [{"name":..,"tier":..,"summary":..}, ...],   # answered for capabilities.list
   "responses": {"<cap>": {"data": <content>, "capability_ok": <bool>},   # -> data_result
                 "<cap>": {"deny": "<message>"}}}                         # -> deny
  A tools/call name absent from `responses` (and not capabilities.list) denies
  "unknown-capability" — the real broker's behavior for an unregistered capability.

Invoked as `python3 broker-stub.py run` (argv ignored, like the real `broker run`).
Stateless: the loop runs ONE request per wall invocation (single-flight), so this reads a
short stream, answers each line, and exits — no cross-call state to keep.
"""
import json, os, sys

# A policy-deny code in the broker's family. The loop never asserts on the numeric code
# (attacker bytes are confined to data_result.content, never error.message), so the exact
# value is cosmetic — it exists only to shape a well-formed deny envelope.
E_DENY = -32000


def _cfg():
    path = os.environ.get("AGENT_OS_BROKER_STUB")
    if not path:
        return {"capabilities": [], "responses": {}}
    try:
        with open(path) as fh:
            c = json.load(fh)
    except (OSError, ValueError):
        return {"capabilities": [], "responses": {}}
    if not isinstance(c, dict):
        return {"capabilities": [], "responses": {}}
    if not isinstance(c.get("capabilities"), list):
        c["capabilities"] = []
    if not isinstance(c.get("responses"), dict):
        c["responses"] = {}
    return c


def deny(rid, message, code=E_DENY):
    return {"ok": False, "id": rid, "error": {"code": code, "message": message}}


def data_result(rid, capability_ok, content):
    # byte-for-byte the shape bin/broker:data_result emits (§4.9 content_type envelope).
    return {"ok": True, "id": rid,
            "result": {"content_type": "data", "capability_ok": bool(capability_ok),
                       "content": content}}


def emit(obj):
    # same canonical serialization discipline as bin/broker / bin/mcp: sort_keys +
    # ensure_ascii + allow_nan=False => one deterministic, single-line, 7-bit verdict.
    sys.stdout.write(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                ensure_ascii=True, allow_nan=False) + "\n")
    sys.stdout.flush()


def process(v, cfg):
    """v is a normalized mcp verdict dict. Return the response dict to emit."""
    rid = v.get("id")
    if v.get("ok") is not True:
        # mcp denied (e.g. a hostile tool name) — pass the deny through verbatim, exactly as
        # the real broker's deny-passthrough does; never re-parse or 'recover' it.
        err = v.get("error") if isinstance(v.get("error"), dict) else \
            {"code": E_DENY, "message": "denied"}
        return {"ok": False, "id": rid, "error": err}
    if v.get("method") != "tools/call":
        # the loop only ever emits tools/call; anything else is out of this stub's remit.
        return deny(rid, "stub handles tools/call only")
    name = v.get("name")
    if name == "capabilities.list":
        # the discovery call — content is a JSON STRING the loop json.loads() to read caps.
        return data_result(rid, True,
                           json.dumps({"capabilities": cfg["capabilities"]}, sort_keys=True))
    spec = cfg["responses"].get(name)
    if not isinstance(spec, dict):
        return deny(rid, "unknown-capability: %s" % name)
    if "deny" in spec:
        return deny(rid, str(spec["deny"]))
    return data_result(rid, spec.get("capability_ok", True), spec.get("data"))


def main():
    cfg = _cfg()
    for raw in sys.stdin:
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        try:
            v = json.loads(line)
        except ValueError:
            emit(deny(None, "malformed verdict line — shutting stream"))
            return 3
        if not isinstance(v, dict):
            emit(deny(None, "verdict is not an object — shutting stream"))
            return 3
        emit(process(v, cfg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
