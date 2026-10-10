#!/usr/bin/env python3
# modules/agos_desktop.py — the desktop adapter (docs/design/surfaces-and-first-login.md §6, order
# of work item 3: "extract the adapter from the Hyprland module"). Stdlib only.
#
# The brain never talks to a compositor directly any more; it calls one small interface, and each
# desktop implements it. Today there are two implementations: Hyprland (the existing hyprctl calls,
# moved here unchanged) and NoDesktop (terminal-only boxes). sway and Plasma come next.
#
#   verb            required  meaning
#   windows()       optional  list of {class, title, workspace}, or UNSUPPORTED
#   arrange(action) optional  one of a CLOSED set of window actions, or UNSUPPORTED
#   notify(msg)     yes       show a notification to the human (org.freedesktop.Notifications)
#
# THE RULE (§6): an optional verb a desktop cannot do returns an explicit Unsupported, a third
# state. It never returns an empty list that reads as "saw nothing". Callers render it as
# "unavailable (<reason>)".
#
# THE OTHER RULE (carried from agent-brain.py, Rabbot ruling 2026-08-30): under a hyprland.lua
# config `hyprctl dispatch` EVALUATES its argument as Lua. So arrange() only ever passes a value
# from the closed ARRANGE table, looked up by key; a caller string is never interpolated into a
# dispatch. Every call is a direct argv, never a shell.
import json, os, shutil, subprocess

# The installed copy has the box's agentos.desktop compiled in (genesis-open.nix substitutes it), so
# the adapter is right however the brain is started: a systemd unit never sees environment.variables.
# AGENTOS_DESKTOP still overrides (tests). The literal placeholder means "not substituted" (repo copy).
DESKTOP_DEFAULT = "@AGENTOS_DESKTOP@"


class Unsupported:
    """The third state: this desktop (or this box) cannot answer. Carries the reason a human reads."""
    def __init__(self, reason):
        self.reason = reason

    def __repr__(self):
        return "Unsupported(%r)" % self.reason

    def __bool__(self):
        return False


class HyprlandAdapter:
    name = "hyprland"
    graphical = True
    # The arrange_windows enum, mapped to Hyprland 0.56 Lua dispatch expressions (probed against a
    # live compositor on the Dell, 2026-08-30). CLOSED SET keyed by the tool's own enum; see the
    # module header for why a caller string must never reach these values.
    ARRANGE = {"close": "hl.dsp.window.close()", "fullscreen": "hl.dsp.window.fullscreen()",
               "cycle": "hl.dsp.window.cycle_next()", "split": 'hl.dsp.layout("togglesplit")'}

    def windows(self):
        hyprctl = shutil.which("hyprctl")
        if not hyprctl:
            return Unsupported("hyprctl not on this unit's PATH")
        try:
            r = subprocess.run([hyprctl, "clients", "-j"], capture_output=True, text=True, timeout=4)
            d = json.loads(r.stdout)
            return [{"class": str(w.get("class") or "?"), "title": str(w.get("title") or ""),
                     "workspace": str((w.get("workspace") or {}).get("name", "")
                                      if isinstance(w.get("workspace"), dict) else "")} for w in d]
        except Exception:
            return Unsupported("hyprctl gave no readable answer")

    def arrange(self, action):
        disp = self.ARRANGE.get(action)
        # Unknown key: return the error and DISPATCH NOTHING; `action` never reaches a command line.
        if not disp:
            return "unknown window action '%s'" % action
        hyprctl = shutil.which("hyprctl")
        if not hyprctl:
            return Unsupported("hyprctl not on this unit's PATH")
        subprocess.run([hyprctl, "dispatch", disp], capture_output=True, text=True, timeout=6)
        return "desktop: %s done" % action

    def notify(self, msg):
        return _notify_send(msg)


class SwayAdapter:
    """sway, via swaymsg (surfaces-and-first-login.md §6, the second adapter). swaymsg takes a sway
    COMMAND, which sway parses (`;` and `,` separate commands), so arrange() passes only a value from
    the closed table below, looked up by key; a caller string never reaches swaymsg."""
    name = "sway"
    graphical = True
    ARRANGE = {"close": "kill", "fullscreen": "fullscreen toggle",
               "cycle": "focus next", "split": "layout toggle split"}

    def windows(self):
        swaymsg = shutil.which("swaymsg")
        if not swaymsg:
            return Unsupported("swaymsg not on this unit's PATH")
        try:
            r = subprocess.run([swaymsg, "-t", "get_tree", "-r"], capture_output=True, text=True, timeout=4)
            tree = json.loads(r.stdout)
        except Exception:
            return Unsupported("swaymsg gave no readable answer")
        out = []

        def walk(node, ws):
            if node.get("type") == "workspace":
                ws = str(node.get("name") or "")
            if node.get("type") in ("con", "floating_con") and node.get("pid"):
                out.append({"class": str(node.get("app_id") or (node.get("window_properties") or {}).get("class") or "?"),
                            "title": str(node.get("name") or ""), "workspace": ws})
            for k in ("nodes", "floating_nodes"):
                for child in node.get(k) or []:
                    walk(child, ws)
        try:
            walk(tree, "")
        except Exception:
            return Unsupported("swaymsg gave no readable answer")
        return out

    def arrange(self, action):
        cmd = self.ARRANGE.get(action)
        if not cmd:
            return "unknown window action '%s'" % action
        swaymsg = shutil.which("swaymsg")
        if not swaymsg:
            return Unsupported("swaymsg not on this unit's PATH")
        subprocess.run([swaymsg, cmd], capture_output=True, text=True, timeout=6)
        return "desktop: %s done" % action

    def notify(self, msg):
        return _notify_send(msg)


class NoDesktop:
    """A terminal-only box: no window list, no arranging; notify still has a portable path when a
    notification daemon exists, else says so."""
    name = "none"
    graphical = False      # no display: browser and window verbs are Unsupported
    ARRANGE = {}

    def windows(self):
        return Unsupported("no desktop on this box")

    def arrange(self, action):
        return Unsupported("no desktop on this box")

    def notify(self, msg):
        return _notify_send(msg)


def _notify_send(msg):
    tool = shutil.which("notify-send")
    if not tool:
        return Unsupported("no notification tool (notify-send) on PATH")
    try:
        subprocess.run([tool, "--app-name=Agent OS", "--", str(msg)[:500]],
                       capture_output=True, text=True, timeout=4)
        return "notified"
    except Exception:
        return Unsupported("notify-send failed")


def adapter():
    """The adapter for this box, from agentos.desktop (surface-options.nix sets AGENTOS_DESKTOP only
    for a non-default desktop): "none" -> NoDesktop; unset or "hyprland" -> HyprlandAdapter, whose
    own verbs say so when hyprctl is absent. Chosen per call, so tests can set the environment."""
    d = os.environ.get("AGENTOS_DESKTOP") or ("" if DESKTOP_DEFAULT.startswith("@") else DESKTOP_DEFAULT)
    if d == "none":
        return NoDesktop()
    if d == "sway":
        return SwayAdapter()
    return HyprlandAdapter()
