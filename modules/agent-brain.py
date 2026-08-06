#!/usr/bin/env python3
# Agent OS local brain — WITH HANDS + CONTEXT ANTENNA.
# Talk to it; it acts (browse, run commands, arrange windows) AND it knows the real NOW.
import json, re, subprocess, sys, urllib.request, urllib.error, datetime, os, hashlib, time, threading, shutil, contextlib

# ── UX v2 slice 1: INPUT LOCK (rabbot-to-page-P2-ux-v2-spec 2026-08-02, Dillon msg 9315) ──
# prompt_toolkit PromptSession + patch_stdout = a bottom input line that background output
# (warmup greeting, any threaded print) can never scroll away or swallow — text prints
# ABOVE the prompt instead of through it. Minimal-diff route per the spec: the existing
# print-based streamer is untouched; only the REPL's input() is swapped. TTY-only guard:
# pipes / --once / a missing prompt_toolkit all fall back to the plain input() loop, so
# non-tty callers never see TUI escape codes and the genesis env stays optional in dev.
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.formatted_text import ANSI
    _PTK = sys.stdin.isatty() and sys.stdout.isatty()
except ImportError:
    _PTK = False

# MODEL from env (P0 fix #2, spec-agentos-ux-polish 2026-08-05: the tuned qwen3.5:9b-agentos
# was seeded but unused because of this hardcode — same class as the mini's 08-02 launcher scar).
# The systemd/kitty launchers set OLLAMA_MODEL; bare dev runs keep the base default.
OLLAMA="http://127.0.0.1:11434/api/chat"; MODEL=os.environ.get("OLLAMA_MODEL","qwen3.5:9b")
def _think_budget():
    # OLLAMA_THINK: think-budget control for thinking models on the ~3 tok/s CPU box
    # (spec 2026-08-05 item b). off/false/0 → no thinking (fastest replies);
    # low/medium/high → per-level budget where the model supports it; on/true/1 → full.
    # Unset → omit the key, keep the model's default.
    v=os.environ.get("OLLAMA_THINK","").strip().lower()
    if v in ("off","false","0","no"): return False
    if v in ("on","true","1","yes"): return True
    if v in ("low","medium","high"): return v
    return None
THINK=_think_budget()
>>>>>>> 57b7201 (feat: OLLAMA_THINK env think-budget control for chat_stream)
MODEL_3B="qwen2.5:3b-augur"  # front-door (model-3b-open.nix); absent → front-door bypasses to the 9B main brain

# ── THE SOUL (genesis lock, Geist ruling "bind not bytes") ─────────────────────
# These two are BUILD-TIME LITERALS. genesis-open.nix substitutes @GENESIS_PATH@ with
# the content-hashed store path (${genesis}/GENESIS.md) and @GENESIS_SHA256@ with that
# file's sha256. Baked into the program = no env var, no runtime symlink, nothing a
# running system can repoint. Changing the soul then requires rebuilding the brain — the
# doc's own "deliberate rebuild" carve. Until substituted (hand-deployed dev), we're unlocked.
GENESIS_PATH="@GENESIS_PATH@"
GENESIS_SHA256="@GENESIS_SHA256@"
LOCKED=not GENESIS_PATH.startswith("@")   # substituted by nix == the locked build

def _refuse(reason):
    # the doc's last paragraph, compiled into code: a bad soul = an attack; load nothing else.
    sys.stderr.write("\n\033[1;31m⛔ "+reason+" — I am not starting.\033[0m\n")
    sys.exit(1)

def load_soul():
    path = GENESIS_PATH if LOCKED else "/etc/agent-os/GENESIS.md"  # dev reads on-disk if present
    try:
        text=open(path,encoding="utf-8").read()
    except OSError:
        if LOCKED: _refuse("my soul is missing where it was baked to be")
        return None  # dev, no soul on disk → run unlocked (a dev convenience, never the security claim)
    if LOCKED and hashlib.sha256(text.encode("utf-8")).hexdigest()!=GENESIS_SHA256:
        _refuse("my soul does not hash to what was baked in — treating this as tampering")
    return text

SOUL=load_soul()   # read ONCE at startup, before anything else

