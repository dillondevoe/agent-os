#!/usr/bin/env python3
"""agos-user-drift — is DECLARED STATE the whole of who can log in and who is privileged?

WHY THIS EXISTS. The open variant sets `users.mutableUsers = true` DELIBERATELY (it is a dev
box; runtime passwd changes are meant to persist). That is a declared choice, not drift — but
it has a consequence nothing on this box checks: /etc/passwd and /etc/group are hand-editable
and survive every rebuild, so a user added at a console, or a name appended to `wheel`, is
invisible to `nix flake check`, to the module, and to agos-key-drift. It is the same class as
the undeclared root key removed 2026-08-31 (M1), one file over.

SCOPE, deliberately narrow (Rabbot, 2026-08-31): /etc/passwd + /etc/group vs the declared
spec. The tailscale-ssh user mapping is a SEPARATE arm and is not folded in here.

THE ORACLE IS THE BOX'S OWN DECLARED SPEC, not a list re-typed into this file. NixOS writes
`users-groups.json` into the store and names it in /run/current-system/activate; that file is
what the activation script itself consults. A re-typed roster would drift from the module the
moment anyone edited configuration-open.nix, and the scanner would then be measuring my memory.

WHAT IT REPORTS, and why each one is here rather than "any difference":
  UNDECLARED-USER        an account no declared user creates. Can hold a password and a shell.
  UNDECLARED-GROUP       a group no declared group creates.
  UNDECLARED-MEMBER      someone in a DECLARED group who is not in its declared member list.
                         Checked PER GROUP, never as a union — a union would let membership of
                         a harmless group launder membership of `wheel`.
  PRIMARY-GID-MEMBER     the same escalation reached the other way. A user whose PASSWD GID is
                         a group's gid is a member of it WITHOUT EVER APPEARING in /etc/group's
                         member list. A members-only check reads that box as clean. This is the
                         "fingerprint, not line" lesson from agos-key-drift in its local form:
                         compare the PROPERTY (who is in the group), not the one spelling of it
                         you happened to look at.
  SHELL-DRIFT            a declared user whose live shell differs from the declared one. A
                         system account flipped from nologin to a real shell is a login the
                         declared state does not grant.

REPORT, DO NOT REPAIR. Removing a user is destructive and can lock out a real human; that is a
ruling, not a scan. Three exit codes, same law as agos-key-drift:
  0 clean · 1 drift · 2 CANNOT-ASSESS — an instrument that cannot read its input must not
  report clean, so 2 is emphatically not 0.
"""
import json
import os
import re
import sys

PREFIX = os.environ.get("AGOS_USER_DRIFT_ROOT", "")      # test seam: point the scan at a fixture
REPORT = os.environ.get("AGOS_USER_DRIFT_REPORT",
                        PREFIX + "/var/lib/agos-user-drift/report.txt")

out = []
rc = 0


def say(line):
    out.append(line)
    print(line)


def find_spec():
    """Locate the declared users/groups spec the way the ACTIVATION SCRIPT does.

    AGOS_USER_DRIFT_SPEC overrides for fixtures. Otherwise: read the store path out of
    /run/current-system/activate. Deriving it any other way would be a second spelling of
    the same fact with nothing asserting the two agree.
    """
    override = os.environ.get("AGOS_USER_DRIFT_SPEC")
    if override:
        return override
    act = PREFIX + "/run/current-system/activate"
    try:
        text = open(act).read()
    except OSError as e:                       # absent/unreadable — NOT "nothing is declared"
        raise LookupError("cannot read %s (%s)" % (act, e.__class__.__name__))
    m = re.search(r"/nix/store/[^\s\"']*users-groups\.json", text)
    if not m:
        raise LookupError("no users-groups.json named in %s" % act)
    return PREFIX + m.group(0)


def parse_passwd(path):
    users = {}
    for line in open(path):
        line = line.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        f = line.split(":")
        if len(f) < 7:
            raise ValueError("malformed passwd line in %s: %r" % (path, line))
        users[f[0]] = {"uid": f[2], "gid": f[3], "shell": f[6]}
    return users


def parse_group(path):
    groups = {}
    for line in open(path):
        line = line.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        f = line.split(":")
        if len(f) < 4:
            raise ValueError("malformed group line in %s: %r" % (path, line))
        groups[f[0]] = {"gid": f[2],
                        "members": [m for m in f[3].split(",") if m]}
    return groups


def main():
    global rc
    say("agos-user-drift %s host=%s" % (
        __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        os.uname().nodename))
    try:
        spec_path = find_spec()
        spec = json.load(open(spec_path))
        live_users = parse_passwd(PREFIX + "/etc/passwd")
        live_groups = parse_group(PREFIX + "/etc/group")
    except (LookupError, OSError, ValueError) as e:
        say("CANNOT-ASSESS: %s" % e)
        say("RESULT CANNOT-ASSESS")
        write(2)
        return 2

    say("spec: %s" % spec_path)
    decl_users = {u["name"]: u for u in spec.get("users", [])}
    decl_groups = {g["name"]: g for g in spec.get("groups", [])}
    say("declared: %d users, %d groups; live: %d users, %d groups"
        % (len(decl_users), len(decl_groups), len(live_users), len(live_groups)))

    for name, live in sorted(live_users.items()):
        if name not in decl_users:
            say("UNDECLARED-USER %s (uid=%s shell=%s)  <- no declared user creates this account"
                % (name, live["uid"], live["shell"]))
            rc = 1
            continue
        want_shell = decl_users[name].get("shell")
        if want_shell and live["shell"] != want_shell:
            say("SHELL-DRIFT %s: live=%s declared=%s" % (name, live["shell"], want_shell))
            rc = 1

    for gname, live in sorted(live_groups.items()):
        if gname not in decl_groups:
            say("UNDECLARED-GROUP %s (gid=%s members=%s)"
                % (gname, live["gid"], ",".join(live["members"]) or "-"))
            rc = 1
            continue
        declared_members = set(decl_groups[gname].get("members") or [])
        for m in live["members"]:
            if m not in declared_members:
                say("UNDECLARED-MEMBER %s in %s  <- declared members: %s"
                    % (m, gname, ",".join(sorted(declared_members)) or "(none)"))
                rc = 1
        # The other way in: primary GID. Membership without a line in /etc/group.
        for uname, u in sorted(live_users.items()):
            if u["gid"] == live["gid"] and uname not in declared_members:
                dg = decl_users.get(uname, {}).get("group")
                if dg == gname:
                    continue                    # declared AS this user's primary group
                say("PRIMARY-GID-MEMBER %s has %s as its primary group (gid=%s) but is not a "
                    "declared member of it" % (uname, gname, live["gid"]))
                rc = 1

    say("RESULT %s" % ("clean" if rc == 0 else "DRIFT"))
    write(rc)
    return rc


def write(code):
    try:
        os.makedirs(os.path.dirname(REPORT), exist_ok=True)
        with open(REPORT, "w") as f:
            f.write("\n".join(out) + "\n")
    except OSError as e:
        # A report we could not write is not a scan we did not do — say so and keep the code.
        print("agos-user-drift: could not write %s (%s)" % (REPORT, e), file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
