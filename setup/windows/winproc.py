"""winproc.py - Windows stand-ins for the Linux /proc process and socket checks used by
sc_start.py, sc_certswap.py and sc_lmcert.py, so those scripts run unchanged on Windows.

  scan(*needles)        [(pid, cmdline)] whose program is one of needles (lower-case names;
                        an .exe by its image name, a Python script by the script's file name)
  procs_with_arg(name)  [pid strings] whose command line has an argument with that file name
                        (sc_certswap.py semantics, used for StarCitizen.exe / SC_Motion_Server.py)
  listening(proto, p)   text describing local sockets bound to port p ("" if none);
                        TCP: listening sockets only
  owner(proto, p)       "name (pid)" of the process bound to that port, or ""
  kill(pid)             end a process (taskkill /T /F)

Windows data comes from PowerShell (Get-CimInstance Win32_Process) and `netstat -ano`.
On Linux (tests only) the same functions read /proc, so the Windows launcher and verifier can
be exercised against stand-ins.
"""
import json, os, re, subprocess

IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW


def _run(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=NO_WINDOW, errors="replace")
        return r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def split_cmdline(s):
    """Split a Windows command line into arguments (quotes handled; good enough for matching)."""
    return [a or b for a, b in re.findall(r'"([^"]*)"|(\S+)', s or "")]


def _base(arg):
    return arg.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()


def program(argv):
    """Same rule as sc_start._program: the exe, or for python [-opts] script.py the script."""
    if not argv:
        return ""
    first = _base(argv[0])
    if first.startswith(("python", "py.exe", "pythonw")) or first in ("py",):
        for a in argv[1:]:
            if not a.startswith("-"):
                return _base(a)
    return first


def processes():
    """[(pid, image name, argv list)]"""
    if not IS_WIN:
        out = []
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            try:
                with open("/proc/%s/cmdline" % d, "rb") as f:
                    argv = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
            except OSError:
                continue
            if argv:
                out.append((int(d), _base(argv[0]), argv))
        return out
    txt = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                "Get-CimInstance Win32_Process | Select-Object ProcessId,Name,CommandLine | "
                "ConvertTo-Json -Compress"])
    try:
        data = json.loads(txt) if txt.strip() else []
    except ValueError:
        data = []
    if isinstance(data, dict):
        data = [data]
    out = []
    for p in data:
        argv = split_cmdline(p.get("CommandLine") or "") or [p.get("Name") or ""]
        out.append((int(p.get("ProcessId") or 0), (p.get("Name") or "").lower(), argv))
    if not out:  # PowerShell blocked: image names only
        for line in _run(["tasklist", "/FO", "CSV", "/NH"]).splitlines():
            cols = [c.strip('"') for c in line.split('","')]
            if len(cols) >= 2 and cols[1].isdigit():
                out.append((int(cols[1]), cols[0].lower(), [cols[0]]))
    return out


def scan(*needles):
    me = os.getpid()
    hits = []
    for pid, name, argv in processes():
        if pid == me:
            continue
        prog = program(argv)
        if prog in needles or (name in needles and not prog.endswith(".py")):
            hits.append((pid, " ".join(argv)))
    return hits


def procs_with_arg(name):
    name = name.lower()
    me = os.getpid()
    return [str(pid) for pid, _, argv in processes()
            if pid != me and any(_base(a) == name for a in argv)]


def _netstat(proto):
    """[(local address, port, state, pid)]"""
    if not IS_WIN:
        rows = []
        for fam in ("", "6"):
            try:
                with open("/proc/net/%s%s" % (proto, fam)) as f:
                    lines = f.readlines()[1:]
            except OSError:
                continue
            for line in lines:
                parts = line.split()
                if len(parts) < 4:
                    continue
                port = int(parts[1].rsplit(":", 1)[1], 16)
                state = "LISTENING" if (proto == "tcp" and parts[3] == "0A") else ("" if proto == "udp" else parts[3])
                rows.append((parts[1], port, state, 0))
        return rows
    return parse_netstat(_run(["netstat", "-ano"]), proto)


def parse_netstat(text, proto):
    """Rows of `netstat -ano` (IPv4 and IPv6). A TCP socket counts as listening when its foreign
    address is 0.0.0.0:0 / [::]:0 (the state word is translated on non-English Windows)."""
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0].upper() != proto.upper():
            continue
        try:
            local = parts[1]
            port = int(local.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            continue
        pid = int(parts[-1]) if parts[-1].isdigit() else 0
        if proto == "tcp":
            listen = parts[2] in ("0.0.0.0:0", "[::]:0", "*:*")
            rows.append((local, port, "LISTENING" if listen else "", pid))
        else:
            rows.append((local, port, "", pid))
    return rows


def listening(proto, port):
    out = []
    for local, p, state, pid in _netstat(proto):
        if p != port:
            continue
        if proto == "tcp" and state != "LISTENING":
            continue
        out.append("%s port %d bound (%s%s)" % (proto, port, local, ", pid %d" % pid if pid else ""))
    return "\n".join(out)


def owner(proto, port):
    for local, p, state, pid in _netstat(proto):
        if p == port and (proto == "udp" or state == "LISTENING") and pid:
            for q, name, argv in processes():
                if q == pid:
                    return "%s (pid %d)" % (program(argv) or name, pid)
            return "pid %d" % pid
    return ""


def kill(pid):
    if IS_WIN:
        _run(["taskkill", "/PID", str(pid), "/T", "/F"])
    else:
        import signal
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