TOOLS=[
 {"type":"function","function":{"name":"open_url","description":"Open a website in the browser (tiles into the desktop). Use when the user wants to SEE or interact with a site — browse, shop, watch, use a web app.","parameters":{"type":"object","properties":{"url":{"type":"string","description":"full https URL"}},"required":["url"]}}},
 {"type":"function","function":{"name":"run_command","description":"Run a shell command on THIS computer and return its output. Use to check, list, inspect, create, or change things on the machine.","parameters":{"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}}},
 {"type":"function","function":{"name":"arrange_windows","description":"Rearrange the desktop workspace. action is one of: 'tidy' (re-tile everything evenly), 'close' (close the focused window), 'fullscreen' (toggle fullscreen on focused window), 'cycle' (focus the next window), 'split' (toggle vertical/horizontal split for the next window).","parameters":{"type":"object","properties":{"action":{"type":"string"}},"required":["action"]}}},
 {"type":"function","function":{"name":"calendar.agenda","description":"List the user's upcoming REAL calendar events. Use whenever they ask what's on their calendar / schedule / coming up / today / this week.","parameters":{"type":"object","properties":{"days":{"type":"integer","description":"days ahead to show (default 7)"}}}}},
 {"type":"function","function":{"name":"calendar.add","description":"Add a REAL event to the user's calendar. Use when they want to schedule/add/create/remember an appointment or event.","parameters":{"type":"object","properties":{"start":{"type":"string","description":"start time as 'YYYY-MM-DD HH:MM' (24h, local)"},"summary":{"type":"string","description":"the event title"},"end":{"type":"string","description":"optional end time 'YYYY-MM-DD HH:MM'; defaults to +1h"}},"required":["start","summary"]}}},
 {"type":"function","function":{"name":"calendar.now","description":"Get the exact current date/time from the calendar (station timezone).","parameters":{"type":"object","properties":{}}}},
 {"type":"function","function":{"name":"calendar.cals","description":"List the user's calendar collections.","parameters":{"type":"object","properties":{}}}},
 {"type":"function","function":{"name":"calculator","description":"Evaluate a math expression (arithmetic, %, units, functions). Use for any calculation.","parameters":{"type":"object","properties":{"expression":{"type":"string","description":"e.g. (2+3)*4, sqrt(2), 200*15%, 5 km + 300 m"}},"required":["expression"]}}},
 {"type":"function","function":{"name":"system","description":"Read or change machine settings. action 'status' reports network/audio/display/power; 'volume' sets 0-100 or mute/unmute/toggle; 'brightness' sets 0-100.","parameters":{"type":"object","properties":{"action":{"type":"string","description":"status | volume | brightness"},"value":{"type":"string","description":"for volume/brightness: 0-100 (or mute/unmute/toggle for volume)"}},"required":["action"]}}},
 {"type":"function","function":{"name":"list_files","description":"List the entries (files/folders) in a directory. Use when the user asks what's in a folder.","parameters":{"type":"object","properties":{"dir":{"type":"string","description":"absolute directory path"}},"required":["dir"]}}},
 {"type":"function","function":{"name":"read_document","description":"Extract text from a PDF document — whole doc or one page. Use to read/summarize a PDF the user names.","parameters":{"type":"object","properties":{"path":{"type":"string","description":"path to the .pdf"},"page":{"type":"integer","description":"optional 1-indexed page; omit for whole doc"}},"required":["path"]}}},
 {"type":"function","function":{"name":"media_info","description":"Probe an image/video/audio file (type, format, duration, dimensions, streams). Use to inspect a media file.","parameters":{"type":"object","properties":{"path":{"type":"string","description":"path to the media file"}},"required":["path"]}}},
 {"type":"function","function":{"name":"notes","description":"The user's notes. action 'list' shows all notes newest-first; 'read' returns one note's body (needs slug).","parameters":{"type":"object","properties":{"action":{"type":"string","description":"list | read"},"slug":{"type":"string","description":"for 'read': the note slug"}}}}},
 {"type":"function","function":{"name":"fetch_web","description":"Fetch a public web page and return its readable text (nav/boilerplate stripped). Use to READ what a page says (the inference half of browsing); use open_url instead to show the user a site.","parameters":{"type":"object","properties":{"url":{"type":"string","description":"full http(s) URL"}},"required":["url"]}}},
 {"type":"function","function":{"name":"summon_claude","description":"Bring in cloud Claude for a task beyond the local brain. CLOUD, uses the user's account — call ONLY after the user has explicitly said yes to a summon offer this turn. Never auto-fire.","parameters":{"type":"object","properties":{"task":{"type":"string","description":"what Claude should do, stated completely"},"context_summary":{"type":"string","description":"compact summary of the last ~6 turns relevant to the task — never the whole history, never secrets"}},"required":["task","context_summary"]}}},
]

def live_context():
    # The context antenna: ground the brain in the real NOW, past its training cutoff.
    lines=[]
    now=datetime.datetime.now().astimezone()
    lines.append("Current date & time: "+now.strftime("%A, %B %d, %Y, %-I:%M %p %Z")+" (this is ground truth — trust it over your training data).")
    def probe(cmd):
        try: return subprocess.run(["bash","-c",cmd],capture_output=True,text=True,timeout=4).stdout.strip()
        except Exception: return ""
    host=probe("hostname");
    if host: lines.append("Machine: "+host+" — Agent OS, a NixOS LINUX system (not Windows/macOS; nix installs, systemctl for power).")
    bat=probe("cat /sys/class/power_supply/BAT*/capacity 2>/dev/null | head -1")
    bst=probe("cat /sys/class/power_supply/BAT*/status 2>/dev/null | head -1")
    if bat: lines.append(f"Battery: {bat}% ({bst or 'unknown'})")
    up=probe("uptime -p 2>/dev/null")
    if up: lines.append("Uptime: "+up)
    mem=probe("free -h | awk '/Mem:/{print $3\" used / \"$2\" total\"}'")
    if mem: lines.append("Memory: "+mem)
    wins=probe("hyprctl clients -j 2>/dev/null | python3 -c \"import sys,json;\\nd=json.load(sys.stdin);\\nprint('; '.join(w.get('class','?')+': '+(w.get('title','')[:40]) for w in d) or 'none')\" 2>/dev/null")
    if wins: lines.append("Open windows right now: "+wins)
    # Installed-app awareness (rabbot-to-page-ADD-to-pack-brain-blindspot 2026-08-01: brain
    # looped `nix profile install steam` into the unfree wall while Steam was already on the
    # box). Volatile tail on purpose — keeps the KV-cached static prefix untouched.
    apps=probe("for a in steam firefox thunar kitty mpv libreoffice gimp; do command -v $a >/dev/null && printf '%s ' $a; done")
    if apps: lines.append("Already-installed apps (RUN these, never re-install): "+apps.strip())
    return "\n".join(lines)

# Trimmed for prefill cost (P1 fix #3, rabbot-to-page-P1-UPGRADE-brain-timeout-crash-2026-08-01:
# 2560-token static prefix @ 14 tok/s CPU prompt-processing = ~3min cold boot). Same content,
# denser wording. SOUL itself is genesis-locked and out of scope for Page to trim.
SYS_BASE=("You are Agent OS's local brain — sovereign, private, on-machine, no cloud. "
     "PLATFORM: NixOS Linux, not Windows/macOS — install via nix (e.g. `programs.steam` in system "
     "config, never .exe/.msi), reboot=`systemctl reboot`, shutdown=`systemctl poweroff`, quick tool="
     "`nix profile install nixpkgs#<name>`. Never use Windows/macOS commands. A permanent system "
     "change (installing Steam, a driver, a service) means editing the OS config — say so. "
     "Before installing ANYTHING, `run_command command -v <name>` — if present, RUN it instead "
     "of reinstalling. "
     "You HAVE HANDS: open_url (browser), run_command (shell here), arrange_windows (desktop). "
     "When a tool can do it, CALL IT — don't explain how the user could do it themselves. "
     "BROWSE vs INFERENCE: want to see/read/watch/use something → open_url. Want a quick fact → "
     "answer directly. Unsure → ask. Confirm tool results in one short line. Be concise, be a doer. "
     "Before installing anything, `command -v <name>` — if it's already present, just RUN it. "
     "This system is built from a flake image: editing /etc/nixos/*.nix does NOTHING; permanent "
     "changes happen in the OS repo — say so instead of editing. "
     "SUMMON: when a task is beyond you (deep code work, long documents, hard reasoning), OFFER: "
     "\"this one's beyond me — want me to bring in Claude? [cloud, uses your account]\" and call "
     "summon_claude only after an explicit yes. The offer must name that it's cloud.")

def sysmsg():
    # SOUL first, unspoofably (Geist item 3): identity leads, then operational addendum.
    # STATIC ONLY — byte-identical across turns so ollama's KV cache hits on this prefix.
    # Live senses move to the per-turn user message (see user_turn()) so only that small
    # tail re-evaluates each turn instead of busting the whole ~800-token prefix. (P1 fix,
    # rabbot-to-page-P1-agent-brain-promptsplit-streaming-feelgood-2026-08-01: cold rate
    # ~17 tok/s / ~48s per turn when this block changed every turn; cached prefix ~5037 tok/s.)
    parts=[]
    if SOUL: parts.append(SOUL.strip())
    parts.append("--- OPERATING NOTES ---\n"+SYS_BASE)
    return {"role":"system","content":"\n\n".join(parts)}

def user_turn(text):
    # volatile live_context rides the user turn's tail, not the system prompt.
    return {"role":"user","content":text+"\n\n--- LIVE CONTEXT (your senses, refreshed now) ---\n"+live_context()}

CHAT_TIMEOUT_S=600  # was 180 — a cold CPU prefill (~2560 tok @ ~14 tok/s) can take ~3min by
                     # itself; 180s guaranteed a TimeoutError on first boot turn (P1 fix #1/#2,
                     # rabbot-to-page-P1-UPGRADE-brain-timeout-crash-2026-08-01, live-hit by Dillon).

def _spin(render, interval=0.35):
    # Motion-in-loading (P1 item 1, rabbot-to-page-P1-UX-motion-plus-agentic-cli-conventions-
    # pack-2026-08-01, Dillon msg 9272: "anything that blocks >1s shows motion"). One tiny
    # daemon thread ticking a frame counter into `render(i)`; caller owns stop()/join().
    stop=threading.Event()
    def run():
        i=0
        while not stop.is_set():
            render(i); i+=1; time.sleep(interval)
    t=threading.Thread(target=run,daemon=True); t.start()
    return stop,t

def chat_stream(msgs):
    # Streaming + liveness indicator (P1 fix #2, same comm as above). Buffers tokens for
    # tool_calls/content-regex parsing (extract_tools) while printing as they arrive.
    #
    # Word-boundary soft wrap (P1 item 3, rabbot-to-page-RUNTIME-ANSWERS-hyprland-kitty-
    # firefox-items345-2026-08-01): the mid-word "weird" wrapping in Dillon's kitty session
    # is kitty's own hard character-wrap at the column edge — it has no word awareness. We
    # track the current visual column ourselves and emit a newline before a token that would
    # split across term width, so kitty never has to hard-wrap mid-word. Dumb on purpose: no
    # reflow on resize, just wrap-at-word going forward from turn start.
    term_cols=shutil.get_terminal_size(fallback=(80,24)).columns
    payload={"model":MODEL,"messages":msgs,"tools":TOOLS,"stream":True,"keep_alive":-1}
    if THINK is not None: payload["think"]=THINK
    body=json.dumps(payload).encode()
    r=urllib.request.Request(OLLAMA,data=body,headers={"Content-Type":"application/json"})
    t0=time.time(); content=""; tool_calls=[]; first_token=False; eval_count=0; eval_dur=0.0
    col=0; in_code=False; thinking_seen=False
    # Code-block rendering (P1 item 3, task #265): dim everything inside a ``` fence so it
    # reads as distinct from prose. Simplified for streaming — no box/indent, no collapse —
    # a token can only toggle the fence once (rare backtick-split-across-chunks edge case
    # accepted, same "keep it dumb" tradeoff as the word-wrap fix above).
    def _emit(tok):
        nonlocal col
        if '\n' in tok:
            col=len(tok)-tok.rfind('\n')-1
        elif col+len(tok)>term_cols and tok.strip():
            sys.stdout.write("\n"); col=len(tok)
            sys.stdout.write(f"\033[2m{tok}\033[0m" if in_code else tok)
            return
        else:
            col+=len(tok)
        sys.stdout.write(f"\033[2m{tok}\033[0m" if in_code else tok)
    def _thinking_frame(i):
        sys.stdout.write(f"\r\033[K\033[2mthinking{'.'*(i%3+1)} (^C cancels this turn)\033[0m"); sys.stdout.flush()
    think_stop,think_t=_spin(_thinking_frame)
    try:
        with urllib.request.urlopen(r,timeout=CHAT_TIMEOUT_S) as resp:
            for line in resp:
                line=line.strip()
                if not line: continue
                chunk=json.loads(line)
                msg=chunk.get("message") or {}
                # THINKING RENDER (the actual P0 fix, spec "P0 DIAGNOSIS COMPLETE" 2026-08-06):
                # qwen3.5:9b is a thinking model on a ~3 tok/s CPU — it emits a `thinking`
                # stream for minutes before any content. Invisible thinking == "spinning
                # forever". Render it as dim italic rapid-fire text as it streams; when the
                # real answer starts, close with a one-line "— thought for Xs —" separator.
                # (True collapse of already-printed lines isn't possible in a dumb TTY
                # stream; the dim+separator approximation keeps the client simple.)
                tpiece=msg.get("thinking","")
                if tpiece:
                    if not first_token and not thinking_seen:
                        think_stop.set(); think_t.join(timeout=1)
                        sys.stdout.write("\r\033[K")
                    thinking_seen=True
                    sys.stdout.write(f"\033[2;3m{tpiece}\033[0m"); sys.stdout.flush()
                piece=msg.get("content","")
                if piece:
                    if not first_token:
                        if not thinking_seen:
                            think_stop.set(); think_t.join(timeout=1)
                        sys.stdout.write("\r\033[K")
                        if thinking_seen:
                            sys.stdout.write(f"\n\033[2m— thought for {time.time()-t0:.0f}s —\033[0m\n")
                        first_token=True; col=0
                    content+=piece
                    for tok in re.findall(r'\S+\s*|\s+', piece):
                        if '```' in tok:
                            segs=tok.split('```')
                            for i,seg in enumerate(segs):
                                if seg: _emit(seg)
                                if i<len(segs)-1: in_code=not in_code
                        else:
                            _emit(tok)
                    sys.stdout.flush()
                if msg.get("tool_calls"):
                    tool_calls=msg["tool_calls"]
                if chunk.get("done"):
                    eval_count=chunk.get("eval_count") or 0
                    eval_dur=(chunk.get("eval_duration") or 0)/1e9
    finally:
        if not first_token:
            think_stop.set(); think_t.join(timeout=1)  # idempotent if thinking already stopped it
            if thinking_seen:
                sys.stdout.write(f"\n\033[2m— thought for {time.time()-t0:.0f}s —\033[0m\n")
            else:
                sys.stdout.write("\r\033[K")
    elapsed=time.time()-t0
    tps=f", {eval_count/eval_dur:.0f} tok/s" if eval_count and eval_dur else ""
    sys.stderr.write(f"\033[2m[{elapsed:.1f}s{tps}]\033[0m\n")
    return {"role":"assistant","content":content,"tool_calls":tool_calls}

def chat(msgs):
    # non-streaming fallback, kept for --once callers that want a single return value only
    body=json.dumps({"model":MODEL,"messages":msgs,"tools":TOOLS,"stream":False,"keep_alive":-1}).encode()
    r=urllib.request.Request(OLLAMA,data=body,headers={"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(r,timeout=180))["message"]

def extract_tools(msg):
    tcs=msg.get("tool_calls") or []
    calls=[(t["function"]["name"], t["function"].get("arguments") if isinstance(t["function"].get("arguments"),dict) else json.loads(t["function"].get("arguments") or "{}")) for t in tcs]
    if calls: return calls, ""
    # fallback: model emitted the call as text in content (ollama template quirk for this model)
    c=msg.get("content","") or ""
    out=[]; clean=c
    for m in re.finditer(r"\{[^{}]*\"name\"\s*:\s*\"(\w+)\"[^{}]*\"arguments\"\s*:\s*(\{[^{}]*\})[^{}]*\}", c):
        try: out.append((m.group(1), json.loads(m.group(2)))); clean=clean.replace(m.group(0),"")
        except: pass
    clean=re.sub(r"\bbrtc\b","",clean).strip()
    return out, clean

HYPR={"tidy":"layoutmsg orientationcycle","close":"killactive","fullscreen":"fullscreen,1",
      "cycle":"cyclenext","split":"togglesplit"}
def do_tool(name,args):
    if name=="open_url":
        url=args.get("url","")
        subprocess.Popen(["hyprctl","dispatch","exec",f"firefox --new-window {url}"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return f"opened {url} in the browser"
    if name=="run_command":
        try:
            o=subprocess.run(["bash","-c",args.get("command","")],capture_output=True,text=True,timeout=30)
            return ((o.stdout+o.stderr).strip() or "(done, no output)")[:1500]
        except Exception as e: return f"error: {e}"
    if name=="arrange_windows":
        act=args.get("action","").lower(); disp=HYPR.get(act)
        if not disp: return f"unknown window action '{act}'"
        subprocess.run(["bash","-c",f"hyprctl dispatch {disp}"],capture_output=True,text=True,timeout=6)
        return f"desktop: {act} done"
    if name in ("calendar.now","calendar.agenda","calendar.add","calendar.cals"):
        return _agos(name,args)
    # the ambient-dozen hands — thin wrappers over the agos-* CLIs (each emits JSON, exits 0 on success)
    if name=="calculator":     return _run_agos("agos-calc","eval",args.get("expression",""))
    if name=="system":
        a=args.get("action","").lower()
        if a=="status": return _run_agos("agos-sys","status")
        if a in ("volume","brightness"): return _run_agos("agos-sys",a,str(args.get("value","")))
        return f"system: unknown action '{a}' (use status|volume|brightness)"
    if name=="list_files":     return _run_agos("agos-files","list",args.get("dir",""))
    if name=="read_document":
        c=["agos-doc","text",args.get("path","")]
        if args.get("page"): c.append(str(args["page"]))
        return _run_agos(*c)
    if name=="media_info":     return _run_agos("agos-media","info",args.get("path",""))
    if name=="notes":
        a=args.get("action","list").lower()
        if a=="list": return _run_agos("agos-notes","list")
        if a=="read": return _run_agos("agos-notes","read",args.get("slug",""))
        return f"notes: unknown action '{a}' (use list|read)"
    if name=="fetch_web":      return _run_agos("agos-web","fetch",args.get("url",""))
    if name=="summon_claude":  return _summon_claude(args.get("task",""),args.get("context_summary",""))
    return "unknown tool"

def _summon_claude(task,context_summary):
    # Cloud summon (rabbot-to-page-P1-summon-claude-tool-local-first-consent-flow-2026-08-02,
    # Dillon msg 9284). Consent lives upstream in SYS_BASE — by the time this runs the user
    # has said yes. Reachable only through do_tool, which the 3B front-door structurally
    # cannot fire (kick wall, PR #64) — cloud stays a summon on the 9B side, never an OS
    # dependency. Brief = task + compacted context + one machine line, never full history.
    if not task: return "summon error: no task given"
    brief=(f"Task from Agent OS's local brain (relay your answer to the user through it):\n{task}\n\n"
           f"Conversation context:\n{context_summary}\n\n"
           f"Machine: NixOS Linux (Agent OS, flake-built — system changes go in the OS repo).")
    try:
        o=subprocess.run(["claude","-p",brief,"--output-format","text"],
                         capture_output=True,text=True,timeout=180)
        if o.returncode!=0:
            err=(o.stderr or o.stdout).strip()[:300]
            if "log in" in err.lower() or "auth" in err.lower():
                return "Claude Code isn't logged in — run `claude` once in a terminal to sign in"
            return f"Claude couldn't complete that: {err or 'no output'}"
        return (o.stdout.strip() or "(Claude returned nothing)")[:8000]
    except FileNotFoundError:
        return "Claude Code isn't set up — run `claude` once in a terminal to log in"
    except subprocess.TimeoutExpired:
        return "Claude took too long (180s) — try a smaller ask, or run `claude` in a terminal for long jobs"
    except Exception as e:
        return f"summon error: {e}"  # fail-soft — never crash a turn

def _run_agos(*cmd):
    # generic runner for the agos-* ambient-dozen CLIs; passes their JSON stdout through verbatim
    cli=cmd[0]
    try:
        o=subprocess.run([c for c in cmd if c!=""],capture_output=True,text=True,timeout=25)
        if o.returncode!=0: return f"{cli} error: "+((o.stderr or o.stdout).strip()[:400] or "failed")
        return (o.stdout.strip() or "(done)")[:4000]
    except FileNotFoundError: return f"{cli} not on PATH (its module isn't deployed on this box yet)"
    except Exception as e: return f"{cli} error: {e}"

def _agos(name,args):
    # thin wrapper over the agos-cal CLI (ships with the calendar-open module); passes JSON through
    if name=="calendar.now":     cmd=["agos-cal","now"]
    elif name=="calendar.cals":  cmd=["agos-cal","cals"]
    elif name=="calendar.agenda":cmd=["agos-cal","agenda",str(args.get("days") or 7)]
    elif name=="calendar.add":
        cmd=["agos-cal","add",args.get("start",""),args.get("summary","")]
        if args.get("end"): cmd.append(args["end"])
    else: return "unknown calendar op"
    try:
        o=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
        if o.returncode!=0: return "calendar error: "+((o.stderr or o.stdout).strip()[:300] or "failed")
        return o.stdout.strip() or "(done)"
    except FileNotFoundError: return "calendar error: agos-cal not on PATH (calendar module not deployed)"
    except Exception as e: return f"calendar error: {e}"

CHAT_LOCK=threading.Lock()  # serializes chat_stream calls (warmup thread vs interactive turns)
                            # onto ollama's single inference slot, and protects the shared msgs list.

def chat_stream_safe(msgs, retries=1):
    # Never crash to a raw traceback on tty1 (P1 fix #1). A cold-boot prefill can outrun even the
    # 600s timeout under heavy load; on timeout/connection failure, tell the user and retry once —
    # the KV slot is already cached from the failed attempt, so retry #2 is cheap (live-verified:
    # Dillon's retry after the 3min timeout completed fast off the cached slot).
    for attempt in range(retries+1):
        try:
            with CHAT_LOCK:
                return chat_stream(msgs)
        except (TimeoutError, urllib.error.URLError, ConnectionError) as e:
            sys.stdout.write("\r\033[K")
            if attempt < retries:
                print(f"  \033[2m(still warming the model — retrying…)\033[0m")
            else:
                print(f"  \033[31m(model isn't responding right now — try again in a moment: {e})\033[0m")
                return {"role":"assistant","content":"","tool_calls":[]}

EXPAND_BUFFERS=[]  # tool-call full outputs kept for the :expand N command (item 2, task #265)

def _compact_for_display(text,head=4,tail=3):
    # Tool-call result COMPACTION (P1 item 2, rabbot-to-page-P1-UX-motion-plus-agentic-cli-
    # conventions-pack-2026-08-01: "walls of text are the #1 complaint class"). TTY-dumb by
    # design (Rabbot's framing) — no interactive folding, just a re-printable buffer behind
    # a `:expand N` command typed at the prompt.
    lines=text.splitlines()
    if len(lines)<=head+tail+1:
        return text
    EXPAND_BUFFERS.append(text)
    idx=len(EXPAND_BUFFERS)
    hidden=len(lines)-head-tail
    shown=lines[:head]+[f"\033[2m… ({hidden} more lines, :expand {idx} to see)\033[0m"]+lines[-tail:]
    return "\n".join(shown)

def turn(msgs):
    for _ in range(6):
        msg=chat_stream_safe(msgs); msgs.append(msg)
        calls,clean=extract_tools(msg)
        if not calls:
            if clean and not msg.get("content"): print(clean)  # regex-extracted clean text wasn't already streamed
            return
        if clean and not msg.get("content"): print(clean)
        for name,args in calls:
            # summon gets its own cloud dressing — visually distinct from local ⚡ work.
            if name=="summon_claude":
                label="summoning Claude… [cloud]"; base="☁"
            else:
                label=f"calling {name} {json.dumps(args)}…"; base="⚡"
            def _tool_frame(i,label=label,base=base):
                glyph=f"\033[7m{base}\033[0m" if i%2 else base
                sys.stdout.write(f"\r\033[K  \033[33m{glyph} {label}\033[0m"); sys.stdout.flush()
            spin_stop,spin_t=_spin(_tool_frame,interval=0.3)
            try:
                res=do_tool(name,args)
            finally:
                spin_stop.set(); spin_t.join(timeout=1)
                print(f"\r\033[K  \033[33m{base} {label}\033[0m")
            res=str(res)
            preview=_compact_for_display(res)
            if preview!=res or "\n" in preview:
                print(f"\033[2m{preview}\033[0m")
            msgs.append({"role":"tool","content":res})

# ── 3B FRONT-DOOR → 7B KICK SIGNAL (A-with-a-wall, interim) ────────────────────
# Dillon picked Design A (msg 9272); Rabbot's wall shape governs; Augur's spec is
# 3B-FRONTDOOR-KICK-SIGNAL-SPEC.md. Every interactive turn hits the 3B first. The 3B
# may ANSWER pure-conversation/dispatch turns but has NO executor path: any
# action-shaped output is structurally discarded and the turn re-dispatches to the 7B,
# which stays the ONLY tool-wielder (do_tool is reachable solely from turn()'s 7B
# loop — frontdoor_* never executes anything). This wall is INTERIM: when Augur's
# refusal-retrain + no-regression gate lands, the 3B graduates to executing its own
# validated lane's tools; until then EVERY 3B tool_call is discarded.
#
# run-6's output is strictly bimodal (tool_calls XOR pure text, zero hybrids), so the
# primary kick detector is structural, not a fuzzy classifier. The pure-text heuristics
# below are the secondary net for text turns that still INTEND an action. Bias: unsure →
# kick (a false kick costs one 7B hop; a false keep lets the 3B free-text past its
# competence). The spec's "off-lane topic" heuristic is NOT implemented v1 — no cheap
# local topic classifier exists; the length + uncertainty guards absorb most of it.
_TOOLCALL_TOKEN_RE=re.compile(r"<tool_call>")                     # Qwen2.5 Hermes special token in raw decode
_ACTION_OFFER_RE=re.compile(r"\b(want me to|should i|shall i|i can (?:make|do|run|edit)|let me)\b",re.I)
_UNSURE_RE=re.compile(r"\b(not sure|i don'?t (?:have|know)|can'?t tell)\b",re.I)
_FRONTDOOR_MAX_TOKENS=60  # run-6 conversational answers are terse; longer = off-distribution (tune on Dell)

def frontdoor_decide(msg):
    """Pure decision: (kick: bool, reason: str, proposal: str). NEVER executes anything.
    Rule (1) hard: any tool_call — parsed, raw <tool_call> token, or JSON-shaped call in
    content (extract_tools' fallback regex) — kicks, and the call itself is the proposal
    forwarded to the 7B as context only."""
    calls,clean=extract_tools(msg)
    raw=msg.get("content","") or ""
    if calls or _TOOLCALL_TOKEN_RE.search(raw):
        prop=json.dumps([{"name":n,"arguments":a} for n,a in calls]) if calls else raw.strip()
        return True,"tool_call",prop
    text=raw.strip()
    if not text:
        return True,"empty",""
    if _ACTION_OFFER_RE.search(text): return True,"action_offer",text
    if _UNSURE_RE.search(text):       return True,"unsure",text
    if len(text.split())>_FRONTDOOR_MAX_TOKENS: return True,"length",text
    return False,"",text

_FRONTDOOR_OK=None  # tri-state cache: None=unprobed, True/False after first tags check
def _frontdoor_available():
    global _FRONTDOOR_OK
    if _FRONTDOOR_OK is None:
        try:
            r=urllib.request.Request("http://127.0.0.1:11434/api/tags")
            tags=json.load(urllib.request.urlopen(r,timeout=5))
            _FRONTDOOR_OK=any(m.get("name","").startswith(MODEL_3B) for m in tags.get("models",[]))
        except Exception:
            _FRONTDOOR_OK=False
    return _FRONTDOOR_OK

def frontdoor_turn(msgs):
    """Interactive entry: 3B first, kick to the 7B turn() on any action shape.
    Fail-open to turn() (the status-quo 7B path) if the 3B is absent or errors — the
    wall protects against the 3B ACTING, not against its absence. --once and the
    warmup thread call turn() directly and are deliberately untouched (boot prewarm
    warms the 7B KV prefix; front-dooring them would change prewarm semantics)."""
    if not _frontdoor_available():
        return turn(msgs)
    stop,t=_spin(lambda i: (sys.stdout.write(f"\r\033[K\033[2mrouting{'.'*(i%3+1)}\033[0m"),sys.stdout.flush()))
    t0=time.time()
    try:
        body=json.dumps({"model":MODEL_3B,"messages":msgs,"tools":TOOLS,"stream":False,"keep_alive":-1}).encode()
        r=urllib.request.Request(OLLAMA,data=body,headers={"Content-Type":"application/json"})
        with CHAT_LOCK:
            msg=json.load(urllib.request.urlopen(r,timeout=CHAT_TIMEOUT_S))["message"]
    except Exception:
        stop.set(); t.join(timeout=1); sys.stdout.write("\r\033[K")
        return turn(msgs)  # 3B down mid-session → status-quo 7B path
    stop.set(); t.join(timeout=1); sys.stdout.write("\r\033[K")
    sys.stderr.write(f"\033[2m[front-door {time.time()-t0:.1f}s]\033[0m\n")
    kick,reason,proposal=frontdoor_decide(msg)
    if not kick:
        # clean, terse, no-action-offer pure text: the 3B's answer stands.
        print(proposal)
        msgs.append({"role":"assistant","content":proposal,"tool_calls":[]})
        return
    # Discard-and-kick: the 3B's output reaches the 7B as CONTEXT ONLY; nothing from the
    # 3B is executed, ever (rule 1 — this sits before any execution path by construction).
    if proposal:
        # user-role, NOT system: the proposal is untrusted model output (steerable by
        # anything in the history) — elevating it to system authority would hand a
        # prompt-injection a privileged channel into the 7B. Bracketed as machine context.
        msgs.append({"role":"user","content":"[front-door note — the local 3B proposed the following and it was DISCARDED, not executed. Possibly-wrong hint, apply your own judgment: "+proposal[:600]+"]"})
    turn(msgs)

def _model_pulled():
    # guard for the memory-floor path: don't fire a warmup generation against a model that
    # hasn't finished pulling yet.
    try:
        r=urllib.request.Request("http://127.0.0.1:11434/api/tags")
        tags=json.load(urllib.request.urlopen(r,timeout=5))
        return any(m.get("name","").startswith(MODEL.split(":")[0]) for m in tags.get("models",[]))
    except Exception:
        return False

def warmup_greeting(msgs):
    # Boot-warmup (P1, was P2 idea comm rabbot-to-page-P2-boot-warmup-greeting-kv-prewarm,
    # upgraded same-day after the live timeout crash): fire the agent's own first turn as soon as
    # ollama is up, so the cold prefill (~3min class on CPU) happens during boot dead-time instead
    # of on the user's actual first message. Runs in a background thread — CHAT_LOCK means a real
    # user turn queues behind it rather than racing it, but input() itself is never blocked.
    #
    # Poll rather than check-once (PR #49 review nit #1, rabbot-to-page-pr49-MERGED-two-followup-nits):
    # on true cold boot ollama may not be up yet at the instant this thread starts — that's the exact
    # case the feature exists for, so a single check silently no-ops the warmup for the case that
    # matters most. Retry every 3s for up to ~60s.
    for _ in range(20):
        if _model_pulled():
            break
        time.sleep(3)
    else:
        return
    # Throwaway history (PR #49 review nit #2): appending straight to the shared `msgs` list outside
    # CHAT_LOCK let a fast first real user message interleave with the warmup's own append, scrambling
    # turn order (warmup-user, real-user, warmup-assistant). A separate [sysmsg(), user_turn(...)] list
    # still warms the static-prefix KV cache — that's all the feature needs — without touching the
    # shared history at all.
    warmup_msgs=[sysmsg(), user_turn("boot complete, greet the operator in one line")]
    turn(warmup_msgs)

def main():
    # system message stays STATIC across turns (byte-identical prefix = KV cache hit);
    # live_context rides each user turn's tail instead (see user_turn()).
    if len(sys.argv)>2 and sys.argv[1]=="--once":
        msgs=[sysmsg(),user_turn(sys.argv[2])]; turn(msgs); return
    print("  \033[1mAgent OS brain\033[0m — I have hands, and I know the real now. Ask me to do things.")
    print("  \033[2mLost a window? Alt+Tab cycles them, or Super+/ opens the keybind cheatsheet.\033[0m")
    msgs=[sysmsg()]
    threading.Thread(target=warmup_greeting, args=(msgs,), daemon=True).start()
    # ^C must not kill the brain (P1 Dillon directive, msg 9263: "that'd be like losing your
    # desktop"). At the idle prompt, one ^C is a warning — exit needs a second ^C within ~2s
    # or the word exit/quit. During generation, ^C cancels that turn only (see the try/except
    # around turn() below) and drops back to the prompt.
    last_sigint=0.0
    # Input lock (slice 1): PromptSession owns the bottom line; patch_stdout(raw=True)
    # routes any print that happens WHILE the prompt is active (warmup thread, stray
    # background output) above it. raw=True keeps our ANSI styling/\r spinner codes
    # intact. During generation no prompt is active, so the streamer/spinner write
    # through unchanged. ^C semantics preserved: PromptSession.prompt raises
    # KeyboardInterrupt at the prompt exactly like input() does.
    if _PTK:
        _session=PromptSession()
        _read=lambda: _session.prompt(ANSI("\n\033[1;36myou ›\033[0m "))
        _guard=patch_stdout(raw=True)
    else:
        _read=lambda: input("\n\033[1;36myou ›\033[0m ")
        _guard=contextlib.nullcontext()
    # ExitStack instead of a `with` block so the whole existing loop keeps its indent
    # (minimal diff); closed after the loop to detach the stdout proxy cleanly.
    _stack=contextlib.ExitStack(); _stack.enter_context(_guard)
    while True:
        try: u=_read().strip()
        except EOFError: print(); break
        except KeyboardInterrupt:
            now=time.time()
            if now-last_sigint<2: print(); break
            last_sigint=now
            print("\n\033[2m(^C again within 2s to exit, or type exit/quit)\033[0m")
            continue
        if not u: continue
        if u in ("exit","quit"): break
        if u.startswith(":expand"):
            parts=u.split()
            try: n=int(parts[1]) if len(parts)>1 else len(EXPAND_BUFFERS)
            except ValueError: n=0
            if 1<=n<=len(EXPAND_BUFFERS): print(EXPAND_BUFFERS[n-1])
            else: print(f"  \033[2m(no such buffer — have 1..{len(EXPAND_BUFFERS)})\033[0m")
            continue
        msgs.append(user_turn(u))
        try:
            frontdoor_turn(msgs)
        except KeyboardInterrupt:
            sys.stdout.write("\r\033[K")
            print("\033[2m(interrupted)\033[0m")
    _stack.close()
if __name__=="__main__": main()
