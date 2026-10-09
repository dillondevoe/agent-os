# Example apps

Two apps shaped like the eval's app-generation tasks, each with a `manifest.json` per
`docs/design/app-manifest.md`. To try one, copy it into the apps root and dry-run it:

    mkdir -p ~/.local/share/agent-os/apps ~/Documents
    [ -e ~/Documents/bank.csv ] || cp examples/apps/gym-spending/sample-bank.csv ~/Documents/bank.csv
    cp -r examples/apps/gym-spending ~/.local/share/agent-os/apps/
    bin/agos-run ~/.local/share/agent-os/apps/gym-spending --dry-run --approve-for-test

The sample CSV step is there because a manifest's `r` paths must exist (rule R13); the runner
refuses rather than binding a file that is not there. `daily-summary` needs no setup: its only
path is `rw` and the runner creates it.

Without `--approve-for-test` the runner refuses until the manifest's sha256 is in
`~/.local/state/agent-os/approvals.json`. To approve for real, from your own login (not the
agent session):

    bin/agos-approve show    ~/.local/share/agent-os/apps/gym-spending   # read what it may touch
    bin/agos-approve approve ~/.local/share/agent-os/apps/gym-spending   # type the 8-hex sha prefix shown
    bin/agos-run             ~/.local/share/agent-os/apps/gym-spending   # runs sandboxed
    bin/agos-approve revoke  gym-spending                                 # and it refuses again
