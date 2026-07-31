# modules/mail-secret-open.nix — Phase 2: the (A) MCP-email token SCAFFOLD (OPEN variant).
#
# This is the AGENT-SIDE half of the "email" ambient app (the human half — Thunderbird — is
# email-open.nix, dozen app #8). It is NOT a new dozen app; it is the plumbing that lets the
# agent reach mail via an MCP connector, per Rabbot's RULING (A):
#   rabbot-to-augur-email-hand-RULING-A-MCP-connector-2026-07-31.md
#
# DELIBERATELY CREDENTIAL-FREE — this ships the SHAPE, never a secret:
#   * WHERE the runtime OAuth/IMAP token WOULD live: ${tokenFile} — a 0600 file inside a 0700
#     agent-only dir, placed at auth time by DILLON (the same shape as tailscale's authKeyFile,
#     which install.sh drops out-of-band). NOTHING secret is in this repo or this image.
#   * HOW the future MCP mail connector finds it: the well-known env contract
#     AGOS_MAIL_TOKEN_FILE (exported for login shells + set in the preflight service env), plus
#     the fixed literal path below. Wiring that connector into agent-brain's tool grammar is
#     Rabbot's lane (an `email.*`/mcp-mail hand on the next brain rebuild) — NOT here.
#   * A BOOT PREFLIGHT that de-risks the drop-in: it validates presence + perms of the token
#     WITHOUT EVER READING ITS CONTENTS, and degrades non-fatally when the token is absent
#     (connector simply stays inert until Dillon authorizes — tailscale-autoconnect-shaped).
#
# NB (Rabbot's "no agos-mail"): there is deliberately NO email HAND/CLI here — email is not a
# local, credential-free service, so its agent surface is the MCP connector, not an agos-mail
# binary. The one shell tool below is a SECRET-PERMS PREFLIGHT, not an email hand: it never
# touches mail, only the token file's metadata.
#
# ISOLATION: OPEN-only, self-contained, imported solely from configuration-open.nix. Shares
# nothing with the sealed path — fold into a shared substrate module at seal-time.
{ config, pkgs, ... }:
let
  secretDir = "/var/lib/agos-mail";
  tokenFile = "${secretDir}/oauth-token";

  # Preflight the out-of-tree token. writeShellApplication runs shellcheck at build (real
  # acceptance). It reads the token's PATH + METADATA only — never `cat`s the file — so it is
  # credential-free by construction and safe to have in a public repo.
  mail-token-preflight = pkgs.writeShellApplication {
    name = "agos-mail-token-preflight";
    runtimeInputs = with pkgs; [ coreutils ];
    text = ''
      tok="''${AGOS_MAIL_TOKEN_FILE:-}"
      if [ -z "$tok" ]; then
        echo "agos-mail-token-preflight: AGOS_MAIL_TOKEN_FILE unset — nothing to check." >&2
        exit 0
      fi
      if [ ! -e "$tok" ]; then
        # No token yet: the MCP mail connector stays INERT until Dillon authorizes. Fine.
        echo "agos-mail-token-preflight: no token at $tok — MCP mail connector inert until Dillon authorizes (expected)."
        exit 0
      fi
      # Token present → ENFORCE the out-of-tree secret contract. Never read its contents.
      perms=$(stat -c '%a' "$tok")
      if [ "$perms" != "600" ]; then
        echo "agos-mail-token-preflight: FAIL — $tok is mode $perms, must be 600 (an out-of-tree secret must not be group/world-readable)." >&2
        exit 1
      fi
      if [ ! -s "$tok" ]; then
        echo "agos-mail-token-preflight: FAIL — $tok exists but is empty." >&2
        exit 1
      fi
      echo "agos-mail-token-preflight: OK — mail token present at $tok (mode 600, non-empty). MCP mail connector may authenticate."
    '';
  };
in {
  # The secret DIR (not the secret) — 0700 so the token can never be group/world-readable even
  # before the preflight runs. Dillon drops the 0600 token file here at auth time (out-of-band).
  systemd.tmpfiles.rules = [
    "d ${secretDir} 0700 agent users -"
  ];

  # Discovery contract: any process (the future MCP connector included) learns the path here.
  environment.sessionVariables.AGOS_MAIL_TOKEN_FILE = tokenFile;

  # Boot preflight — validates the drop-in without touching mail or the secret's contents.
  systemd.services.agos-mail-token-preflight = {
    description = "Preflight the out-of-tree mail OAuth/IMAP token (presence + 0600 perms only; never reads contents)";
    wantedBy = [ "multi-user.target" ];
    after = [ "systemd-tmpfiles-setup.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      User = "agent";                       # only stats a path in the agent-owned dir
      Environment = "AGOS_MAIL_TOKEN_FILE=${tokenFile}";
      ExecStart = "${mail-token-preflight}/bin/agos-mail-token-preflight";
    };
  };

  # The preflight tool is on PATH too, so Dillon/Rabbot can run it by hand after dropping a token.
  environment.systemPackages = [ mail-token-preflight ];
}
