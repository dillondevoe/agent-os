# Contributing

Thanks for looking. This is a one-owner project; the owner reviews and merges every pull
request. Small, focused changes get reviewed fastest.

## Ground rules

- **Small PRs.** One concern per pull request. If a change needs a refactor first, send the
  refactor separately.
- **Branch, then PR.** Never push to `main`. Open a pull request from a branch or fork.
- **Match the style.** Look at recent `git log` for the title convention, for example
  `tools(pr-currency): ...` or `docs(open): ...`, and explain the *why* in the body.
- **Do not weaken an invariant to make a test pass.** The sovereignty invariants (see
  [SECURITY.md](SECURITY.md)) are the point of the project. A change that touches the egress
  wall, the break-glass door, the broker or the capability registry should say so up front.

## Sign-off (DCO)

Sign every commit with the [Developer Certificate of Origin](https://developercertificate.org/):

```sh
git commit -s
```

That adds a `Signed-off-by: Your Name <you@example.com>` line, certifying you have the right to
submit the work under the project's MIT license.

## Run the tests

```sh
bash tests/run-local.sh                    # the nix-free batteries, one table, seconds
bash tests/run-local.sh -v                 # ...with output from any failure
nix flake check --option sandbox true -L   # the real merge gate (needs nix)
```

`tests/run-local.sh` is a fast local loop, not merge evidence; CI runs the sandboxed flake
check. A new behaviour should come with a battery arm that fails without it.

## Personal-data gate

This repository is public. A gate (`tools/personal-data-gate.sh`, denylist in
`tools/personal-data-denylist.txt`, run in CI by `.github/workflows/personal-data-gate.yml`)
rejects added lines that look like machine or operator identity: tailnet/CGNAT and private-LAN
addresses, personal mailboxes, account handles, auth keys and tokens. Install the local
pre-push hook with `bash tools/install-hooks.sh`. Never commit real addresses, hostnames,
credentials or personal details; use documentation ranges (`203.0.113.0/24`) and placeholders
(`user@host`). Do not bypass the gate with `--no-verify`; fix the content instead.

## Licensing

By contributing you agree your work is released under the [MIT license](LICENSE). Model weights
the project fetches have their own licenses; see [NOTICE](NOTICE).
