#!/usr/bin/env python3
"""evals/run.py — model-agnostic eval runner for Agent OS (skeleton, 2026-10).

Runs task files (evals/tasks/*.json) against a backend and reports pass/fail, latency and
per-task variance. The score is REPORTED, never a gate: this is the instrument the weekly
model watch and the router experiment read, not a merge check.

    evals/run.py --backend fake                         # replay canned answers, no model
    evals/run.py --backend ollama --model qwen3.5:9b    # a real local model
    evals/run.py --runs 3 --family tool-use --json out.json --md out.md

Every task is a tool-calling turn: the model gets a system prompt, optional context, a
closed tool list, and must answer with tool calls. The expectation is a declarative matcher
(see README: no code in task files). Families today: tool-use, routing, app-generation.

stdlib only (same rule as bin/brain-ollama): python3 + urllib.
"""
import argparse, glob, json, os, statistics, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TASKS = os.path.join(HERE, "tasks")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")


# ── task loading ──────────────────────────────────────────────────────────────────────────
def load_tasks(path, family=None):
    files = sorted(glob.glob(os.path.join(path, "*.json"))) if os.path.isdir(path) else [path]
    tasks = []
    for f in files:
        doc = json.load(open(f))
        fam = doc["family"]
        if family and fam != family:
            continue
        for t in doc["tasks"]:
            t = dict(t)
            t["family"] = fam
            t.setdefault("tools", doc.get("tools", []))
            t.setdefault("system", doc.get("system", ""))
            t["_file"] = os.path.basename(f)
            if "id" not in t or "expect" not in t or "prompt" not in t:
                raise ValueError(f"{f}: task needs id/prompt/expect: {t.get('id')}")
            tasks.append(t)
    return tasks


# ── backends: callable(task) -> assistant message dict {"content": str, "tool_calls": [...]} ──
def fake_backend(task):
    """Replays task['fake'] (the canned CORRECT answer). Exists so the runner itself can be
    tested with no model; a task without 'fake' yields an empty answer (scores 0)."""
    return task.get("fake", {"content": "", "tool_calls": []})


def make_ollama_backend(model, timeout=180):
    def backend(task):
        msgs = [{"role": "system", "content": task["system"]}]
        user = task["prompt"] if not task.get("context") else task["context"] + "\n\nRequest: " + task["prompt"]
        msgs.append({"role": "user", "content": user})
        body = {"model": model, "messages": msgs, "tools": task["tools"], "stream": False,
                "think": False, "options": {"temperature": 0.1, "num_ctx": 4096}, "keep_alive": "30m"}
        req = urllib.request.Request(OLLAMA_HOST + "/api/chat", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=timeout))["message"]
    return backend


BACKENDS = {"fake": lambda model: fake_backend, "ollama": make_ollama_backend}


# ── matchers (declarative, no code in task files) ─────────────────────────────────────────
def _args(call):
    a = call.get("function", {}).get("arguments", {})
    if isinstance(a, str):
        try: a = json.loads(a)
        except Exception: a = {}
    return a if isinstance(a, dict) else {}


def _name(call):
    return call.get("function", {}).get("name")


def _lower(v):
    return v.lower() if isinstance(v, str) else v


def match_value(spec, v):
    """spec is a dict of ops over one argument value. All ops must hold."""
    for op, want in spec.items():
        if op == "present":
            if (v not in (None, "")) != bool(want): return False
        elif op == "absent":
            if (v in (None, "")) != bool(want): return False
        elif op == "eq":
            if _lower(v) != _lower(want) and str(v) != str(want): return False
        elif op == "in":
            if not any(_lower(v) == _lower(w) or str(v) == str(w) for w in want): return False
        elif op == "contains":
            if not (isinstance(v, str) and want.lower() in v.lower()): return False
        elif op == "any_contains":
            if not (isinstance(v, str) and any(w.lower() in v.lower() for w in want)): return False
        elif op == "none_contains":
            if isinstance(v, str) and any(w.lower() in v.lower() for w in want): return False
        elif op == "type":
            ok = {"int": lambda x: isinstance(x, int) and not isinstance(x, bool) or (isinstance(x, str) and x.isdigit()),
                  "str": lambda x: isinstance(x, str), "list": lambda x: isinstance(x, list),
                  "dict": lambda x: isinstance(x, dict)}[want]
            if not ok(v): return False
        elif op == "max_len":
            if v is None: v = []
            if not isinstance(v, (list, str, dict)) or len(v) > want: return False
        elif op == "min_len":
            if not isinstance(v, (list, str, dict)) or len(v) < want: return False
        elif op == "subset_of":
            if not isinstance(v, list) or not all(_lower(x) in [_lower(w) for w in want] for x in v): return False
        elif op == "all_items":   # every list item (dict) satisfies {field: spec}
            if not isinstance(v, list): return False
            for item in v:
                if not isinstance(item, dict): return False
                for field, s in want.items():
                    if not match_value(s, item.get(field)): return False
        elif op == "no_item_contains":   # no list item's string field contains any of the words
            if isinstance(v, list):
                for item in v:
                    s = item.get(want["field"]) if isinstance(item, dict) else item
                    if isinstance(s, str) and any(w.lower() in s.lower() for w in want["words"]): return False
        elif op == "keys_required":
            if not isinstance(v, dict) or any(k not in v for k in want): return False
        elif op == "fields":   # nested: {subkey: spec} over a dict value
            if not isinstance(v, dict): return False
            for k, s in want.items():
                if not match_value(s, v.get(k)): return False
        else:
            raise ValueError(f"unknown matcher op {op!r}")
    return True


