# modules/system-set.nix — SCAFFOLD ONLY (Phase 2, PR-A).
#
# The root-side executor SEAM for the future `system.set` (T2) capability. Under the
# no-agent-root posture the agent NEVER holds root and never authors Nix; to "change a
# setting" it asks the broker, which stages a BOUNDED option-value delta (option = a registry
# enum: timezone / console-font-size / hostname / wifi-network / brain-model; value type-
# validated by the broker's existing arg-schema machinery). A fixed root-side unit then
# template-renders the Nix from store-side templates, evaluates `--offline` against pinned
# inputs, switches, and renders the option-value delta into the confirm frame.
#
# THIS PR ships ONLY the inert skeleton + the staging dir. The executor logic, the Nix
# templates, the offline eval, the nixos-rebuild switch, and the confirm-on-effect render all
# land in PR-J with their OWN Fable pass. Nothing here is enabled, wired, or path-activated.
#
# Arbitrary-config rebuild (anything outside the registry enum) is formally T3 — human-only at
# the tty3 break-glass (modules/break-glass.nix), never expressible through the broker.
#
# SECURITY SURFACE: routed branch -> PR -> Fable, never direct-push, never self-merge.
{ config, pkgs, lib, ... }:

{
  # Staging dir: where the unprivileged broker impl (PR-J) will drop a typed option-delta
  # request for the root unit to consume. 0700 root:root — the agent (non-root) can neither
  # read nor write it; the future impl writes via the broker's audited path, not directly.
  systemd.tmpfiles.rules = [
    "d /var/lib/agent-os/system-set 0700 root root -"
    "d /var/lib/agent-os/system-set/staging 0700 root root -"
  ];

  # INERT scaffold unit — the SHAPE of the PR-J executor, deliberately not enabled and with a
  # placeholder ExecStart that refuses. No [Install]/wantedBy, no .path activation on the
  # staging dir → it never auto-starts; if started by hand it fails closed. Wiring (path-
  # activation, template render, offline eval, switch, confirm) is PR-J.
  systemd.services."agent-os-system-set" = {
    description = "Agent OS system.set executor (SCAFFOLD — not wired; real impl in PR-J)";
    serviceConfig = {
      Type = "oneshot";
      User = "root";
      # refuses until PR-J ships the real executor — NEVER a fabricated success.
      ExecStart = "${pkgs.coreutils}/bin/false";
    };
  };
}
