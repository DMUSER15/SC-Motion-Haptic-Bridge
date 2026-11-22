"""scm_proc.py - the one rule for "which program is this process running".

Used by the launcher (scripts/sc_start.py), the Linux setup code (setup/linux/scm_linux.py) and
the Windows process helper (setup/windows/winproc.py), so "is the game / SimHub / the bridge
running" means the same everywhere.

  base(arg)          file name of a path, lower case ("C:\\x\\SimHubWPF.exe" -> "simhubwpf.exe")
  program(argv)      the program of a command line: the executable (Wine shows the Windows
                     .exe as the first argument), or for "python3 [-opts] script.py" the first
                     argument that is not an option (so "python -u -mwrapper lm_simhub4t.py"
                     counts as lm_simhub4t.py; add-ons that wrap the tee rely on this). An
                     editor or shell that only mentions a name does not count.
  processes()        Linux: [(pid, argv)] of every other readable process (from /proc)
  scan(*needles)     Linux: [(pid, command line)] whose program is one of needles
  wine_state(needle) Linux: is a Windows program under Wine really running, or only a stuck
                     process whose wineserver is gone ("none" | "running" | "stuck", [pids])
"""
import os

PYTHON_NAMES = ("python", "pythonw", "py.exe")


def base(arg):
    return arg.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()


def program(argv):
    if not argv:
        return ""
    first = base(argv[0])
    if first.startswith(PYTHON_NAMES) or first == "py":
        for a in argv[1:]:
            if not a.startswith("-"):
                return base(a)
    return first


def processes(proc="/proc"):
    me = os.getpid()
    out = []
    try:
        names = os.listdir(proc)
    except OSError:
        return out
    for d in names:
        if not d.isdigit() or int(d) == me:
            continue
        try:
            with open(os.path.join(proc, d, "cmdline"), "rb") as f:
                raw = f.read()
        except OSError:
            continue
        argv = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
        if argv:
            out.append((int(d), argv))
    return out


def scan(*needles):
    want = {n.lower() for n in needles}
    return [(pid, " ".join(argv)) for pid, argv in processes() if program(argv) in want]


def environ_value(pid, key, proc="/proc"):
    """An environment variable of a process, or None (not ours, gone, or not set)."""
    try:
        with open(os.path.join(proc, str(pid), "environ"), "rb") as f:
            raw = f.read()
    except OSError:
        return None
    pre = key.encode() + b"="
    for item in raw.split(b"\0"):
        if item.startswith(pre):
            return item[len(pre):].decode("utf-8", "replace")
    return None


def _same_prefix(a, b):
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return a == b


def wine_state(needle, procs=None, env=environ_value):
    """A Windows program under Wine needs its wineserver. When that is gone the process stays in
    the process list but has no window and does nothing ("stuck"). Only called stuck when that
    is certain: no wineserver process at all, or none for the program's own WINEPREFIX.
    -> ("none" | "running" | "stuck", [pids of the program])"""
    procs = processes() if procs is None else procs
    pids = [pid for pid, argv in procs if program(argv) == needle.lower()]
    if not pids:
        return "none", []
    servers = [pid for pid, argv in procs if base(argv[0]).startswith("wineserver")]
    if not servers:
        return "stuck", pids
    healthy = False
    for pid in pids:
        mine = env(pid, "WINEPREFIX")
        if mine is None:
            healthy = True                      # cannot tell: assume it is fine
            continue
        for s in servers:
            theirs = env(s, "WINEPREFIX")
            if theirs is None or _same_prefix(mine, theirs):
                healthy = True
    return ("running" if healthy else "stuck"), pids
