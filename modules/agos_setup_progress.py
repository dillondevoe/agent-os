#!/usr/bin/env python3
# modules/agos_setup_progress.py — what tty1 shows between the first-login questions and the agent's
# login, while the setup window is open (docs/design/first-login-and-setup-window.md §2.4).
# Stdlib only. OUTPUT ONLY: it never reads the terminal, holds no privilege (a throwaway account, no
# network), and decides nothing. It exits when the box is sealed, so tty1 never has two owners.
#
# It reads two world-readable files in /run, and looks for a third:
#   /run/agentos-setup/status    one JSON line from agos_window.py: {"state": "open", "remaining": N}
#                                | {"state": "sealed", "reason": ...} | {"state": "failed" | "down"}
#   /run/agent-os/pull-status    the model download's latest progress line. That text comes from the
#                                fetcher, which is on the network: only printable ASCII reaches the
#                                terminal, and at most one short line of it.
#   /run/agentos-unsealed        seal-check's banner. If it EXISTS the wall is down: say that setup
#                                could not finish and stop (the consoles' login gate prints the banner).
#
#   agos_setup_progress.py <limit seconds>     the unit's own TimeoutStartSec is the backstop
import json, os, sys, time

INSTALLED = "@AGOS_PROGRESS_INSTALLED@" == "yes"      # setup-window.nix substitutes the literal
READ_MAX = 512


def _paths():
    """The installed copy reads its two fixed paths. Only the repo copy reads AGOS_PROGRESS_* (tests)."""
    if INSTALLED:
        return "/run/agentos-setup/status", "/run/agent-os/pull-status", "/run/agentos-unsealed"
    return (os.environ.get("AGOS_PROGRESS_STATUS", "/run/agentos-setup/status"),
            os.environ.get("AGOS_PROGRESS_PULL", "/run/agent-os/pull-status"),
            os.environ.get("AGOS_PROGRESS_UNSEALED", "/run/agentos-unsealed"))


def clean(text, limit=70):
    """Printable ASCII only, runs of spaces collapsed, cut to `limit`. Nothing a terminal interprets
    (escape, carriage return, bell, any control or non-ASCII character) gets through."""
    out = "".join(ch if " " <= ch <= "~" else " " for ch in text)
    return " ".join(out.split())[:limit]


def _read(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return ""
    try:
        return os.read(fd, READ_MAX).decode("utf-8", "replace")
    except OSError:
        return ""
    finally:
        os.close(fd)


def read_status(path):
    """-> (state, remaining, reason). Anything unreadable or malformed is ("", None, "")."""
    try:
        doc = json.loads(_read(path))
    except ValueError:
        return "", None, ""
    if not isinstance(doc, dict) or doc.get("state") not in ("open", "sealed", "failed", "down"):
        return "", None, ""
    left = doc.get("remaining")
    left = left if isinstance(left, int) and not isinstance(left, bool) and 0 <= left <= 4 * 3600 else None
    reason = doc.get("reason")
    return doc["state"], left, clean(reason, 40) if isinstance(reason, str) else ""


REASONS = {
    "budget": "the time allowed for setup ran out",
    "deadline": "the time allowed for setup ran out",
    "timeout": "setup did not finish in time",
    "ended": "setup was stopped",
    "not open": "setup could not start on this boot",
    "invalid state": "the setup record on disk could not be read",
}

REPROVISION = (
    "To set this machine up again: at the repair console (Ctrl+Alt+F3, root), remove\r\n"
    "  /var/lib/agent-os/seal/sealed and /var/lib/agent-os/seal/window.json\r\n"
    "and restart the machine.\r\n"
)


def line_for(left, pull):
    text = "Agent OS is getting its model (first start only)."
    pull = clean(pull)
    if pull:
        text += "  " + pull
    if left is not None:
        text += "  [closes by itself in about %d min]" % max(1, (left + 59) // 60)
    return text


def final_for(state, reason):
    """-> (text, seconds to leave it on the screen)."""
    if state == "sealed" and reason == "complete":
        return "The model is ready. Starting Agent OS.\r\n", 0
    if state == "sealed":
        why = REASONS.get(reason, "setup ended")
        return ("Setup has ended: %s.\r\n"
                "This machine is now closed to the network and stays that way.\r\n"
                "Agent OS starts next. If it says it has no model, the download did not finish.\r\n"
                % why) + REPROVISION, 8
    return ("Setup could not finish. This machine is closed to the network\r\n"
            "and Agent OS has not been started.\r\n"
            "Repair console: Ctrl+Alt+F3 (root).\r\n"), 0


def main(argv, out=None, sleep=time.sleep, now=time.monotonic):
    out = out or sys.stdout
    try:
        limit = float(argv[1])
    except (IndexError, ValueError):
        sys.stderr.write("usage: agos_setup_progress.py <limit seconds>\n")
        return 2
    status_path, pull_path, unsealed_path = _paths()
    end, last = now() + limit, None
    while True:
        state, left, reason = read_status(status_path)
        if os.path.lexists(unsealed_path):           # seal-check: the wall is down, whatever the status says
            state, reason = "down", ""
        if state in ("sealed", "failed", "down"):
            text, hold = final_for(state, reason)
            out.write("\r\033[K" + text)
            out.flush()
            sleep(hold)
            return 0
        line = line_for(left, _read(pull_path))
        if line != last:
            out.write("\r\033[K" + line)
            out.flush()
            last = line
        if now() >= end:
            out.write("\r\033[K" + final_for("failed", "")[0])
            out.flush()
            return 0
        sleep(1)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
