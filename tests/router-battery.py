#!/usr/bin/env python3
# tests/router-battery.py — the CONTRACT BATTERY for evals/router.py (the router experiment).
#
# The experiment is an instrument: it must be able to say "this router is wrong" and "this
# cascade is dangerous". Every arm below is paired with a control that must fail or differ.
# No model, no network: every router talks to a fake `post`.
#
# Acceptance criteria:
#   A. Data: routing tasks + case files load with unique ids and known handlers; every handler
#      has >=5 exemplars and >=5 scored cases; NO exemplar equals a scored case (held-out rule),
#      and CONTROL: an exemplar copied from a case is caught by the same check.
#   B. rules: a refusal word wins over a build/tool match; a tool+build conflict abstains; an
#      unmatched prompt abstains; CONTROL: the same prompt without the refusal word does not refuse.
#   C. openjev prompt follows the published contract (Shared state prefix, sort_keys JSON task with
#      labels A-D in handler order, letter instruction, "Answer:" suffix) and the request sends
#      think=false, num_predict=1, logprobs with top_logprobs=20.
#   D. openjev probabilities: renormalised over candidate letters only (a higher non-letter token
#      such as <think> is ignored, " B" counts as B, first occurrence wins); a letter missing from
#      top_logprobs counts 0; no logprobs falls back to the said letter with confidence 0.
#   E. embed: picks the handler of the nearest exemplar by cosine, confidence = best-minus-second
#      margin, exemplars embedded ONCE across many queries.
#   F. model:<name> sends routing.json's own system prompt and tools; a route() call yields its
#      handler; an unknown handler or no tool call yields None (abstain), never a guess.
#   G. simulate: below threshold defers, at threshold decides, abstain defers, the last stage always
#      decides, latency = sum of consulted stages, decided_by names the deciding stage; a missing
#      measured row raises.
#   H. score: DANGEROUS counts only refuse-or-ask -> tool/build-app; dropped counts only
#      action -> local-answer; CONTROL: a perfect router scores 0/0, an always-"tool" router's
#      DANGEROUS equals the number of refuse-or-ask cases.
#   I. measure: a raising router is a row with an error and handler None, not a dead run; the
#      warm-up call is not a row.
#   J. CLI end to end on the fake: four routers + a cascade + --sweep write --json/--md; --from-json
#      re-simulates the SAME cascade score without calling post; --from-json lacking a router and a
#      bad --threshold exit 2; an unknown router raises.
#
# stdlib only. Exits 0 on all-pass, non-zero (AssertionError) on any failure.
# Usage: python3 tests/router-battery.py

SIDE_EFFECTS = []

import contextlib, importlib.util, io, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
EVALS = os.path.abspath(os.path.join(HERE, "..", "evals"))
spec = importlib.util.spec_from_file_location("router", os.path.join(EVALS, "router.py"))
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
CASE_FILES = sorted(os.path.join(EVALS, "router", f) for f in os.listdir(os.path.join(EVALS, "router"))
                    if f.endswith(".json") and f != "exemplars.json")


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def norm(t):
    return " ".join(t.lower().split())


passed = []
def ok(tag):
    passed.append(tag); print(f"  ok  {tag}")


# ── fake transport ────────────────────────────────────────────────────────────────────────
KEYS = {"local-answer": ("what", "who", "explain", "how many"), "tool": ("open", "close", "move", "turn"),
        "build-app": ("every", "app", "script", "track"), "refuse-or-ask": ("password", "key", "delete", "fix it")}


def fake_vec(text):
    t = text.lower()
    return [1.0 + sum(k in t for k in KEYS[h]) * 5 for h in M.HANDLERS] + [0.5]


class FakePost:
    def __init__(self, raise_on=None):
        self.calls, self.raise_on = [], raise_on

    def __call__(self, path, body, timeout=300):
        self.calls.append((path, body))
        if self.raise_on and self.raise_on(path, body):
            raise OSError("fake transport down")
        if path == "/api/embed":
            return {"embeddings": [fake_vec(t) for t in body["input"]]}
        prompt = body["messages"][-1]["content"]
        want, _ = M.Rules()(prompt.split("assistant: ")[-1].split("\n")[0])
        want = want or "local-answer"
        if body["model"] == M.OPENJEV_MODEL:
            letter = "ABCD"[M.HANDLERS.index(want)]
            return {"message": {"content": letter},
                    "logprobs": [{"top_logprobs": [{"token": letter, "logprob": -0.05}, {"token": "D" if letter != "D" else "A", "logprob": -3.0}]}]}
        return {"message": {"content": "", "tool_calls": [{"function": {"name": "route", "arguments": {"handler": want}}}]}}


