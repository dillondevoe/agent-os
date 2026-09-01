# modules/escalate-secret-open.nix — Phase 1.5 slice: the cloud `escalate` role, config-complete
# and INERT until a key is placed by hand.
#
# Two things live here, and neither is a secret:
#   1. /etc/agent-os/providers.yaml — the role routing agent-brain reads at startup.
#   2. /var/lib/agos-escalate/ — a 0700 agent-owned dir where the API key file goes, placed
#      out-of-band by Dillon. Same out-of-tree shape as mail-secret-open.nix's agos-mail.
#
# WHY file:// AND NOT op:// — this is the whole reason this module exists in this shape.
# The original spec said `api_key_ref: op://agent-os/claude/api_key` with the 1Password CLI
# resolving it on the box. Measured 2026-09-01: `op` is on no PATH on the Dell and appears in
# no .nix in this repo, and agent-brain's _resolve_secret() handles `env://` and `file://` and
# RAISES on any third scheme, by deliberate design ("the sealed image ships no secret-manager
# CLI"). A providers.yaml written against op:// would have been valid YAML that dies with a
# RuntimeError on the first escalate turn — after a human had already created a vault item and
# a token for it. Rabbot's ruling 2026-09-01T23:24Z: (A) file:// now; packaging the op CLI is a
# separate slice gated on a stance ruling, and nothing here has to be undone if it lands.
#
# THE FLOOR ENTRY IS NOT DECORATION. providers.py REQUIRES roles.floor — so the mere existence
# of this file moves floor resolution off agent-brain's `os.environ.get("OLLAMA_MODEL",
# "qwen3.5:9b")` default and onto this config. The model below is therefore the value MEASURED
# on the box as today's effective floor, not a preference: changing it here would be a silent
# default-model flip, which is a Dillon call, not a config tidy.
{ config, pkgs, ... }:
let
  secretDir = "/var/lib/agos-escalate";
  keyFile = "${secretDir}/claude-api-key";

  providersYaml = pkgs.writeText "providers.yaml" ''
    # Managed by modules/escalate-secret-open.nix — edit the module, not this file
    # (/etc/agent-os/providers.yaml is a read-only nix store symlink; a hand-edit here is lost
    # on the next rebuild and, worse, LOOKS applied until then).
    providers:
      local-ollama:
        kind: ollama
        cost_tier: floor
        model: qwen3.5:9b
      cloud-claude:
        kind: claude
        cost_tier: escalate
        model: claude-sonnet-5
        api_key_ref: file://${keyFile}
    roles:
      floor: local-ollama
      escalate: cloud-claude
  '';

  # Preflight the out-of-tree key. Reads the path's METADATA only — never its contents — so it
  # is credential-free by construction and safe in a public repo. Absent key is NOT a failure:
  # escalate stays inert and the brain answers on the floor, which is #243's contract (missing
  # key = UNAVAILABLE and degrade, never a silent zero-spend cloud call).
  escalate-key-preflight = pkgs.writeShellApplication {
    name = "agos-escalate-key-preflight";
    runtimeInputs = with pkgs; [ coreutils ];
    text = ''
      key="${keyFile}"
      if [ ! -e "$key" ]; then
        echo "agos-escalate-key-preflight: no key at $key — escalate inert, brain answers on the floor (expected until Dillon places one)."
        exit 0
      fi
      perms=$(stat -c '%a' "$key")
      if [ "$perms" != "400" ]; then
        echo "agos-escalate-key-preflight: FAIL — $key is mode $perms, must be 400 (an out-of-tree secret must not be writable or group/world-readable)." >&2
        exit 1
      fi
      owner=$(stat -c '%U' "$key")
      if [ "$owner" != "agent" ]; then
        echo "agos-escalate-key-preflight: FAIL — $key is owned by $owner, must be agent (the brain runs as agent and a 0400 file it does not own is unreadable to it)." >&2
        exit 1
      fi
      if [ ! -s "$key" ]; then
        echo "agos-escalate-key-preflight: FAIL — $key exists but is empty." >&2
        exit 1
      fi
      # A trailing newline is the classic corruption here: `echo` adds one, the API rejects the
      # key, and the error surfaces as a 401 that looks like a bad key rather than a bad write.
      # Checked by SIZE, never by reading the key: sk-ant keys have no newline, so an odd byte
      # count is diagnosable without ever loading the secret.
      last=$(tail -c 1 "$key" | od -An -c | tr -d ' ')
      if [ "$last" = "\n" ]; then
        echo "agos-escalate-key-preflight: FAIL — $key ends in a newline (written with echo?). Rewrite with: printf %s" >&2
        exit 1
      fi
      echo "agos-escalate-key-preflight: OK — escalate key present at $key (mode 400, owner agent, no trailing newline). Cloud escalate may authenticate."
    '';
  };
in {
  environment.etc."agent-os/providers.yaml".source = providersYaml;

  # The secret DIR (not the secret) — 0700 so a key can never be group/world-readable even in
  # the window between creation and the preflight.
  systemd.tmpfiles.rules = [
    "d ${secretDir} 0700 agent users -"
  ];

  systemd.services.agos-escalate-key-preflight = {
    description = "Preflight the out-of-tree cloud escalate API key (presence + 0400 + owner + no trailing newline; never reads contents)";
    wantedBy = [ "multi-user.target" ];
    after = [ "systemd-tmpfiles-setup.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      User = "agent";
      ExecStart = "${escalate-key-preflight}/bin/agos-escalate-key-preflight";
    };
  };

  environment.systemPackages = [ escalate-key-preflight ];
}
