#!/usr/bin/env python3
"""scm_taps.py - add-on taps (add-on contract v1, section 3): read and check the tap files.

An add-on asks for its own copy of the telemetry with one small file in the taps folder:
    ~/.config/sc-motion/taps.d/<name>.json      (Windows %APPDATA%\\sc-motion\\taps.d)
    {"name": "<name>", "port": 61704, "enabled": true, "run": ["{python}", "-u", "/abs/path/app.py", "run"]}

  - The tee (lm_simhub4t.py) reads the folder when it starts and sends every datagram, byte for
    byte, to 127.0.0.1:<port> of each usable tap, AFTER its bridge and plugin copies.
  - The launcher (sc_start.py) starts each usable tap's "run" command after its self-test and
    stops it (Ctrl-C first) when it stops the pipeline.
  - Removing the file removes the tap at the next start.

A tap is usable when: the file is a JSON object under 64 KB, "name" is 2-32 lower-case letters,
digits, - or _ and equals the file name, "port" is inside an add-on range and is not a bridge
port, no earlier file (name order) uses the same port, "enabled" is not false, and at most
MAX_TAPS are usable. Anything else is skipped with one line saying why; it never stops the tee.

"run" is optional. It is only started when it is a list whose first item is {python} (replaced
by the launcher's own interpreter), followed by Python options (not -c or -m) and then the
absolute path of an existing .py file, then its arguments. No shell is involved.

Env: SC_BRIDGE_CONFIG_DIR (the sc-motion settings folder; the taps folder is taps.d inside it),
LM_TEE_TAPS=0 (the tee sends no add-on copies), SC_START_NO_ADDONS=1 (the launcher starts none).

  python3 scm_taps.py        list the taps and why any are skipped
"""
import json
import os
import re
import sys

# Same numbers as setup/common/scm_ports.py (tests/test_proc_ports.py checks that they agree).
# Kept here so the tee does not depend on the setup code.
RANGES = ((61704, 61708), (61720, 61739))
BRIDGE_PORTS = frozenset((61666, 61700, 61701, 61702, 61703, 61709, 61710, 30777))
MAX_TAPS = 8
MAX_FILE = 64 * 1024
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")


def config_dir():
    env = os.environ.get("SC_BRIDGE_CONFIG_DIR")
    if env:
        return env
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "sc-motion")


def taps_dir():
    return os.path.join(config_dir(), "taps.d")


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def port_ok(port):
    return _int(port) and port not in BRIDGE_PORTS and any(lo <= port <= hi for lo, hi in RANGES)


def load(folder=None):
    """-> (taps, notes). taps: [{"name", "port", "run" (list or None), "file"}] in file-name order;
    notes: one text per file that was skipped or disabled."""
    folder = folder or taps_dir()
    taps, notes, used = [], [], set()
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".json"))
    except OSError:
        return taps, notes
    for n in names:
        path = os.path.join(folder, n)
        stem = n[:-5]
        try:
            if os.path.islink(path) or not os.path.isfile(path):
                raise ValueError("not a plain file")
            if os.path.getsize(path) > MAX_FILE:
                raise ValueError("larger than %d bytes" % MAX_FILE)
            with open(path, encoding="utf-8-sig") as f:
                rec = json.load(f)
            if not isinstance(rec, dict):
                raise ValueError("not a JSON object")
            name = rec.get("name")
            if not isinstance(name, str) or not NAME_RE.match(name) or name != stem:
                raise ValueError("'name' must equal the file name (2-32 of a-z 0-9 - _)")
            if rec.get("enabled", True) is False:
                notes.append("%s: disabled (\"enabled\": false)" % n)
                continue
            port = rec.get("port")
            if not port_ok(port):
                raise ValueError("port %r is not an add-on port (%s)" % (port, ", ".join("%d-%d" % r for r in RANGES)))
            if port in used:
                raise ValueError("port %d is already used by another tap" % port)
            if len(taps) >= MAX_TAPS:
                raise ValueError("more than %d taps" % MAX_TAPS)
            run = rec.get("run")
            if run is not None and not isinstance(run, list):
                raise ValueError("'run' must be a list")
            used.add(port)
            taps.append({"name": name, "port": port, "run": run, "file": path})
        except (OSError, ValueError) as e:
            notes.append("%s: skipped (%s)" % (n, e))
    return taps, notes


def run_command(tap, python=None):
    """-> (argv, "") for a startable "run", (None, why) otherwise, (None, "") when there is none."""
    run = tap.get("run")
    if not run:
        return None, ""
    if not all(isinstance(x, str) and x and "\x00" not in x and len(x) < 1000 for x in run) or len(run) > 24:
        return None, "'run' must be a short list of texts"
    if run[0] != "{python}":
        return None, "'run' must start with {python}"
    i = 1
    while i < len(run) and run[i].startswith("-"):
        if run[i] in ("-c", "-m") or run[i].startswith(("-c", "-m")):
            return None, "'run' may not use python -c or -m"
        i += 1
    if i >= len(run):
        return None, "'run' names no script"
    script = run[i]
    if not os.path.isabs(script) or not script.endswith(".py") or not os.path.isfile(script):
        return None, "'run' script must be the absolute path of an existing .py file (%s)" % script
    return [python or sys.executable] + run[1:], ""


def main():
    taps, notes = load()
    print("taps folder: %s" % taps_dir())
    for t in taps:
        argv, why = run_command(t)
        print("  %-10s UDP %d  %s" % (t["name"], t["port"],
                                     ("starts: " + " ".join(argv[1:])) if argv else ("not started: " + why) if why else "no run command"))
    for n in notes:
        print("  " + n)
    if not taps and not notes:
        print("  none")
    if os.environ.get("LM_TEE_TAPS") == "0":
        print("  LM_TEE_TAPS=0 is set: the tee sends no add-on copies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
