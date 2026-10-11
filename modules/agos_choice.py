#!/usr/bin/env python3
# modules/agos_choice.py — the surface choice (docs/design/first-login-and-setup-window.md §3-§4).
# Stdlib only. One file, four roles, each installed as its own command with the role fixed:
#
#   agentos-firstlogin        the questions. Runs as the unprivileged `agentos-setup` user on tty1.
#                             Reads the keyboard; writes three tokens to one file; never execs.
#   agentos-surface-apply     the root helper. Reads that one file, accepts three tokens that are each
#                             in its compiled-in list, writes the store. Parses nothing else.
#   agentos-terminal          exec the chosen terminal   } from a compiled-in table of store paths;
#   agentos-shell             exec the chosen shell      } the environment never decides which.
#   agentos-surface           print the validated choice (read-only).
#
# THE STORE is /var/lib/agent-os/surface.toml, root 0644: a schema version and three enum values.
# read_store() is its ONLY reader. It returns values from its own closed lists and nothing else, so
# no caller can be handed a path or a command; anything wrong with the file means the defaults.
#
# WHY THE SPLIT (Fable spec ruling 4): the program that reads keyboard bytes has no privilege, and
# the program that has privilege reads no keyboard — it reads at most 64 bytes of a file whose
# owner, type, size and filesystem it checks first, and compares three words against a list.
#
# The lists, the defaults, the exec tables and the paths are compiled in by surface-choice-open.nix.
# The literal placeholder means "repo copy", and ONLY the repo copy reads AGOS_CHOICE_CONFIG (tests);
# the installed copy ignores the environment for every decision.
import glob, json, os, pwd, select, signal, stat, sys, time

CONFIG_JSON = "@AGOS_CHOICE_CONFIG@"

KINDS = ("desktop", "terminal", "shell")
STORE_MAX = 4096           # bytes
TOKENS_MAX = 64            # bytes
LINE_MAX = 16              # bytes a typed line may have, newline included
TIMEOUT = 300              # seconds for the whole front end; then the defaults
ATTEMPTS = 5               # bad lines per question before its default is taken


def config():
    """The compiled-in configuration. The repo copy takes it from the environment (tests)."""
    raw = os.environ.get("AGOS_CHOICE_CONFIG", "") if CONFIG_JSON.startswith("@") else CONFIG_JSON
    try:
        c = json.loads(raw)
    except ValueError:
        raise SystemExit("agos-choice: not configured on this box")
    for kind in KINDS:
        offered = c["offered"][kind]
        if not offered or c["defaults"][kind] not in offered:
            raise SystemExit("agos-choice: the compiled-in default for %s is not offered" % kind)
    return c


def log(line):
    """One journal line. Never the typed text: callers pass reasons and enum values only."""
    try:
        import syslog
        syslog.openlog("agos-choice", 0, syslog.LOG_USER)
        syslog.syslog(syslog.LOG_INFO, line)
    except Exception:
        pass


