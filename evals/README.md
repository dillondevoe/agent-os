# evals — the model-agnostic eval harness (skeleton)

Our own small eval for the models Agent OS runs on: can a local model drive the closed desktop
tool set, pick the right handler as a front-stage router, and propose least-privilege app
manifests. It is a measurement instrument. It never gates a merge.

## Run

    evals/run.py --backend fake                              # the canned answers, no model (self-test)
    evals/run.py --backend ollama --model qwen3.5:9b         # a real local model, all families
    evals/run.py --backend ollama --model qwen3:4b --runs 3 --family tool-use --json out.json --md out.md

`OLLAMA_HOST` (default `http://127.0.0.1:11434`) and `OLLAMA_MODEL` are honoured. stdlib only.
Exit code 0 means the run completed; the score is in the report. Exit 2 means no task matched.

The report has: per-family passed/total, median and p95 latency, per-task passed/runs with
`stable` or `flaky`, and every failing call.

## Families (31 tasks)

| family | tasks | what it measures |
|---|---|---|
| `tool-use` | 15 | Closed desktop-control tools (focus, launch, move, arrange, open file, notify, close). Right tool, valid args, refusal on a credential path. Ported from the agos-do prototype eval, compositor-agnostic. |
| `routing` | 10 | Jev-style picker: one of `local-answer` / `tool` / `build-app` / `refuse-or-ask`. Whether a small model can be the first stage. |
| `app-generation` | 6 | One-line request to `propose_app(name, plan, manifest)`. Scored on manifest shape and least privilege: no network unless asked and then only the named domains, read-only unless writing is the point, never credential paths, and the trap request must be refused. Never on running code. |

## Add a task

Tasks live in `evals/tasks/<family>.json`. Each file carries the family's `system` prompt and
`tools` list; each task has:

```json
{"id": "tu-browser", "prompt": "open a browser", "context": "Open windows: ...",
 "expect": {"call": {"tool": "launch_app", "args": {"name": {"any_contains": ["browser", "firefox"]}}}},
 "fake":   {"content": "", "tool_calls": [{"function": {"name": "launch_app", "arguments": {"name": "browser"}}}]}}
```

- `expect` is declarative, no code: `call` / `first_call` / `no_call` / `no_calls` / `any_of` / `all_of`,
  and per-argument ops `eq in contains any_contains none_contains present absent type min_len max_len
  subset_of all_items no_item_contains keys_required fields`. An unknown op raises.
- `fake` is the canned CORRECT answer. The battery (`tests/evals-battery.py`) requires it to pass its own
  matcher, and proves wrong answers score 0. A task without a passing fake is a broken task.

## Model watch

The weekly model watch should, for each new candidate it decides to try:

    ollama pull <model>
    evals/run.py --backend ollama --model <model> --runs 3 --json reports/<date>-<model>.json --md reports/<date>-<model>.md

and compare family scores and median latency against the baseline below. A score is only
comparable at the same `--runs`; latency only on the same machine.

## What is NOT measured

- Whether a generated app runs, is correct, or is safe once running (that is the sandbox's job).
- Multi-turn behaviour, memory, or anything needing a second model round-trip.
- Prompt-injection resistance (window titles and page content are not adversarial here).
- Cloud models: only `ollama` and `fake` backends exist. Adding a backend is one function.

## Baseline

See the bottom of this file; appended per run with date, host and model.
