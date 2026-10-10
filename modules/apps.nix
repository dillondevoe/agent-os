# Agent-built apps on the image: the agos tools, packaged with their store and apps paths compiled
# in, and the two directories the app.approve capability binds (docs/design/app-approval-confirm.md
# §3.1 and build-order PR 3).
#
# Wall-adjacent: branch -> PR -> Fable(code) -> merge.
#
# What this module guarantees:
#   - the image copies of agos-run / agos-approve / agos-schedule / agos-build carry
#     IMAGE_APPROVALS / IMAGE_APPS (and agos-schedule IMAGE_RUNNER) as constants, so the agent
#     account cannot point them at a store it can write (A3); a missing constant fails the BUILD
#     (substituteInPlace --replace-fail), never a silent dev-mode fallback;
#   - /var/lib/agent-os/app-approvals is root:root 0755 (the store file the impl writes is 0644):
#     the agent-uid runner can read approvals and cannot write them (A2);
#   - /var/lib/agent-os/apps is owned by the agent account, where agos-build writes apps and the
#     app.approve capability reads them (read-only bind). Both must exist before the capability's
#     unit starts: its sandbox binds them without "-", so a missing one fails the unit.
#   - scheduled timers ExecStart the system profile's agos-run, a path that survives rebuilds.
{ config, pkgs, lib, ... }:

let
  approvalsDir = "/var/lib/agent-os/app-approvals";
  appsDir = "/var/lib/agent-os/apps";
  profileRunner = "/run/current-system/sw/bin/agos-run";

  agosTools = pkgs.runCommand "agent-os-apps-tools" { nativeBuildInputs = [ pkgs.python3 ]; } ''
    lib=$out/libexec/agent-os
    mkdir -p $lib/bin $lib/evals/tasks $out/bin
    cp ${../bin/agos-run} $lib/bin/agos-run
    cp ${../bin/agos-approve} $lib/bin/agos-approve
    cp ${../bin/agos-schedule} $lib/bin/agos-schedule
    cp ${../bin/agos-build} $lib/bin/agos-build
    cp ${../bin/agos-net} $lib/bin/agos-net
    cp ${../bin/agos-fetch} $lib/bin/agos-fetch
    cp ${../bin/cap-net-fetch} $lib/bin/cap-net-fetch
    cp ${../evals/run.py} $lib/evals/run.py
    cp ${../evals/tasks/app-generation.json} $lib/evals/tasks/app-generation.json
    substituteInPlace $lib/bin/agos-run \
      --replace-fail 'IMAGE_APPROVALS = ""' 'IMAGE_APPROVALS = "${approvalsDir}/approvals.json"' \
      --replace-fail 'IMAGE_APPS = ""' 'IMAGE_APPS = "${appsDir}"'
    substituteInPlace $lib/bin/agos-schedule \
      --replace-fail 'IMAGE_RUNNER = ""' 'IMAGE_RUNNER = "${profileRunner}"'
    chmod 0555 $lib/bin/*
    patchShebangs $lib/bin
    for t in agos-run agos-approve agos-schedule agos-build; do
      cat > $out/bin/$t <<EOF
    #!${pkgs.runtimeShell}
    exec ${pkgs.python3}/bin/python3 -I $lib/bin/$t "\$@"
    EOF
      chmod 0555 $out/bin/$t
    done
  '';
in
{
  environment.systemPackages = [ agosTools pkgs.bubblewrap ];

  systemd.tmpfiles.rules = [
    "d ${approvalsDir} 0755 root root - -"
    "d ${appsDir} 0755 agent ${config.users.users.agent.group} - -"
  ];
}