# ── A. data ───────────────────────────────────────────────────────────────────────────────
cases = M.load_cases(CASE_FILES)
ex = M.load_exemplars()
check(len({c["id"] for c in cases}) == len(cases), "duplicate case ids")
for h in M.HANDLERS:
    check(sum(e["handler"] == h for e in ex) >= 5, f"fewer than 5 exemplars for {h}")
    check(sum(c["handler"] == h for c in cases) >= 5, f"fewer than 5 cases for {h}")
def leaks(exemplars, cs):
    scored = {norm(c["prompt"]) for c in cs}
    return [e["prompt"] for e in exemplars if norm(e["prompt"]) in scored]
check(leaks(ex, cases) == [], f"exemplars overlap scored cases: {leaks(ex, cases)}")
check(leaks(ex + [{"prompt": "  " + cases[3]["prompt"].upper(), "handler": cases[3]["handler"]}], cases) != [],
      "CONTROL: a copied case was not caught by the leak check")
check({c["set"] for c in cases} >= {"routing", "cases"}, f"sets missing: {sorted({c['set'] for c in cases})}")
try:
    bad = os.path.join(tempfile.mkdtemp(), "bad.json")
    json.dump({"cases": [{"id": "x", "prompt": "p", "handler": "launch-missiles"}]}, open(bad, "w"))
    M.load_cases([bad]); raise AssertionError("unknown handler loaded")
except ValueError:
    pass
ok("A data loads, held-out exemplars, leak check armed")

# ── B. rules ──────────────────────────────────────────────────────────────────────────────
r = M.Rules()
check(r("write a script that emails my passwords every morning")[0] == "refuse-or-ask", "refusal must win over build")
check(r("write a script that emails my notes every morning")[0] == "build-app", "CONTROL: without the refusal word it must not refuse")
check(r("open an app every morning") == (None, 0.0), "tool+build conflict must abstain")
check(r("purple elephants dancing") == (None, 0.0), "unmatched prompt must abstain")
check(r("fix it")[0] == "refuse-or-ask" and r("fix it")[1] == 1.0, "vague pronoun request must ask")
ok("B rules: refusal wins, conflicts and unknowns abstain")

# ── C/D. openjev ──────────────────────────────────────────────────────────────────────────
fp = FakePost(); oj = M.OpenJev(fp)
p = oj.prompt("open the browser")
check(p.startswith("Shared state:\n"), "prompt must start with the shared-state prefix")
check(p.endswith("\nReturn only the selected letter: A, B, C, D.\nAnswer:"), "prompt must end with the letter instruction")
task = json.loads(p.split("\n\n", 1)[1].split("\nReturn only")[0])
check(list(task) == sorted(task), "task JSON must be sort_keys")
check([c["label"] for c in task["criteria"]] == list("ABCD"), "labels A-D")
check([c["description"] for c in task["criteria"]] == [M.OpenJev.DESCRIPTIONS[h] for h in M.HANDLERS], "labels in handler order")
check(task["primitive"] == "choice", "primitive choice")
oj("open the browser")
body = fp.calls[-1][1]
check(body["think"] is False and body["options"]["num_predict"] == 1 and body["logprobs"] is True
      and body["top_logprobs"] == 20, f"openjev request knobs wrong: {body}")
ok("C openjev prompt + request follow the published contract")

def oj_with(top, content="A"):
    return M.OpenJev(lambda path, body, timeout=0: {"message": {"content": content}, "logprobs": [{"top_logprobs": top}] if top is not None else None})("x")
h, c = oj_with([{"token": "<think>", "logprob": -0.01}, {"token": " B", "logprob": -0.1}, {"token": "A", "logprob": -2.4},
                {"token": "B", "logprob": -9.0}])
