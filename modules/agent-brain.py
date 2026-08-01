#!/usr/bin/env python3
# Agent OS local brain — WITH HANDS + CONTEXT ANTENNA.
# Talk to it; it acts (browse, run commands, arrange windows) AND it knows the real NOW.
import json, re, subprocess, sys, urllib.request, urllib.error, datetime, os, hashlib, time, threading, shutil

OLLAMA="http://127.0.0.1:11434/api/chat"; MODEL="qwen2.5:7b-instruct"

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
    return "\n".join(lines)

# Trimmed for prefill cost (P1 fix #3, rabbot-to-page-P1-UPGRADE-brain-timeout-crash-2026-08-01:
# 2560-token static prefix @ 14 tok/s CPU prompt-processing = ~3min cold boot). Same content,
# denser wording. SOUL itself is genesis-locked and out of scope for Page to trim.
SYS_BASE=("You are Agent OS's local brain — sovereign, private, on-machine, no cloud. "
     "PLATFORM: NixOS Linux, not Windows/macOS — install via nix (e.g. `programs.steam` in system "
     "config, never .exe/.msi), reboot=`systemctl reboot`, shutdown=`systemctl poweroff`, quick tool="
     "`nix profile install nixpkgs#<name>`. Never use Windows/macOS commands. A permanent system "
     "change (installing Steam, a driver, a service) means editing the OS config — say so. "
     "You HAVE HANDS: open_url (browser), run_command (shell here), arrange_windows (desktop). "
     "When a tool can do it, CALL IT — don't explain how the user could do it themselves. "
     "BROWSE vs INFERENCE: want to see/read/watch/use something → open_url. Want a quick fact → "
     "answer directly. Unsure → ask. Confirm tool results in one short line. Be concise, be a doer.")

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
    body=json.dumps({"model":MODEL,"messages":msgs,"tools":TOOLS,"stream":True}).encode()
    r=urllib.request.Request(OLLAMA,data=body,headers={"Content-Type":"application/json"})
    t0=time.time(); content=""; tool_calls=[]; first_token=False; eval_count=0; eval_dur=0.0
    col=0
    sys.stdout.write("\033[2mthinking…\033[0m"); sys.stdout.flush()
    with urllib.request.urlopen(r,timeout=CHAT_TIMEOUT_S) as resp:
        for line in resp:
            line=line.strip()
            if not line: continue
            chunk=json.loads(line)
            msg=chunk.get("message") or {}
            piece=msg.get("content","")
            if piece:
                if not first_token:
                    sys.stdout.write("\r\033[K"); first_token=True
                content+=piece
                for tok in re.findall(r'\S+\s*|\s+', piece):
                    if '\n' in tok:
                        col=len(tok)-tok.rfind('\n')-1
                    elif col+len(tok)>term_cols and tok.strip():
                        sys.stdout.write("\n"); col=len(tok); sys.stdout.write(tok)
                        continue
                    else:
                        col+=len(tok)
                    sys.stdout.write(tok)
                sys.stdout.flush()
            if msg.get("tool_calls"):
                tool_calls=msg["tool_calls"]
            if chunk.get("done"):
                eval_count=chunk.get("eval_count") or 0
                eval_dur=(chunk.get("eval_duration") or 0)/1e9
    if not first_token: sys.stdout.write("\r\033[K")
    elapsed=time.time()-t0
    tps=f", {eval_count/eval_dur:.0f} tok/s" if eval_count and eval_dur else ""
    sys.stderr.write(f"\033[2m[{elapsed:.1f}s{tps}]\033[0m\n")
    return {"role":"assistant","content":content,"tool_calls":tool_calls}

def chat(msgs):
    # non-streaming fallback, kept for --once callers that want a single return value only
    body=json.dumps({"model":MODEL,"messages":msgs,"tools":TOOLS,"stream":False}).encode()
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
    return "unknown tool"

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

def turn(msgs):
    for _ in range(6):
        msg=chat_stream_safe(msgs); msgs.append(msg)
        calls,clean=extract_tools(msg)
        if not calls:
            if clean and not msg.get("content"): print(clean)  # regex-extracted clean text wasn't already streamed
            return
        if clean and not msg.get("content"): print(clean)
        for name,args in calls:
            print(f"  \033[33m⚡ calling {name} {json.dumps(args)}…\033[0m")
            res=do_tool(name,args); msgs.append({"role":"tool","content":str(res)})

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
    while True:
        try: u=input("\n\033[36myou ›\033[0m ").strip()
        except EOFError: print(); break
        except KeyboardInterrupt:
            now=time.time()
            if now-last_sigint<2: print(); break
            last_sigint=now
            print("\n\033[2m(^C again within 2s to exit, or type exit/quit)\033[0m")
            continue
        if not u: continue
        if u in ("exit","quit"): break
        msgs.append(user_turn(u))
        try:
            turn(msgs)
        except KeyboardInterrupt:
            sys.stdout.write("\r\033[K")
            print("\033[2m(interrupted)\033[0m")
if __name__=="__main__": main()
