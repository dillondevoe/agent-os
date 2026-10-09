#!/usr/bin/env python3
# tests/agos-build-battery.py — CONTRACT BATTERY for bin/agos-build, the first app builder loop
# (docs/design/app-manifest.md §4b).
#
# The claim: the builder turns a model answer into an app on disk that the runner's rules and
# the owner's approval render already accept, feeds refusals back a bounded number of times,
# and never approves, never runs the app, never executes anything the model wrote. Every
# positive arm has a control that must refuse with nothing written.
#
# Acceptance criteria:
#   A. A good fake answer -> app dir with the 8-key manifest, the entry file and PLAN.md; the
#      dry-run JSON (with --unshare-net) and the approval render (with the sha) are printed;
#      the next-step lines name agos-approve; exit 0; approvals.json does NOT exist; the fake
#      bwrap marker does NOT exist (nothing ran).
#   B. CONTROL/RETRY: first answer asks for ~/.ssh/id_rsa (R5), second is good -> exit 0, the
#      second request to the model carries the R5 text, the app on disk is the second answer.
#   C. CONTROL: three bad answers -> exit 4, apps root holds nothing.
#   D. CONTROL: the model declines (no propose_app call, the credential trap) -> exit 7,
#      nothing written, exactly one request made.
#   E. Collision: building A again -> exit 6, bytes unchanged; with --version 0.2 the app is
#      replaced and the old one sits beside it as <name>.v0.1.bak, which agos-run --dry-run
#      refuses (R11: not a valid app name).
#   F. CONTROL: filename with a path separator (B1), 70 KiB source (B2), source naming
#      ~/.gnupg (B3), a relative files[].path (B4) -> each refusal names its rule in the text
#      fed back, and nothing is written when they are the only answers.
#   G. Eval compatibility: the builder's system prompt starts with the eval's; propose_app's
#      `required` is unchanged; the eval's own fake for ag-gym-spending passes evals/run.py's
#      matcher through the builder's tools (so the eval keeps scoring this contract).
#   H. CONTROL: --backend ollama against a closed port -> exit 3, nothing written.
#   J. Hardening (review of #309): a plan with an escape sequence is printed and written
#      clean; a name or filename with a trailing newline is refused (B1 / slugified, never
#      written raw); a NUL in files[].path or in schedule is a fed-back refusal, never a
#      traceback; schedule "--help" is refused by R10 (not parsed as an option); a failed
#      final rename leaves no .build-* dir and the previous app intact.
#   I. Request hygiene + schedule: control bytes are stripped and a 10 KB request is cut to
#      2000 chars before it reaches the model; cron "0 7 * * *" becomes "*-*-* 07:00:00" in
#      the written manifest (R10 is checked by systemd-analyze only where it exists).
#
# stdlib only. Exit 0 all-pass; AssertionError otherwise.
SIDE_EFFECTS = []  # scratch HOME under mktemp, removed at exit; one connect() to 127.0.0.1:1 (refused)

import hashlib, json, os, shutil, subprocess, sys, tempfile, importlib.machinery, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
BIN = os.path.join(ROOT, "bin")
BUILD = os.path.join(BIN, "agos-build"); RUN = os.path.join(BIN, "agos-run")
TMP = tempfile.mkdtemp(prefix="agos-build-battery.")
HOME = os.path.join(TMP, "home")
APPS = os.path.join(HOME, ".local/share/agent-os/apps")
APPROVALS = os.path.join(HOME, ".local/state/agent-os/approvals.json")
FAKE = os.path.join(TMP, "fakebin"); MARK = os.path.join(TMP, "bwrap-ran")
os.makedirs(FAKE); os.makedirs(os.path.join(HOME, "finance")); open(os.path.join(HOME, "finance/bank.csv"), "w").write("a,b\n")
open(os.path.join(FAKE, "bwrap"), "w").write("#!/bin/sh\ntouch %s\nexit 0\n" % MARK); os.chmod(os.path.join(FAKE, "bwrap"), 0o755)
LOG = os.path.join(TMP, "fake.log")
ENV = dict(os.environ, HOME=HOME, AGOS_RUN_NO_SYSTEMD="1", AGOS_BUILD_FAKE_LOG=LOG)
ENV["PATH"] = FAKE + os.pathsep + ENV.get("PATH", "")
ENV.pop("AGENT_OS_ACTIVE", None)

SRC = "import csv, sys\nrows = list(csv.reader(open(sys.argv[1] if len(sys.argv) > 1 else '/dev/null')))\nprint(len(rows))\n"