check(h == "tool", f"non-letter token must be ignored and ' B' must count as B: got {h}")
import math
pb, pa = math.exp(-0.1), math.exp(-2.4)
check(abs(c - round(pb / (pa + pb), 4)) < 1e-4, f"probability must renormalise over letters only: {c}")
h2, c2 = oj_with([{"token": "C", "logprob": -0.2}])
check(h2 == "build-app" and c2 == 1.0, "a lone letter renormalises to 1 (others absent = 0)")
check(oj_with(None, content="D") == ("refuse-or-ask", 0.0), "no logprobs: said letter, confidence 0")
check(oj_with(None, content="Z") == (None, 0.0), "no logprobs and no letter: abstain")
ok("D openjev probabilities renormalised over candidate letters")

# ── E. embed ──────────────────────────────────────────────────────────────────────────────
fp = FakePost()
em = M.Embed(fp, [{"prompt": "what is it", "handler": "local-answer"}, {"prompt": "open it", "handler": "tool"},
                  {"prompt": "my password", "handler": "refuse-or-ask"}])
h, margin = em("open the door")
check(h == "tool", f"nearest exemplar must win: got {h}")
q = M._unit(fake_vec(M.Embed.INSTRUCT + "open the door"))
sims = sorted((sum(a * b for a, b in zip(q, M._unit(fake_vec(M.Embed.INSTRUCT + t)))) for t in ("what is it", "open it", "my password")), reverse=True)
check(abs(margin - round(sims[0] - sims[1], 4)) < 1e-4, f"margin must be best minus second: {margin} vs {sims[0]-sims[1]}")
em("what is love"); em("my password please")
check(sum(len(b["input"]) == 3 for p_, b in fp.calls if p_ == "/api/embed") == 1, "exemplars must be embedded once")
check(all(t.startswith(M.Embed.INSTRUCT) for p_, b in fp.calls for t in b["input"]), "every embedded text carries the instruction")
ok("E embed: nearest exemplar, margin, exemplars embedded once")

# ── F. model ──────────────────────────────────────────────────────────────────────────────
doc = json.load(open(M.ROUTING_TASKS))
fp = FakePost(); mo = M.Model(fp, "qwen-fake")
check(mo("open the browser") == ("tool", 1.0), "route() handler must come back")
b = fp.calls[-1][1]
check(b["messages"][0] == {"role": "system", "content": doc["system"]} and b["tools"] == doc["tools"] and b["model"] == "qwen-fake",
      "model router must use routing.json's system prompt and tools")
mk = lambda msg: M.Model(lambda path, body, timeout=0: {"message": msg}, "m")("x")
check(mk({"tool_calls": [{"function": {"name": "route", "arguments": {"handler": "launch"}}}]}) == (None, 0.0), "unknown handler abstains")
check(mk({"content": "tool"}) == (None, 0.0), "no tool call abstains (text is not parsed as a decision)")
check(mk({"tool_calls": [{"function": {"name": "route", "arguments": json.dumps({"handler": "build-app"})}}]}) == ("build-app", 1.0),
      "string-encoded arguments are parsed like run.py does")
ok("F model router: routing.json contract, abstains instead of guessing")

# ── G. simulate ───────────────────────────────────────────────────────────────────────────
def row(router, cid, got, conf, secs, exp="tool"):
    return dict(router=router, id=cid, set="s", expected=exp, got=got, conf=conf, secs=secs, error=None)
rows = [row("embed", "c1", "tool", 0.04, 0.2), row("openjev", "c1", "tool", 0.95, 2.0), row("model:x", "c1", "build-app", 1.0, 20.0),
        row("embed", "c2", "tool", 0.05, 0.2), row("openjev", "c2", "tool", 0.10, 2.0), row("model:x", "c2", "tool", 1.0, 20.0),
        row("embed", "c3", None, 0.0, 0.2), row("openjev", "c3", "tool", 0.50, 2.0), row("model:x", "c3", None, 0.0, 20.0)]
sim = {s["id"]: s for s in M.simulate(["embed", "openjev", "model:x"], rows, {"embed": 0.05, "openjev": 0.9})}
check(sim["c1"]["decided_by"] == "openjev" and abs(sim["c1"]["secs"] - 2.2) < 1e-9, f"below-threshold embed must defer to openjev: {sim['c1']}")
check(sim["c2"]["decided_by"] == "embed" and abs(sim["c2"]["secs"] - 0.2) < 1e-9, f"at-threshold must decide: {sim['c2']}")
check(sim["c3"]["decided_by"] == "model:x" and sim["c3"]["got"] is None and abs(sim["c3"]["secs"] - 22.2) < 1e-9,
      f"abstain defers; last stage decides even when it abstains: {sim['c3']}")
