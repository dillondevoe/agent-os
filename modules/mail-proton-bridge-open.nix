# modules/mail-proton-bridge-open.nix — Phase 2: the Proton-Mail-via-Bridge SCAFFOLD (OPEN variant).
#
# The "both" of Dillon's email call (Dillon 9035, relayed
# rabbot-to-augur-email-gmail-test-plus-proton-bridge-both-2026-07-31.md):
#   * GMAIL is the TEST path — already covered by mail-secret-open.nix as-is (nothing more there).
#   * PROTON is the parallel, sovereign-flavored path built HERE. It is NOT a new dozen app; like
#     mail-secret-open.nix it is out-of-tree agent-side plumbing — the shape, never a secret.
#
# WHY BRIDGE (not OAuth/IMAP like Gmail): Proton Mail is end-to-end encrypted, so there is no
# direct OAuth or IMAP. Proton Mail Bridge is a LOCAL app that logs into Proton, decrypts mail
# ON-BOX, and re-exposes it over plain localhost IMAP/SMTP (127.0.0.1:1143 / :1025) guarded by a
# Bridge-generated password. Mail stays E2E to Proton and is only ever decrypted locally — the
# sovereign-flavored path. So the Proton "token" is really: Bridge running + Bridge's localhost
# credential. Dillon runs Bridge and logs in AT AUTH TIME (out-of-band, exactly like the Gmail
# OAuth drop and tailscale's authKeyFile); nothing secret lives in this repo or image.
#
# DELIBERATELY CREDENTIAL-FREE — this ships the SHAPE:
#   * `protonmail-bridge` — the local E2E-decryption app Dillon logs into. Present but INERT until
#     he configures it (no account, no keychain in-repo). This is the mechanism, not a dozen GUI.
#   * WHERE Bridge's localhost credential WOULD live: ${credFile} — a 0600 file inside a 0700
#     agent-only dir, placed at auth time by DILLON (Bridge prints the IMAP password on login).
#   * The localhost endpoints the future MCP/mail connector dials, published as a discovery
#     contract: AGOS_MAIL_PROTON_IMAP (127.0.0.1:1143) + AGOS_MAIL_PROTON_SMTP (127.0.0.1:1025),
#     plus AGOS_MAIL_PROTON_CRED_FILE. Wiring the connector into agent-brain's grammar is Rabbot's
#     lane (it rides the same MCP/integration layer as the Gmail hand) — NOT here.
#   * A BOOT PREFLIGHT that de-risks the drop-in: it validates presence + 0600 perms of the cred
#     WITHOUT EVER READING ITS CONTENTS, and degrades non-fatally when absent (the Proton connector
#     stays inert until Dillon configures Bridge — tailscale-autoconnect-shaped).
#
# NB (mirrors mail-secret's "no agos-mail"): there is deliberately NO email HAND/CLI here — the
# one shell tool below is a SECRET-PERMS PREFLIGHT, not an email hand: it never touches mail, only
# the cred file's metadata.
#
# ISOLATION: OPEN-only, self-contained, imported solely from configuration-open.nix. Shares
# nothing with the sealed path — fold into a shared substrate module at seal-time.
{ config, pkgs, ... }:
let
  secretDir = "/var/lib/agos-mail-proton";
  credFile  = "${secretDir}/bridge-cred";
  imapAddr  = "127.0.0.1:1143";   # Proton Bridge default localhost IMAP
  smtpAddr  = "127.0.0.1:1025";   # Proton Bridge default localhost SMTP

  # Preflight the out-of-tree Bridge credential. writeShellApplication runs shellcheck at build
  # (real acceptance). It reads the cred's PATH + METADATA only — never `cat`s the file — so it is
  # credential-free by construction and safe to have in a public repo.
  mail-proton-preflight = pkgs.writeShellApplication {
    name = "agos-mail-proton-preflight";
    runtimeInputs = with pkgs; [ coreutils ];
    text = ''
      cred="''${AGOS_MAIL_PROTON_CRED_FILE:-}"
      if [ -z "$cred" ]; then
        echo "agos-mail-proton-preflight: AGOS_MAIL_PROTON_CRED_FILE unset — nothing to check." >&2
        exit 0
      fi
      if [ ! -e "$cred" ]; then
        # No Bridge cred yet: the Proton connector stays INERT until Dillon runs Bridge + logs in. Fine.
        echo "agos-mail-proton-preflight: no cred at $cred — Proton connector inert until Dillon configures Bridge (expected)."
        exit 0
      fi
      # Cred present → ENFORCE the out-of-tree secret contract. Never read its contents.
      perms=$(stat -c '%a' "$cred")
      if [ "$perms" != "600" ]; then
        echo "agos-mail-proton-preflight: FAIL — $cred is mode $perms, must be 600 (an out-of-tree secret must not be group/world-readable)." >&2
        exit 1
      fi
      if [ ! -s "$cred" ]; then
        echo "agos-mail-proton-preflight: FAIL — $cred exists but is empty." >&2
        exit 1
      fi
      echo "agos-mail-proton-preflight: OK — Bridge cred present at $cred (mode 600, non-empty). Proton connector may dial localhost IMAP/SMTP."
    '';
  };
in {
  # The secret DIR (not the secret) — 0700 so the cred can never be group/world-readable even
  # before the preflight runs. Dillon drops the 0600 cred file here at auth time (out-of-band).
  systemd.tmpfiles.rules = [
    "d ${secretDir} 0700 agent users -"
  ];

  # Discovery contract: the future MCP/mail connector learns the cred path AND the localhost
  # endpoints Bridge exposes here — no hardcoding, no secret.
  environment.sessionVariables = {
    AGOS_MAIL_PROTON_CRED_FILE = credFile;
    AGOS_MAIL_PROTON_IMAP = imapAddr;
    AGOS_MAIL_PROTON_SMTP = smtpAddr;
  };

  # Boot preflight — validates the drop-in without touching mail or the secret's contents.
  systemd.services.agos-mail-proton-preflight = {
    description = "Preflight the out-of-tree Proton Bridge localhost credential (presence + 0600 perms only; never reads contents)";
    wantedBy = [ "multi-user.target" ];
    after = [ "systemd-tmpfiles-setup.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      User = "agent";                       # only stats a path in the agent-owned dir
      Environment = "AGOS_MAIL_PROTON_CRED_FILE=${credFile}";
      ExecStart = "${mail-proton-preflight}/bin/agos-mail-proton-preflight";
    };
  };

  environment.systemPackages = [
    pkgs.protonmail-bridge   # local E2E-decryption app; Dillon logs in at auth time (inert until then)
    mail-proton-preflight    # the perms preflight, runnable by hand after dropping a Bridge cred
  ];
}