def match_call(spec, call):
    if _name(call) != spec["tool"]:
        return False
    return all(match_value(s, _args(call).get(k)) for k, s in spec.get("args", {}).items())


def matches(expect, calls):
    """expect: {"call": {...}} some call matches | {"first_call": {...}} the first call matches
    | {"no_call": "name"} no such call | {"any_of": [...]} | {"all_of": [...]} | {"no_calls": true}."""
    if "any_of" in expect: return any(matches(e, calls) for e in expect["any_of"])
    if "all_of" in expect: return all(matches(e, calls) for e in expect["all_of"])
    if "call" in expect: return any(match_call(expect["call"], c) for c in calls)
    if "first_call" in expect: return bool(calls) and match_call(expect["first_call"], calls[0])
    if "no_call" in expect: return not any(_name(c) == expect["no_call"] for c in calls)
    if "no_calls" in expect: return (not calls) == bool(expect["no_calls"])
    raise ValueError(f"unknown expect shape: {list(expect)}")


# ── running ───────────────────────────────────────────────────────────────────────────────
def evaluate(tasks, backend, runs=1):
    rows = []
    for r in range(runs):
        for t in tasks:
            t0 = time.time()
            try:
                msg = backend(t) or {}
                calls = msg.get("tool_calls") or []
                err = None
            except Exception as e:   # a backend error is a FAILED task, not a dead run
                msg, calls, err = {}, [], f"{type(e).__name__}: {e}"
            dt = time.time() - t0
            ok = (not err) and bool(matches(t["expect"], calls))
            rows.append(dict(run=r, id=t["id"], family=t["family"], ok=ok, secs=round(dt, 3),
                             calls=[{"name": _name(c), "args": _args(c)} for c in calls],
                             content=(msg.get("content") or "")[:300], error=err))
    return rows


def summarize(rows, runs):
    fams = sorted({r["family"] for r in rows})
    out = {"runs": runs, "families": {}, "tasks": {}}
    for f in fams:
        fr = [r for r in rows if r["family"] == f]
        lat = sorted(r["secs"] for r in fr)
        out["families"][f] = dict(passed=sum(r["ok"] for r in fr), total=len(fr),
                                  median_s=round(statistics.median(lat), 3) if lat else None,
                                  p95_s=round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 3) if lat else None)
    for r in rows:
        t = out["tasks"].setdefault(r["id"], dict(family=r["family"], passed=0, runs=0, secs=[]))
        t["passed"] += r["ok"]; t["runs"] += 1; t["secs"].append(r["secs"])
    for t in out["tasks"].values():
        t["variance"] = "stable" if t["passed"] in (0, t["runs"]) else "flaky"
    out["passed"] = sum(r["ok"] for r in rows); out["total"] = len(rows)
    return out


def to_markdown(summary, rows, label):
    L = [f"# eval: {label}", "", f"runs: {summary['runs']}  score: {summary['passed']}/{summary['total']}", "",
         "| family | passed | median s | p95 s |", "|---|---|---|---|"]
    for f, s in summary["families"].items():
        L.append(f"| {f} | {s['passed']}/{s['total']} | {s['median_s']} | {s['p95_s']} |")
    L += ["", "| task | family | passed/runs | variance | secs |", "|---|---|---|---|---|"]
    for tid, t in summary["tasks"].items():
        L.append(f"| {tid} | {t['family']} | {t['passed']}/{t['runs']} | {t['variance']} | {' '.join(str(s) for s in t['secs'])} |")
    fails = [r for r in rows if not r["ok"]]
    if fails:
        L += ["", "## failures", ""]
        for r in fails:
            L.append(f"- run {r['run']} `{r['id']}`: calls={json.dumps(r['calls'])[:200]} "
                     f"{('error=' + r['error']) if r['error'] else ''} {('content=' + r['content'][:100]) if r['content'] else ''}")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", choices=sorted(BACKENDS), default="fake")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--tasks", default=DEFAULT_TASKS, help="task dir or one task file")
    ap.add_argument("--family", default=None)
    ap.add_argument("--json", default=None, help="write full results here")
    ap.add_argument("--md", default=None, help="write a markdown report here")
    a = ap.parse_args(argv)
    tasks = load_tasks(a.tasks, a.family)
    if not tasks:
        print("no tasks", file=sys.stderr); return 2
    backend = BACKENDS[a.backend](a.model)
    label = f"{a.backend}" + (f" {a.model}" if a.backend != "fake" else "")
    rows = evaluate(tasks, backend, a.runs)
    summary = summarize(rows, a.runs)
    md = to_markdown(summary, rows, label)
    print(md, end="")
    if a.json:
        json.dump({"label": label, "when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "summary": summary, "rows": rows}, open(a.json, "w"), indent=1)
    if a.md:
        open(a.md, "w").write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