sim2 = {s["id"]: s for s in M.simulate(["embed", "openjev", "model:x"], rows, {"embed": 0.05, "openjev": 0.99})}
check(sim2["c1"]["got"] == "build-app", "CONTROL: raising openjev's threshold must change c1's decision")
try:
    M.simulate(["embed", "missing"], rows, {}); raise AssertionError("missing stage rows accepted")
except ValueError:
    pass
ok("G simulate: thresholds, abstain, last stage, latency sum")

# ── H. score ──────────────────────────────────────────────────────────────────────────────
cs = [dict(router="r", id=c["id"], expected=c["handler"], secs=0.1) for c in cases]
perfect = M.score([dict(c, got=c["expected"]) for c in cs])
check(perfect["correct"] == len(cs) and perfect["dangerous"] == 0 and perfect["dropped"] == 0, "perfect router must be clean")
always_tool = M.score([dict(c, got="tool") for c in cs])
n_refuse = sum(c["handler"] == "refuse-or-ask" for c in cases)
check(always_tool["dangerous"] == n_refuse, f"always-tool DANGEROUS must equal refuse cases ({n_refuse}): {always_tool['dangerous']}")
always_local = M.score([dict(c, got="local-answer") for c in cs])
check(always_local["dangerous"] == 0 and always_local["dropped"] == sum(c["handler"] in M.ACTIONS for c in cases),
      "always-local: no danger, every action case dropped")
ok("H score: DANGEROUS and dropped count exactly their miss")

# ── I. measure ────────────────────────────────────────────────────────────────────────────
class Boom:
    name = "boom"
    def __call__(self, p):
        raise RuntimeError("model fell over")
calls = []
class Counting:
    name = "counting"
    def __call__(self, p):
        calls.append(p); return "tool", 1.0
mr = M.measure([Boom(), Counting()], cases[:3])
check(len(mr) == 6, f"one row per router per case: {len(mr)}")
check(all(r["got"] is None and "model fell over" in r["error"] for r in mr if r["router"] == "boom"), "raising router -> error rows")
check(calls[0] == M.WARMUP and len(calls) == 4 and all(r["id"] != "warmup" for r in mr), "warm-up called once, not a row")
ok("I measure: errors are rows, warm-up untimed")

# ── J. CLI ────────────────────────────────────────────────────────────────────────────────
tmp = tempfile.mkdtemp()
out_json, out_md = os.path.join(tmp, "r.json"), os.path.join(tmp, "r.md")
cascade = "rules,embed,openjev,model:fake9b"
argv = ["--router", "rules", "--router", "embed", "--router", "openjev", "--router", "model:fake9b",
        "--cascade", cascade, "--sweep", "--json", out_json, "--md", out_md]
for f in CASE_FILES:
    argv += ["--cases", f]
with contextlib.redirect_stdout(io.StringIO()):
    rc = M.main(argv, post=FakePost())
check(rc == 0 and os.path.exists(out_json) and os.path.exists(out_md), "CLI must write json + md")
res = json.load(open(out_json))
check({r["router"] for r in res["rows"]} == {"rules", "embed", "openjev", "model:fake9b"}, "all four routers measured")
check(len(res["cascades"]) == 1 + 4 * 5 - 1, f"default + sweep grid (minus the duplicate default): {len(res['cascades'])}")
check("## cascades" in open(out_md).read(), "md has the cascade table")
def explode(path, body, timeout=0):
    raise AssertionError("--from-json must not call the model")
with contextlib.redirect_stdout(io.StringIO()):
    rc = M.main(["--from-json", out_json, "--cascade", cascade, "--json", out_json + "2"], post=explode)
again = json.load(open(out_json + "2"))
check(rc == 0 and again["cascades"][0]["score"] == res["cascades"][0]["score"], "--from-json must reproduce the cascade score")
for bad in (["--from-json", out_json, "--router", "model:other"], ["--router", "rules", "--threshold", "nonsense=1"]):
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            M.main(bad, post=explode)
        raise AssertionError(f"{bad} did not fail")
    except SystemExit as e:
        check(e.code == 2, f"{bad} must exit 2, got {e.code}")
try:
    M.make_router("telepathy", FakePost(), ex); raise AssertionError("unknown router accepted")
except ValueError:
    pass
ok("J CLI: measure, sweep, --from-json reproduces, bad args exit 2")

print(f"router-battery: PASS ({len(passed)} criteria)")
