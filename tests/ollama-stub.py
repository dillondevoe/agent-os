#!/usr/bin/env python3
"""ollama-stub — a deterministic fake Ollama for the agent-loop battery.

Serves the two endpoints agent-loop touches — GET /api/tags and POST /api/chat — with
SCRIPTED responses, so the loop's mechanics (tool-call round-trip, deny cap, tools-withheld
final turn, control-byte scrub) can be proven with NO real model and NO nondeterminism.
The nix build sandbox provides a 127.0.0.1 loopback, so this runs there too.

Wire (all via env, so the battery stays a plain arg-passer):
  AGENT_OS_STUB_SCRIPT   : path to a JSON array of Ollama `message` objects. The Nth POST
                           /api/chat returns the Nth element wrapped exactly as Ollama does
                           for stream=false — {"message": <elt>, "done": true}. Past the end
                           → a filler message (a script that runs short is a test bug, not a
                           hang).
  AGENT_OS_STUB_LOG      : (optional) path; each POST /api/chat REQUEST is appended as one
                           JSON line {"n", "has_tools", "messages"} so the battery can prove
                           what the loop SENT — e.g. the echo result fed back as a tool
                           message, or that the final turn offered no tools.
  AGENT_OS_STUB_PORTFILE : a path (the battery uses a FIFO); the bound ephemeral port is
                           written here once, so the battery learns the port with no polling
                           and no sleep (a clean rendezvous).

/api/tags always advertises one model, so `agent-loop --check` passes while the stub is up.
"""
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

SCRIPT = []
_sp = os.environ.get("AGENT_OS_STUB_SCRIPT")
if _sp:
    with open(_sp) as f:
        SCRIPT = json.load(f)
LOG = os.environ.get("AGENT_OS_STUB_LOG")

_state = {"n": 0}   # POST /api/chat counter, advanced per request (single-flight loop → no races)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tags":
            self._send(200, {"models": [{"name": "stub-qwen"}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        if self.path != "/api/chat":
            self._send(404, {"error": "not found"})
            return
        try:
            req = json.loads(raw or b"{}")
        except Exception:
            req = {}
        n = _state["n"]
        _state["n"] = n + 1
        if LOG:
            with open(LOG, "a") as f:
                f.write(json.dumps({
                    "n": n,
                    "has_tools": "tools" in req,
                    "messages": req.get("messages", []),
                }) + "\n")
        if n < len(SCRIPT):
            msg = SCRIPT[n]
        else:
            msg = {"role": "assistant", "content": "[stub: script exhausted]"}
        self._send(200, {"model": req.get("model", "stub"), "message": msg, "done": True})

    def log_message(self, *a):   # silence the default stderr access log
        pass


def main():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    pf = os.environ.get("AGENT_OS_STUB_PORTFILE")
    if pf:
        # Opening a FIFO for write blocks until the battery opens the read end — a race-free
        # rendezvous that hands over the ephemeral port without any timed polling.
        with open(pf, "w") as f:
            f.write(str(port) + "\n")
    else:
        sys.stdout.write(str(port) + "\n")
        sys.stdout.flush()
    srv.serve_forever()


if __name__ == "__main__":
    main()
