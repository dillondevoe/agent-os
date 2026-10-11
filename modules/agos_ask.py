#!/usr/bin/env python3
# modules/agos_ask.py — quick-ask (docs/design/surfaces-and-first-login.md §7). Stdlib only.
#
# One read-only question to the local model: a prompt, ONE HTTP call to the model on loopback, the
# answer. From a hotkey the question comes from a launcher prompt and the answer goes to a
# notification and the clipboard; from a terminal (`agos-ask "question"`) it is printed.
#
# WHAT IT CANNOT DO, by construction:
#   - no tools: the request body has no `tools` key, so the model has nothing to call;
#   - no broker, no capabilities, no memory, no conversation: every call starts from nothing;
#   - no files: it opens none for reading or writing (the trace below goes to the journal);
#   - no network but the model: the URL is built from a compiled-in host that must be loopback,
#     and a response that redirects is refused (urllib would otherwise follow it off-box).
#
# THE TRACE (owner ruling 2026-10-10, option 1): one journal line per use with byte counts and the
# outcome, never the text. The journal stamps the time. It is a usage record, not a control: this
# runs as the agent account, which can add lines but cannot remove the journal's.
#
# The model, host and launcher are compiled in by quick-ask-open.nix (a hotkey's process does not
# reliably see environment.variables — the OLLAMA_THINK scar). The literal placeholders mean "repo
# copy"; AGOS_ASK_* override them for tests only.
import ipaddress, json, os, shutil, subprocess, sys, urllib.error, urllib.request

MODEL_DEFAULT = "@AGOS_ASK_MODEL@"
HOST_DEFAULT = "@AGOS_ASK_HOST@"          # host:port of the local model
LAUNCHER_DEFAULT = "@AGOS_ASK_LAUNCHER@"  # a dmenu-style prompt; one argv, split on spaces

MAX_QUESTION = 4000        # characters
MAX_CONTEXT = 6000         # bytes of selected text (launcher mode only)
MAX_RESPONSE = 1 << 20     # bytes read from the model
NOTIFY_CHARS = 500         # what a notification shows; the clipboard gets the whole answer
TIMEOUT = 120              # seconds for the one call
SYSTEM = "Answer concisely. Plain text, no markdown headings."


def _cfg(name, default):
    v = os.environ.get("AGOS_ASK_" + name)
    if v:
        return v
    return "" if default.startswith("@") else default


def endpoint():
    """The model URL, or None if the configured host is not a loopback literal. A name is refused
    outright (no lookup): 'localhost' is whatever the resolver says it is."""
    host = _cfg("HOST", HOST_DEFAULT)
    name, sep, port = host.rpartition(":")
    if not sep or not port.isdigit():
        return None
    try:
        if not ipaddress.ip_address(name.strip("[]")).is_loopback:
            return None
    except ValueError:
        return None
    return "http://%s:%s/api/chat" % (name, port)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None     # a redirect becomes an HTTPError instead of a second, unvetted request


def ask(question, context=""):
    """One call. Returns (ok, text). Never raises."""
    url = endpoint()
    model = _cfg("MODEL", MODEL_DEFAULT)
    if not url or not model:
        return False, "quick-ask is not configured on this box"
    prompt = question if not context else "Context (selected text):\n%s\n\nQuestion: %s" % (context, question)
    body = json.dumps({"model": model, "stream": False, "think": False,
                       "messages": [{"role": "system", "content": SYSTEM},
                                    {"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(req, timeout=TIMEOUT) as r:
            raw = r.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            return False, "the model's answer was too large"
        text = json.loads(raw)["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            return False, "the model gave no answer"
        return True, text.strip()
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        return False, "the local model did not answer"


def trace(q_bytes, ctx_bytes, a_bytes, ok, mode):
    """One journal line: sizes and outcome, never the text."""
    line = "agos-ask mode=%s in=%d ctx=%d out=%d ok=%d" % (mode, q_bytes, ctx_bytes, a_bytes, 1 if ok else 0)
    try:
        import syslog
        syslog.openlog("agos-ask", 0, syslog.LOG_USER)
        syslog.syslog(syslog.LOG_INFO, line)
    except Exception:
        pass
    return line


def _run(argv, stdin=None, timeout=10):
    try:
        return subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def _selection():
    tool = shutil.which("wl-paste")
    if not tool:
        return ""
    r = _run([tool, "--primary", "--no-newline"], timeout=1)
    if not r or r.returncode != 0:
        return ""
    return r.stdout[:MAX_CONTEXT].decode("utf-8", "ignore")


def _notify(title, text):
    tool = shutil.which("notify-send")
    if tool:
        _run([tool, "--app-name=Agent OS", "-h", "string:x-canonical-private-synchronous:agos-ask",
              "--", title, text[:NOTIFY_CHARS]])


def _prompt():
    """The launcher prompt. Returns the question, or None if cancelled or no launcher."""
    argv = _cfg("LAUNCHER", LAUNCHER_DEFAULT).split()
    if not argv or not shutil.which(argv[0]):
        return None
    r = _run(argv, stdin=b"", timeout=300)
    if not r or r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "ignore").strip() or None


def main(argv):
    if len(argv) > 2 or (len(argv) == 2 and argv[1] in ("-h", "--help")):
        sys.stderr.write('usage: agos-ask "question"   (prints the answer)\n'
                         "       agos-ask              (launcher prompt; answer as a notification + clipboard)\n")
        return 2
    gui = len(argv) == 1
    question = _prompt() if gui else argv[1].strip()
    if not question:
        return 0 if gui else 2          # a cancelled prompt is not an error and leaves no trace
    question = question[:MAX_QUESTION]
    context = _selection() if gui else ""
    if gui:
        _notify("ask", "thinking…")
    ok, text = ask(question, context)
    trace(len(question.encode()), len(context.encode()), len(text.encode()) if ok else 0, ok,
          "gui" if gui else "cli")
    if gui:
        if ok and shutil.which("wl-copy"):
            _run(["wl-copy"], stdin=text.encode())
        _notify("ask: " + question[:60], text)
    else:
        (sys.stdout if ok else sys.stderr).write(text + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