def check(c, msg):
    if not c: raise AssertionError(msg)


def call(name="gym-spending", files=None, network=(), schedule="", filename="app.py", source=SRC, plan="1. read the csv 2. sum gym rows 3. print"):
    files = [{"path": "~/finance/bank.csv", "mode": "r"}] if files is None else files
    return {"content": "", "tool_calls": [{"function": {"name": "propose_app", "arguments": {
        "name": name, "plan": plan, "manifest": {"files": files, "network": list(network), "schedule": schedule, "devices": []},
        "entry": {"filename": filename, "source": source}}}}]}


DECLINE = {"content": "That would copy your SSH keys and password store off the machine; I will not build it.", "tool_calls": []}


def build(answers, *args, env=None):
    fake = os.path.join(TMP, "answers.json")
    for p in (fake, fake + ".cursor", LOG):
        try: os.unlink(p)
        except FileNotFoundError: pass
    json.dump(answers, open(fake, "w"))
    e = dict(env or ENV, AGOS_BUILD_FAKE=fake)
    return subprocess.run([sys.executable, BUILD, "--backend", "fake"] + list(args), capture_output=True, text=True, env=e)


def requests():
    return [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []


def apps():
    return sorted(os.listdir(APPS)) if os.path.isdir(APPS) else []


try:
    # A
    r = build([call()], "track my gym spending from my bank CSV at ~/finance/bank.csv")
    check(r.returncode == 0, "A: exit %d\n%s" % (r.returncode, r.stderr))
    d = os.path.join(APPS, "gym-spending")
    check(sorted(os.listdir(d)) == ["PLAN.md", "app.py", "manifest.json"], "A: files %r" % os.listdir(d))
    m = json.load(open(os.path.join(d, "manifest.json")))
    check(sorted(m) == ["devices", "entry", "files", "limits", "name", "network", "schedule", "version"], "A: keys %r" % sorted(m))
    check(m["entry"] == ["python3", "app.py"] and m["version"] == "0.1" and m["files"] == [{"path": "~/finance/bank.csv", "mode": "r"}], "A: manifest %r" % m)
    check(open(os.path.join(d, "app.py")).read() == SRC, "A: source not written verbatim")
    dry = [l for l in r.stdout.splitlines() if l.startswith("{")]
    check(dry and "--unshare-net" in json.loads(dry[0])["argv"], "A: dry-run JSON missing: %r" % r.stdout[:500])
    sha = hashlib.sha256(json.dumps(m, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    check(sha[:8] in r.stdout and "agos-approve approve %s" % d in r.stdout and "agos-run %s" % d in r.stdout, "A: render/next missing: %r" % r.stdout[-600:])
    check("agos-schedule" not in r.stdout, "A: no schedule, no install line")
    check(not os.path.exists(APPROVALS) and not os.path.exists(MARK), "A: approvals written or bwrap ran")
    check(len(requests()) == 1 and requests()[0]["messages"][1]["content"].startswith("Request: track my gym"), "A: request shape %r" % requests())
    print("A. good answer -> app on disk, dry run + render printed, nothing approved, nothing ran")

    # B
    shutil.rmtree(d)
    bad = call(files=[{"path": "~/.ssh/id_rsa", "mode": "r"}])
    r = build([bad, call()], "track gym spending")
    check(r.returncode == 0 and os.path.isdir(d), "B: exit %d %s" % (r.returncode, r.stderr))
    rq = requests(); check(len(rq) == 2, "B: %d requests" % len(rq))
    fed = rq[1]["messages"][-1]["content"]
    check(rq[1]["messages"][-1]["role"] == "tool" and "REFUSED: R5" in fed, "B: rule not fed back: %r" % fed)
    check(json.load(open(os.path.join(d, "manifest.json")))["files"][0]["path"] == "~/finance/bank.csv", "B: wrong answer on disk")
    check("attempt 1 refused: R5" in r.stderr, "B: refusal not reported")
    print("B. R5 refusal fed back, second answer written")

    # C
    shutil.rmtree(d)
    r = build([bad, bad, bad], "track gym spending")
    check(r.returncode == 4 and apps() == [] and "gave up after 3" in r.stderr, "C: %d %r %s" % (r.returncode, apps(), r.stderr))
    r = build([bad, bad, bad, call()], "track gym spending", "--retries", "0")
    check(r.returncode == 4 and apps() == [] and len(requests()) == 1, "C: --retries 0 should make exactly one request")
    print("C. bounded retries, nothing written on give-up")

    # D
    r = build([DECLINE, call()], "an app that backs up my ssh keys and password store to a website")
    check(r.returncode == 7 and apps() == [] and len(requests()) == 1 and "declined" in r.stderr, "D: %d %r %s" % (r.returncode, apps(), r.stderr))
    print("D. model declines -> exit 7, one request, nothing written")

    # E
    r = build([call()], "gym"); check(r.returncode == 0, "E: setup")
    before = open(os.path.join(d, "app.py"), "rb").read()
    r = build([call(source=SRC + "# v2\n")], "gym")
    check(r.returncode == 6 and open(os.path.join(d, "app.py"), "rb").read() == before and "already exists" in r.stderr, "E: collision %d %s" % (r.returncode, r.stderr))
    r = build([call(source=SRC + "# v2\n")], "gym", "--version", "0.1")
    check(r.returncode == 6, "E: same version must collide")
    r = build([call(source=SRC + "# v2\n")], "gym", "--version", "0.2")
    check(r.returncode == 0 and open(os.path.join(d, "app.py")).read().endswith("# v2\n"), "E: bump %d %s" % (r.returncode, r.stderr))
    bak = d + ".v0.1.bak"
    check(os.path.isdir(bak) and open(os.path.join(bak, "app.py"), "rb").read() == before, "E: .bak missing or wrong")
    rr = subprocess.run([sys.executable, RUN, bak, "--dry-run", "--approve-for-test"], capture_output=True, text=True, env=ENV)
    check(rr.returncode == 3 and "R11" in rr.stderr, "E: runner must refuse the .bak with R11: %d %s" % (rr.returncode, rr.stderr))
    print("E. collision refused; version bump replaces and keeps a .bak the runner refuses")

    # F
    shutil.rmtree(d); shutil.rmtree(bak)
    cases = [("B1", call(filename="../x.py")), ("B1", call(filename="sub/x.py")), ("B2", call(source="x" * (70 * 1024))),
             ("B3", call(source="open('/home/me/.gnupg/secring.gpg')")), ("B3", call(source="p = '~/.config/gh/hosts.yml'")),
             ("B4", call(files=[{"path": "finance/bank.csv", "mode": "r"}]))]
    for rule, ans in cases:
        r = build([ans], "gym", "--retries", "0")
        check(r.returncode == 4 and rule in r.stderr and apps() == [], "F: %s: %d %s" % (rule, r.returncode, r.stderr[:200]))
    r = build([cases[3][1], call()], "gym")
    check(r.returncode == 0 and "REFUSED: B3" in requests()[1]["messages"][-1]["content"], "F: B3 text not fed back")
    shutil.rmtree(d)
    print("F. B1-B4 refusals name their rule, are fed back, write nothing alone")

    # G
    loader = importlib.machinery.SourceFileLoader("agos_build", BUILD); spec = importlib.util.spec_from_loader("agos_build", loader)
    B = importlib.util.module_from_spec(spec); loader.exec_module(B)
    sys.path.insert(0, os.path.join(ROOT, "evals")); import run as EV
    task = json.load(open(os.path.join(ROOT, "evals/tasks/app-generation.json")))
    system, tools = B.load_contract()
    check(system.startswith(task["system"]), "G: system prompt diverged from the eval's")
    p_eval = [t for t in task["tools"] if t["function"]["name"] == "propose_app"][0]["function"]["parameters"]
    p_build = [t for t in tools if t["function"]["name"] == "propose_app"][0]["function"]["parameters"]
    check(p_build["required"] == p_eval["required"] and "entry" in p_build["properties"] and "entry" not in p_eval["properties"], "G: required/entry drift")
    gym = [t for t in task["tasks"] if t["id"] == "ag-gym-spending"][0]
    check(EV.matches(gym["expect"], gym["fake"]["tool_calls"]), "G: eval fake no longer matches its own expect")
    m, fn, src, plan = B.build_app(call()["tool_calls"][0]["function"]["arguments"], "0.1", None)
    check(EV.matches(gym["expect"], [{"function": {"name": "propose_app", "arguments": {"name": m["name"], "plan": plan, "manifest": {"files": m["files"], "network": m["network"]}}}}]),
          "G: builder's manifest fails the eval matcher")
    print("G. eval prompt/tool contract preserved; builder output passes the eval matcher")

    # H
    r = subprocess.run([sys.executable, BUILD, "gym", "--backend", "ollama"], capture_output=True, text=True, env=dict(ENV, OLLAMA_HOST="http://127.0.0.1:1"))
    check(r.returncode == 3 and apps() == [] and "transport" in r.stderr, "H: %d %s" % (r.returncode, r.stderr))
    print("H. unreachable model -> exit 3, nothing written")

    # I
    big = "x" * 10000 + "\x1b[31m\x07 gym"
    r = build([call(schedule="0 7 * * *")], big)
    check(r.returncode == 0, "I: %d %s" % (r.returncode, r.stderr))
    sent = requests()[0]["messages"][1]["content"]
    check(len(sent) <= len("Request: ") + 2000 and "\x1b" not in sent and "\x07" not in sent, "I: request not cleaned: len %d" % len(sent))
    m = json.load(open(os.path.join(d, "manifest.json")))
    check(m["schedule"] == "*-*-* 07:00:00" and "agos-schedule install %s" % d in r.stdout, "I: schedule %r / next lines %r" % (m["schedule"], r.stdout[-300:]))
    print("I. request cleaned and capped; cron converted; schedule install step shown")

    # J
    shutil.rmtree(d, ignore_errors=True)
    r = build([call(plan="1. do\x1b[8mhidden\x1b[0m 2. done")], "gym")
    check(r.returncode == 0 and "\x1b" not in r.stdout and "\x1b" not in open(os.path.join(d, "PLAN.md")).read() and "dohidden" in r.stdout, "J: plan not cleaned: %r" % r.stdout[:300])
    shutil.rmtree(d)
    r = build([call(filename="app.py\n")], "gym", "--retries", "0")
    check(r.returncode == 4 and "B1" in r.stderr and apps() == [], "J: trailing-newline filename accepted: %d %s" % (r.returncode, r.stderr[:200]))
    r = build([call(name="gym\n")], "gym")
    check(r.returncode == 0 and apps() == ["gym"] and "\n" not in json.load(open(os.path.join(APPS, "gym/manifest.json")))["name"], "J: newline name leaked: %r" % apps())
    shutil.rmtree(os.path.join(APPS, "gym"))
    r = build([call(files=[{"path": "~/finance/ba\x00nk.csv", "mode": "r"}]), call()], "gym")
    check(r.returncode == 0 and "Traceback" not in r.stderr and "REFUSED: B4" in requests()[1]["messages"][-1]["content"], "J: NUL path: %d %s" % (r.returncode, r.stderr[:300]))
    shutil.rmtree(d)
    r = build([call(schedule="*-*-* 07:00:00\x00"), call()], "gym")
    check(r.returncode == 0 and "Traceback" not in r.stderr and "REFUSED: B5" in requests()[1]["messages"][-1]["content"], "J: NUL schedule: %d %s" % (r.returncode, r.stderr[:300]))
    shutil.rmtree(d)
    r = build([call(schedule="--help")], "gym", "--retries", "0")
    check(r.returncode == 4 and "R10" in r.stderr and apps() == [], "J: schedule --help: %d %s" % (r.returncode, r.stderr[:200]))
    r = build([call()], "gym"); check(r.returncode == 0, "J: setup")
    keep = open(os.path.join(d, "app.py"), "rb").read()
    os.rename(d, d + ".hold"); open(d, "w").write("in the way\n")  # a regular FILE where the dir must go: rename(dir -> file) raises
    r = build([call(source=SRC + "# v2\n")], "gym", "--version", "0.2")
    check(r.returncode == 5 and "nothing left behind" in r.stderr, "J: failed rename: %d %s" % (r.returncode, r.stderr[:300]))
    check(not [x for x in os.listdir(APPS) if x.startswith(".build-")], "J: stage dir left: %r" % os.listdir(APPS))
    check(open(d).read() == "in the way\n" and open(os.path.join(d + ".hold", "app.py"), "rb").read() == keep, "J: previous state disturbed")
    os.unlink(d); os.rename(d + ".hold", d)
    r = build([call(source=SRC + "# v2\n")], "gym", "--version", "0.2")  # bak path: make the final rename fail AFTER the bak move
    check(r.returncode == 0 and os.path.isdir(d + ".v0.1.bak"), "J: setup bak")
    print("J. plan cleaned; newline names/filenames refused; NUL and --help schedules fed back; failed rename leaves nothing")

    print("agos-build-battery: PASS (10 criteria)")
finally:
    shutil.rmtree(TMP, ignore_errors=True)
