# modules/break-glass.nix — the ONE interactive door to uid 0 (Phase 2, PR-A).
#
# The agent (tty1) holds NO path to root (configuration.nix no-agent-root posture: agent ∉
# wheel, no sudoers, no password). This module is the deliberate exception: a physically-
# present operator can get a REAL root bash on tty3 for rescue/admin. It is password-gated
# and console-only — there is NO network path here (SSH stays off; mesh is a later,
# broker-gated PR, and task-279 #6 is its hard precondition). "Console-only" is ENFORCED, not
# merely asserted: the break-glass hash authenticates at the physical tty3 login prompt and
# NOWHERE else, because `su` is restricted to wheel (which is empty) below — so the agent's
# own tty1 shell cannot `su` into root with the break-glass password (Fable MED, 2026-07-29).
#
# Threat-model note: the tty is effector #0 — physical presence is already trusted (INV-1).
# This does NOT weaken that; it makes the privileged console path EXPLICIT and auditable (one
# named VT, password-gated) instead of leaving root reachable by accident.
#
# ERGONOMICS PENDING DILLON-CONFIRM (flagged to Rabbot): whether "tty3 as the admin path" is
# the right shape, and how the break-glass password is provisioned. This module ships the
# MECHANISM; the actual password hash is operator-set at install (hashedPasswordFile below),
# NEVER baked into git. Until that file exists, root has no valid password → the door is
# fail-SAFE-closed (no passwordless root), not fail-open. This holds ONLY because
# configuration.nix sets `users.mutableUsers = false` — declarative creds are the sole source
# of truth; with mutable users a missing hash would merely WARN and root would keep its
# installer shadow entry (Fable MED, 2026-07-29). Corollary: the install runbook MUST
# provision the hash or there is NO admin path at all (installer-media recovery only) — this
# is the crux of the pending Dillon-confirm on break-glass provisioning.
#
# BOOT/CONSOLE SURFACE. `nix build .#vm` swaps the whole console/getty story under mkVMOverride,
# so for months the getty@tty3 override below was eval-only, and it was wrong the whole time (see
# the note at the unit). Since 2026-10-12 a booted machine checks it: tests/break-glass.nix switches
# to tty3 the way a human does and types the password. Real hardware (Dell) is still worth one look:
# the VM proves the unit and the login, not a particular keyboard or firmware console.
#
# SECURITY SURFACE: routed branch -> PR -> Fable, never direct-push, never self-merge.
{ config, pkgs, lib, ... }:

{
  # tty3 = break-glass: override the global agent-autologin (agent-shell.nix sets
  # `services.getty.autologinUser = "agent"`, which bakes `--autologin agent` into the shared
  # getty@ template, so every VT would otherwise autologin the agent) with a PLAIN login
  # prompt on tty3 ONLY. Blast radius is tty3 alone — tty1 (the agent console) is untouched,
  # so a mistake here cannot brick the primary boot/login path; worst case tty3 is unusable
  # and the operator falls back to another VT.
  #
  # NOTE: if it turns out autologin was already tty1-only on the pinned nixpkgs, this override
  # is a harmless no-op (tty3 was already a plain login) and only the root password below
  # matters. Verified either way on real HW per the VM-blind note above.
  #
  # `overrideStrategy = "asDropin"` IS LOAD-BEARING (found 2026-10-12, tests/break-glass.nix). The
  # default, "asDropinIfExists", looks for an upstream unit FILE of this exact name; a template
  # INSTANCE has none, so nixpkgs wrote `getty@tty3.service` as a whole unit of its own. logind does
  # not start that name on a VT switch: it starts `autovt@tty3.service`, an alias of the TEMPLATE,
  # and with a separate file in the way the two were different units. So Ctrl+Alt+F3 ran the
  # template's getty, an autologin as the agent, and the root prompt this module exists for never
  # appeared (the NOTE above guessed at the opposite failure). As a drop-in there is one unit under
  # both names, and this ExecStart is what runs. The drop-in is named overrides.conf, like the
  # template's own, and so replaces it for this instance: nothing of the template's autologin
  # command line is inherited, which is the point.
  # `restartIfChanged = false` is the template's own setting, and it went away with the template's
  # drop-in: without it a `nixos-rebuild switch` that changes this unit's text (a new util-linux or
  # shadow path is enough) restarts the console, and the session it kills is the root repair
  # session, the one most likely to be running that very rebuild.
  systemd.services."getty@tty3" = {
    overrideStrategy = "asDropin";
    restartIfChanged = false;
    serviceConfig.ExecStart = [
      ""  # clear the inherited (autologin) ExecStart before setting our own
      "@${pkgs.util-linux}/sbin/agetty agetty --login-program ${pkgs.shadow}/bin/login --noclear %I $TERM"
    ];
  };

  # The break-glass identity is root itself — a real root bash, the rescue shell. Its password
  # is provisioned out-of-band by the installer/operator and is NOT in this repo. File absent
  # => login disabled => fail-safe (no passwordless root). Fail-safe depends on
  # `users.mutableUsers = false` in configuration.nix (see the header note).
  users.users.root.hashedPasswordFile = "/etc/agent-os/break-glass.hash";

  # su must NOT be a second door to the break-glass password. Left default, PAM `su` accepts
  # root's password from ANY process — including the agent's own shell on tty1 (an operator
  # typo lands the break-glass password on agent stdin by construction). `requireWheel` adds
  # pam_wheel to the su stack so ONLY members of `wheel` may `su` to root; under the
  # no-agent-root posture `wheel` is EMPTY (configuration.nix) → `su` authenticates NO ONE →
  # the break-glass hash works at the physical tty3 login prompt and nowhere else. This is what
  # makes the "console-only" claim in the header actually true (Fable MED, 2026-07-29).
  security.pam.services.su.requireWheel = true;
}
