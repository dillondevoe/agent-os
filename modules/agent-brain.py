#!/usr/bin/env python3
# Agent OS local brain — WITH HANDS + CONTEXT ANTENNA.
# Talk to it; it acts (browse, run commands, arrange windows) AND it knows the real NOW.
import json, re, subprocess, sys, urllib.request, datetime, os, hashlib

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

SYS_BASE=("You are the local brain of Agent OS, running ON the user's own machine — sovereign, private, no cloud. "
     "PLATFORM (critical — you are NOT on Windows or macOS): Agent OS is a NixOS-based LINUX system. Software is "
     "installed the NixOS way — declaratively via nix (e.g. Steam = enable `programs.steam` in the system config, "
     "NOT a downloaded .exe/.msi installer). NEVER use Windows commands (no `shutdown /r`, no .exe, no PowerShell) "
     "or macOS commands. On this machine: reboot = `systemctl reboot`, shutdown = `systemctl poweroff`, install a "
     "quick tool = `nix profile install nixpkgs#<name>`, packages live in nixpkgs. If a task needs a permanent "
     "system change (installing Steam, a driver, a service), say so — that's a change to the OS config, and note it. "
     "You HAVE HANDS: open_url opens a website in the browser, run_command runs a shell command here, "
     "arrange_windows rearranges the desktop. When the user asks for something a tool can do, CALL THE TOOL and DO IT — "
     "never explain how they could do it themselves. "
     "BROWSE vs INFERENCE: if they want to SEE, read, watch, or use something → open_url (browse). "
     "If they just want a quick answer or fact → answer directly from what you know (inference). When unsure, ask which. "
     "After a tool runs, confirm in one short line. Be concise and direct — you're a doer, not a lecturer.")

def sysmsg():
    # SOUL first, unspoofably (Geist item 3): identity leads, then operational addendum, then live senses.
    parts=[]
    if SOUL: parts.append(SOUL.strip())
    parts.append("--- OPERATING NOTES ---\n"+SYS_BASE)
    parts.append("--- LIVE CONTEXT (your senses, refreshed now) ---\n"+live_context())
    return {"role":"system","content":"\n\n".join(parts)}

def chat(msgs):
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

def turn(msgs):
    for _ in range(6):
        msg=chat(msgs); msgs.append(msg)
        calls,clean=extract_tools(msg)
        if not calls:
            if clean: print(clean)
            return
        if clean: print(clean)
        for name,args in calls:
            print(f"  \033[33m⚡ {name} {json.dumps(args)}\033[0m")
            res=do_tool(name,args); msgs.append({"role":"tool","content":str(res)})

def main():
    # system message is rebuilt each turn so the context antenna stays fresh
    if len(sys.argv)>2 and sys.argv[1]=="--once":
        msgs=[sysmsg(),{"role":"user","content":sys.argv[2]}]; turn(msgs); return
    print("  \033[1mAgent OS brain\033[0m — I have hands, and I know the real now. Ask me to do things.")
    msgs=[sysmsg()]
    while True:
        try: u=input("\n\033[36myou ›\033[0m ").strip()
        except (EOFError,KeyboardInterrupt): print(); break
        if not u: continue
        if u in ("exit","quit"): break
        msgs[0]=sysmsg()  # refresh senses each turn
        msgs.append({"role":"user","content":u}); turn(msgs)
if __name__=="__main__": main()