# ── the store reader ─────────────────────────────────────────────────────────────────────────────
def _read_checked(path, max_bytes, owner_uid, same_dev_as):
    """The bytes of `path`, or (None, reason). Refuses a symlink, anything but a regular file, the
    wrong owner, a file on another filesystem, and an oversize file. One open, one read."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as e:
        return None, "unreadable (%s)" % e.__class__.__name__
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None, "not a regular file"
        if st.st_uid != owner_uid:
            return None, "wrong owner"
        if st.st_dev != os.stat(same_dev_as).st_dev:
            return None, "wrong filesystem"
        if st.st_size > max_bytes:
            return None, "oversize"
        data = os.read(fd, max_bytes + 1)
        if len(data) > max_bytes:
            return None, "oversize"
        return data, None
    except OSError as e:
        return None, "unreadable (%s)" % e.__class__.__name__
    finally:
        os.close(fd)


def read_store(c=None):
    """(choice, reason). `choice` always has the three kinds, each a value from the compiled-in
    list. `reason` is None when the store was valid, else why the defaults were used."""
    c = c or config()
    defaults = {k: c["defaults"][k] for k in KINDS}
    # "On the root filesystem": if an image ever puts /var on its own filesystem this fails closed to
    # the defaults (with a journal line per use); compile store_dev to the state directory then.
    data, why = _read_checked(c["store"], STORE_MAX, c.get("store_uid", 0), c.get("store_dev", "/"))
    if data is None:
        return defaults, why
    try:
        import tomllib
        doc = tomllib.loads(data.decode("utf-8"))
    except Exception:
        return defaults, "not valid TOML"
    if set(doc) != {"schema"} | set(KINDS):
        return defaults, "unexpected keys"
    if type(doc["schema"]) is not int or doc["schema"] != 1:      # not 1.0, not true
        return defaults, "unknown schema"
    choice = {}
    for k in KINDS:
        v = doc[k]
        if not isinstance(v, str) or v not in c["offered"][k]:
            return defaults, "%s is not an offered value" % k
        choice[k] = c["offered"][k][c["offered"][k].index(v)]   # OUR string, not the file's
    return choice, None


def _write_atomic(path, data, mode):
    d = os.path.dirname(path)
    for stale in glob.glob(os.path.join(glob.escape(d), ".%s.tmp.*" % os.path.basename(path))):
        try:
            os.unlink(stale)                         # a temp left by a run that died mid-write
        except OSError:
            pass
    tmp = os.path.join(d, ".%s.tmp.%d" % (os.path.basename(path), os.getpid()))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
    try:
        os.write(fd, data)
        os.fchmod(fd, mode)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.rename(tmp, path)
    dfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


# ── the front end (unprivileged; reads the keyboard; never execs) ────────────────────────────────
def _valid_line(raw, n):
    """A typed line -> ('default',), ('skip',), ('pick', i) or None (invalid). `raw` is bytes."""
    if len(raw) > LINE_MAX or not raw.endswith(b"\n"):
        return None
    body = raw[:-1]
    if any(b < 0x20 or b > 0x7e for b in body):       # ESC, other controls, paste, non-ASCII
        return None
    text = body.decode("ascii").strip()
    if text == "":
        return ("default",)
    if text == "s":
        return ("skip",)
    if text.isdigit() and len(text) <= 2 and 1 <= int(text) <= n:
        return ("pick", int(text) - 1)
    return None


def _flush_input(fd):
    try:
        import termios
        termios.tcflush(fd, termios.TCIFLUSH)
    except Exception:
        pass


def _read_line(fd, deadline):
    """One read from the terminal (canonical mode hands over a whole line), or None on timeout/EOF."""
    left = deadline - time.monotonic()
    if left <= 0:
        return None
    try:
        r, _, _ = select.select([fd], [], [], left)
    except (OSError, ValueError):
        return None
    if not r:
        return None
    try:
        raw = os.read(fd, 4096)
    except OSError:
        return None
    return raw or None


_KEYBOARD_SIGNALS = ("SIGINT", "SIGQUIT", "SIGTSTP", "SIGTTIN", "SIGTTOU", "SIGHUP")


def _quiet_keyboard(fd):
    """Ctrl-C, Ctrl-\\ and Ctrl-Z must not kill or stop the questions: a dead front end writes no
    tokens, the helper then fails, and the box asks again on every boot. Ignore the signals, and on a
    real terminal turn off the keys that send them (ISIG) and the literal-next key (IEXTEN), so they
    arrive as ordinary control bytes, which the line rule rejects. Returns what to restore."""
    saved = {}
    for name in _KEYBOARD_SIGNALS:
        try:
            saved[name] = signal.signal(getattr(signal, name), signal.SIG_IGN)
        except (OSError, ValueError, AttributeError):
            pass
    attrs = None
    try:
        import termios
        if os.isatty(fd):
            attrs = termios.tcgetattr(fd)
            new = list(attrs)
            new[3] &= ~(termios.ISIG | termios.IEXTEN)
            termios.tcsetattr(fd, termios.TCSANOW, new)
    except Exception:
        attrs = None
    return saved, attrs


def _restore_keyboard(fd, state):
    saved, attrs = state
    if attrs is not None:
        try:
            import termios
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        except Exception:
            pass
    for name, old in saved.items():
        try:
            signal.signal(getattr(signal, name), old)
        except (OSError, ValueError, TypeError):
            pass


def firstlogin(c=None, fd_in=0, out=None):
    """Ask up to three questions; write the tokens file. Returns the choice it wrote. Whatever
    happens while asking, the tokens file is written: the helper always has something to read."""
    c = c or config()
    out = out or sys.stdout
    choice = {k: c["defaults"][k] for k in KINDS}
    state = _quiet_keyboard(fd_in)
    try:
        _ask(c, choice, fd_in, out)
    except Exception:
        pass                                         # the answers so far, the defaults for the rest
    finally:
        _restore_keyboard(fd_in, state)
    tokens = " ".join(choice[k] for k in KINDS) + "\n"
    _write_atomic(c["tokens"], tokens.encode("ascii"), 0o600)
    try:
        out.write("\nDesktop %s, terminal %s, shell %s.\n" % tuple(choice[k] for k in KINDS)); out.flush()
    except Exception:
        pass
    return choice


def _ask(c, choice, fd_in, out):
    deadline = time.monotonic() + TIMEOUT
    out.write("\nAgent OS — first login. Three questions; Enter takes the default, s skips the rest.\n")
    skip = False
    for kind in KINDS:
        offered = c["offered"][kind]
        if skip or len(offered) < 2:
            continue
        for _ in range(ATTEMPTS):
            out.write("\n%s:\n" % kind.capitalize())
            for i, name in enumerate(offered):
                out.write("  %d) %s%s\n" % (i + 1, name, "   (default)" if name == choice[kind] else ""))
            out.write("> "); out.flush()
            _flush_input(fd_in)
            raw = _read_line(fd_in, deadline)
            if raw is None:                          # timeout or EOF: the defaults for what is left
                skip = True
                break
            got = _valid_line(raw, len(offered))
            if got is None:
                out.write("Please type a number from the list, s, or just Enter.\n")
                continue
            if got[0] == "skip":
                skip = True
            elif got[0] == "pick":
                choice[kind] = offered[got[1]]
            break


# ── the root helper (privileged; reads one small file; no keyboard) ──────────────────────────────
def apply(c=None):
    """Tokens file -> store. Returns 0 if the store was written, 1 if the tokens were refused."""
    c = c or config()
    try:
        owner = c["tokens_uid"] if "tokens_uid" in c else pwd.getpwnam(c["tokens_user"]).pw_uid
    except KeyError:
        log("apply refused: no such user for the tokens file")
        return 1
    data, why = _read_checked(c["tokens"], TOKENS_MAX, owner, c.get("tokens_dev", "/run"))
    if data is None:
        log("apply refused: tokens %s" % why)
        return 1
    try:
        os.unlink(c["tokens"])                       # read once
    except OSError:
        pass
    try:
        words = data.decode("ascii").split()
    except UnicodeDecodeError:
        words = []
    if len(words) != 3 or any(w not in c["offered"][k] for k, w in zip(KINDS, words)):
        log("apply refused: tokens are not three offered values")
        return 1
    picked = [c["offered"][k][c["offered"][k].index(w)] for k, w in zip(KINDS, words)]   # OUR strings
    body = "schema = 1\n" + "".join('%s = "%s"\n' % (k, v) for k, v in zip(KINDS, picked))
    _write_atomic(c["store"], body.encode("ascii"), 0o644)
    log("store written: desktop=%s terminal=%s shell=%s" % tuple(picked))
    return 0


# ── the wrappers (exec from a compiled-in table; the environment never decides) ──────────────────
def run(kind, argv, c=None):
    """exec the chosen terminal or shell. Only returns if the exec itself fails."""
    c = c or config()
    choice, why = read_store(c)
    if why:
        log("%s: store %s; using the default" % (kind, why))
    table = c["exec"][kind]
    path = table.get(choice[kind]) or table[c["defaults"][kind]]
    # The dash login(1), sshd and `su -l` put on argv[0] is lost before this code runs (the wrapper and
    # python both replace argv[0]), so the rule is by arguments: a shell started with NONE is the
    # login / interactive case and gets the dash; one started with any (`-c cmd`, a script: ssh host
    # cmd, scp, rsync, `su -c`) is not a login shell and must not read the profile.
    argv0 = "-" + os.path.basename(path) if (kind == "shell" and not argv) else path
    os.execv(path, [argv0] + argv)


def main(argv):
    role = argv[1] if len(argv) > 1 else ""
    if role == "firstlogin" and len(argv) == 2:
        firstlogin()
        return 0
    if role == "apply" and len(argv) == 2:
        return apply()
    if role in ("terminal", "shell"):
        run(role, argv[2:])
        return 127
    if role == "read" and len(argv) == 2:
        choice, why = read_store()
        for k in KINDS:
            sys.stdout.write("%s=%s\n" % (k, choice[k]))
        if why:
            sys.stderr.write("store %s; these are the defaults\n" % why)
        return 0
    sys.stderr.write("usage: agos_choice.py firstlogin | apply | terminal [args] | shell [args] | read\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
