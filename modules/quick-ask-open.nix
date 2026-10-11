# modules/quick-ask-open.nix — quick-ask (docs/design/surfaces-and-first-login.md §7; owner rulings
# 2026-10-05 "may call the local model directly, read-only, no tools" and 2026-10-10 "1": one
# journal line per use with sizes only). OPEN variant.
#
# `agos-ask "question"` prints the answer; bare `agos-ask` (the Super+I hotkey on the Hyprland and sway
# desktops) asks through a launcher prompt and answers as a notification and on the clipboard.
#
# The model, the model's address and the launcher are COMPILED INTO the installed copy: a hotkey's
# process does not reliably inherit environment.variables (the OLLAMA_THINK scar), and compiling the
# address in is what lets the program refuse anything but a loopback literal. The model is the box's
# configured local model, never a tag typed here.
{ config, pkgs, lib, ... }:

let
  model = config.environment.variables.OLLAMA_MODEL or (throw
    "quick-ask-open: environment.variables.OLLAMA_MODEL is unset, so agos-ask would have no model");
  h = config.services.ollama.host;
  host = "${if lib.hasInfix ":" h then "[${h}]" else h}:${toString config.services.ollama.port}";
  # A dmenu-style prompt with no list: type the question, Enter. A store path, so it is there on every
  # desktop, not only the ones that install wofi for their own launcher.
  launcher = "${pkgs.wofi}/bin/wofi --dmenu --prompt ask --lines 1";

  agos-ask = pkgs.runCommand "agos-ask" { nativeBuildInputs = [ pkgs.makeWrapper ]; } ''
    install -Dm755 ${./agos_ask.py} $out/libexec/agos_ask.py
    substituteInPlace $out/libexec/agos_ask.py \
      --replace-fail '"@AGOS_ASK_MODEL@"' '"${model}"' \
      --replace-fail '"@AGOS_ASK_HOST@"' '"${host}"' \
      --replace-fail '"@AGOS_ASK_LAUNCHER@"' '"${launcher}"'
    # -I: no user site, no PYTHON* env, no cwd on sys.path. PATH gains only the two desktop tools the
    # launcher mode calls; the terminal mode calls neither.
    makeWrapper ${pkgs.python3}/bin/python3 $out/bin/agos-ask \
      --add-flags "-I $out/libexec/agos_ask.py" \
      --prefix PATH : ${lib.makeBinPath [ pkgs.wl-clipboard pkgs.libnotify ]}
  '';
in
{
  assertions = [ {
    assertion = config.services.ollama.host == "127.0.0.1" || config.services.ollama.host == "::1";
    message = "quick-ask-open: the local model must be on loopback; agos-ask refuses any other address at run time too.";
  } ];
  system.build.agosAsk = agos-ask;
  environment.systemPackages = [ agos-ask ];
}
