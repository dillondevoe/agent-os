# Example apps

Two apps shaped like the eval's app-generation tasks, each with a `manifest.json` per
`docs/design/app-manifest.md`. To try one, copy it into the apps root and dry-run it:

    mkdir -p ~/.local/share/agent-os/apps
    cp -r examples/apps/gym-spending ~/.local/share/agent-os/apps/
    bin/agos-run ~/.local/share/agent-os/apps/gym-spending --dry-run --approve-for-test

Without `--approve-for-test` the runner refuses until the manifest's sha256 is in
`~/.local/state/agent-os/approvals.json`, which only the confirm channel writes.
