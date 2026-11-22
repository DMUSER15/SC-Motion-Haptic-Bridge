#!/usr/bin/env python3
"""scm_errlog.py - error and crash logs for the SC Motion Haptic Bridge.

Every program that uses it (the launcher sc_start.py / sc_start_win.py, the installers and the
verifiers) writes a journal of its run while it works (`.running-<program>-<pid>.log` in the log
folder). When the run ends:
  - no error and no crash: the journal is deleted;
  - an error or a crash: it becomes `YYYY-MM-DD_HH-MM-SS_<program>_<crash|error>.log`, with a
    plain-English summary at the top (what happened, what it means, what to try) and the run's
    output and technical details below. Only the 5 newest logs are kept: the oldest is replaced.
    When the same problem happens again in the next run, the newest log is replaced instead (with
    a repeat count), so one recurring problem cannot push out the logs of other problems;
  - a run that never got to close (killed, power off, terminal closed) leaves its journal behind;
    the next start of any of these programs turns it into a crash log.
The launcher also watches its children (capture server, bridge): a Python traceback in their
output, or a child that stops, is recorded with the same plain-English explanation. The bridge,
tee and capture server themselves are not changed by this.

Your home folder is written as ~ in saved logs (it contains your user name), so a log can be
shared as it is. The live journal is only kept while the program runs.

Log folder: ~/.local/state/sc-motion/logs (Linux; XDG_STATE_HOME), %LOCALAPPDATA%\\sc-motion\\logs
(Windows); env SC_MOTION_ERRLOG_DIR. SC_MOTION_NO_POPUPS=1 turns desktop notifications off.

  python3 ~/scm_errlog.py list            the saved logs, newest first
  python3 ~/scm_errlog.py show [N]        print log N (1 = newest)
  python3 ~/scm_errlog.py folder          print the log folder
"""
import atexit
import collections
import datetime
import faulthandler
import io
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback

VERSION = "1.1"
WIN = os.name == "nt"
KEEP = 5                      # log files kept
JOURNAL_LIMIT = 1_500_000     # bytes of output written to the live journal
TAIL_LINES = 400              # lines of output kept in memory for the saved log
CHILD_TRACE_LINES = 200       # lines of one child traceback kept
POPUP_LIMIT = 3
LEVELS = ("warning", "error", "crash")
LOG_RE = re.compile(r"^\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d_[\w.-]+_(crash|error)(-\d+)?\.log$")
HERE = os.path.dirname(os.path.realpath(os.path.abspath(__file__)))
_current = None


def log_dir():
    env = os.environ.get("SC_MOTION_ERRLOG_DIR")
    if env:
        return env
    if WIN:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "sc-motion", "logs")
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "sc-motion", "logs")


def _cmd(linux, windows):
    return windows if WIN else linux


# ---------------------------------------------------------------- plain-English explanations

PACKAGES = {  # python module -> (what needs it, Linux (Arch/CachyOS) install, Windows install)
    "grpc": ("the capture server (it talks to the game's D-BOX connector)", "sudo pacman -S --needed python-grpcio",
             "py -3 -m pip install --user grpcio"),
    "google": ("the capture server (protobuf, the telemetry message format)", "sudo pacman -S --needed python-protobuf",
               "py -3 -m pip install --user protobuf"),
}
PORTS = {"61666": "the capture server (TLS, the game connects to it)", "61700": "the tee (telemetry in)",
         "61701": "the SC Haptics plugin in SimHub", "61702": "the bridge", "30777": "SimHub's Star Citizen game input",
         "61703": "the developer watcher", "61709": "the probe recorder"}


def _advice(title, meaning, steps, code):
    return {"title": title, "meaning": meaning, "steps": [s for s in steps if s], "code": code}


def explain(exc=None, text="", where=""):
    """-> {title, meaning, steps[], code}: what a problem means for the person at the rig."""
    msg = "%s: %s" % (type(exc).__name__, exc) if exc is not None else (text or "")
    low = msg.lower()
    cls = type(exc).__name__ if exc is not None else ""
    start = _cmd("python3 ~/sc_start.py", "py -3 %USERPROFILE%\\sc-motion\\sc_start_win.py")
    stop = _cmd("python3 ~/sc_start.py stop", "py -3 %USERPROFILE%\\sc-motion\\sc_start_win.py stop")
    verify = _cmd("python3 setup/linux/sc_motion_verify.py", "py -3 setup\\windows\\sc_verify_win.py")
    plan = _cmd("python3 setup/linux/sc_motion_install.py plan", "py -3 setup\\windows\\sc_install_win.py plan")
    cert = _cmd("python3 ~/sc_lmcert.py", "py -3 %USERPROFILE%\\sc-motion\\sc_cert_win.py lmcert")
    if isinstance(exc, ImportError) or "no module named" in low:
        mod = (getattr(exc, "name", None) or (re.findall(r"no module named '([\w.]+)'", low) or [""])[0]).split(".")[0]
        need, lin, win = PACKAGES.get(mod, ("this part of the bridge", "", ""))
        inst = win if WIN else lin
        return _advice("A Python module is missing: %s" % (mod or "?"),
                       "The bridge needs %s for %s. It is not installed for the Python that runs it (%s)." % (
                           mod, need, sys.executable),
                       ["Install it: %s" % inst if inst else "Install the Python module %s." % mod,
                        "Or run `%s`: it lists every missing piece and the command." % plan,
                        "Then start again."], "module:" + mod)
    if "recorder or old bridge is running" in low:
        return _advice("A recorder or an old bridge is still running",
                       "A probe recorder, replay tool or an old bridge script holds the telemetry ports, so the "
                       "launcher will not start a second pipeline next to it.",
                       ["Stop the program listed above this message (Ctrl-C in its terminal, or end it).",
                        "Then start again."], "blocker")
    port = re.search(r"\b(61666|6170\d|30777)\b", msg)
    if ("address already in use" in low or "errno 98" in low or "winerror 10048" in low or "still in use" in low
            or "is in use by" in low):
        p = port.group(1) if port else ""
        return _advice("A port the bridge needs is already in use" + (" (%s)" % p if p else ""),
                       "Another program is already using %s, usually a bridge or capture server left over from an "
                       "earlier start%s." % ("port %s (%s)" % (p, PORTS.get(p, "?")) if p else "one of the bridge's ports",
                                              ", or D-BOX's Motion Engine on 61666" if WIN else ""),
                       ["Run `%s`, then start again." % stop,
                        "Still busy? Find the owner: %s" % _cmd("ss -lntup | grep -E '61666|617|30777'",
                                                                 "netstat -ano | findstr \"61666 6170 30777\""),
                        "Windows: if it is D-BOX's Motion Engine, set that service to Manual (docs/INSTALL-WINDOWS.md, 'Port 61666')."
                        if WIN else ""], "port-in-use:" + p)
    if "certificate" in low or "lmcert" in low or "selftest fail" in low or "livemotion.crt" in low:
        return _advice("The game's connection certificate is not right",
                       "Star Citizen's D-BOX connector only talks to a server whose certificate it trusts. A game or "
                       "D-BOX update usually puts D-BOX's own certificate back, or deletes a copy, so the game will not "
                       "send motion telemetry.",
                       ["Close the game.", "Run `%s repair`%s." % (cert, " in an administrator PowerShell" if WIN else ""),
                        "Then `%s check` should say LMCERT PASS, and you can start again." % cert,
                        "`%s status` lists every copy and what it holds." % cert], "certificate")
    if "simhub game definition" in low or "simdef" in low or "unsupported field" in low:
        return _advice("SimHub's Star Citizen game definition cannot be used",
                       "The bridge builds its packets from the game definition SimHub has registered (Star Citizen.simdef). "
                       "It is missing, unreadable, or has a field whose packet type the bridge does not know "
                       "(fields it knows but does not fill are sent as 0 and only give a warning).",
                       ["Run `%s`: the simdef step says what is wrong." % plan,
                        "Run the installer's apply (SimHub closed); it puts the repository's definition back and renames the old one.",
                        "If you edited the definition in SimHub, undo that change."], "simdef")
    if "simhub did not appear" in low or "simhub not found" in low:
        return _advice("SimHub did not start",
                       "The launcher started SimHub but it never showed up as running within 60 seconds%s." % (
                           "" if WIN else " (it runs under Wine)"),
                       ["Look at the SimHub start log named in the details.",
                        "Start SimHub by hand once; answer its first-start questions, then close it and start again.",
                        "" if WIN else "Check that the SimHub Wine prefix and wine binary in the installer plan still exist."],
                       "simhub-start")
    if "self-test failed" in low:
        return _advice("The bridge self-test failed",
                       "The launcher sent zero frames through the bridge and did not get the expected SimHub packet back. "
                       "SimHub was not running, so nothing reached the rig.",
                       ["Read the bridge log named above (bridge.log in the launcher log folder).",
                        "Run `%s` for the full picture." % verify,
                        "If you changed the simdef or bridge files by hand, put the repository's versions back."], "selftest")
    if "exited during start-up" in low or "did not become ready" in low:
        which = "capture server" if "capture" in low else "bridge" if "bridge" in low else "program"
        return _advice("The %s did not start" % which,
                       "The %s stopped or never got ready. Its own log (named in the details) shows why; the first "
                       "error there is the cause." % which,
                       ["Read the log named in the details (the last lines are printed above).",
                        "Run `%s`." % verify, "Then start again."], "child-start:" + which)
    if "has stopped" in low and ("bridge" in low or "capture server" in low):
        which = "capture server" if "capture" in low else "bridge"
        return _advice("The %s stopped during play" % which,
                       "Motion output stopped while the game was running. The game keeps running; the rig gets no more data.",
                       ["Start the launcher again (`%s`); it restarts the pipeline without restarting the game." % start,
                        "The %s's last lines are in this log; keep it if it happens again." % which], "child-stopped:" + which)
    if "traceback in" in low and ("bridge" in low or "capture server" in low or "tee" in low):
        return _advice("The %s hit a program error" % ("capture server" if "capture" in low else "bridge"),
                       "A Python error was printed by one of the bridge's own processes. It may have stopped, or it may "
                       "have skipped one telemetry message and carried on.",
                       ["Keep this log; the technical details show the exact place.",
                        "If motion stopped, start the launcher again.",
                        "If it happens every time, run `%s` and send this log with a note of what you were doing." % verify],
                       "child-traceback")
    if "rig pause needs" in low or "needs a terminal" in low or "no terminal to ask" in low:
        return _advice("The launcher needs a terminal window",
                       "It asks you to confirm the rig check (and certificate questions) with the keyboard, but it was "
                       "started without a terminal.",
                       ["Start it from the app menu entry (it opens a terminal) or from a terminal window."], "no-terminal")
    if "rsi launcher not found" in low or ("missing" in low and "sc-launch.sh" in low):
        return _advice("Star Citizen's launcher was not found",
                       "The launcher starts the game through %s, and that file is not where the settings say." % (
                           "the RSI Launcher" if WIN else "LUG-Helper's sc-launch.sh in the game prefix"),
                       ["Run `%s`: the game step shows which folder is used." % plan,
                        "If you moved or reinstalled the game, run the installer's apply again."], "game-launcher")
    if "openssl" in low and ("not found" in low or "no such file" in low):
        return _advice("openssl is not installed",
                       "The certificate tools use openssl to check and make certificates.",
                       ["Install it: %s" % _cmd("sudo pacman -S --needed openssl",
                                                "winget install -e --id ShiningLight.OpenSSL.Light (then open a new PowerShell)")],
                       "openssl")
    if cls == "JSONDecodeError" or "expecting value" in low or "expecting ',' delimiter" in low:
        return _advice("A settings file is damaged",
                       "One of the bridge's JSON files cannot be read (a missing comma or bracket, or the file was cut off). "
                       "The line and column in the details show where.",
                       ["The settings are in %s (config.json) and your effects in sc_haptics/effects.json." % _cmd(
                           "~/.config/sc-motion", "%APPDATA%\\sc-motion"),
                        "Fix the place named in the details, or run the installer's apply to write config.json again."], "json")
    if isinstance(exc, PermissionError) or "permission denied" in low or "no permission" in low:
        return _advice("No permission to use a file or folder",
                       "The program was not allowed to write or open something it needs (named in the details).",
                       ["Windows: certificate repairs and the installer write under C:\\ProgramData\\D-BOX; run them in an "
                        "administrator PowerShell." if WIN else "Check that the file belongs to you (`ls -l <path>`).",
                        "" if WIN else "Never run the bridge, launcher or installer with sudo."], "permission")
    if isinstance(exc, FileNotFoundError) or "no such file" in low or low.startswith("missing "):
        return _advice("A file the bridge needs is missing",
                       "A file named in the settings or installed by the installer does not exist (see details).",
                       ["Run `%s`: steps that are not DONE say what is missing." % plan,
                        "Run the installer's apply to put the files back."], "missing-file")
    if isinstance(exc, MemoryError):
        return _advice("The computer ran out of memory", "The program could not get the memory it needed.",
                       ["Close other programs and start again."], "memory")
    if "ended without closing" in low:
        hard = "fatal python error" in low or "segmentation fault" in low
        return _advice("The program crashed inside a library" if hard else "The program was stopped without closing",
                       ("A library crashed the program; the details show where." if hard else
                        "It ended without its normal shutdown: the terminal window was closed, it was killed, the PC "
                        "lost power, or the session ended. A capture server or bridge it started may still be running."),
                       ["Run `%s` once to stop anything left over." % stop, "Start again; nothing else needs cleaning up."],
                       "hard-crash" if hard else "killed")
    if "ended with exit code" in low or "exit code" in low:
        return _advice("The program stopped with an error code",
                       "It ended early. The output section of this log shows the last messages before it stopped; the "
                       "first message there that mentions a problem is the cause.",
                       ["Read the last lines of the output section below.", "Run `%s`." % verify], "exit-code")
    return _advice("Unexpected error" + (" in %s" % where if where else ""),
                   "Something went wrong that the bridge does not have a specific explanation for. The details below "
                   "show exactly where, which is what is needed to fix it.",
                   ["Start again; if it happens again, keep this log file and send it with a note of what you were doing.",
                    "Run `%s` to rule out a settings or installation problem." % verify], "unexpected:" + cls)


def where_of(tb):
    """The last place in the bridge's own code in a traceback: 'sc_start.py line 120 (main)'."""
    best = ""
    for fr in traceback.extract_tb(tb) if tb else []:
        fn = os.path.realpath(os.path.abspath(fr.filename))
        if os.path.basename(fn).startswith(("sc_", "scm_", "lm_", "SC_", "win")):
            best = "%s line %d (%s)" % (os.path.basename(fn), fr.lineno, fr.name)
    return best


def format_box(advice, level, detail="", log_hint=""):
    head = {"crash": "SC MOTION CRASH", "error": "SC MOTION ERROR", "warning": "SC MOTION WARNING"}[level]
    lines = ["", "=" * 16 + " " + head + " " + "=" * 16, advice["title"], "", advice["meaning"], ""]
    if advice["steps"]:
        lines.append("What to try:")
        lines += ["  %d. %s" % (i + 1, s) for i, s in enumerate(advice["steps"])]
    if detail:
        lines += ["", "Details: " + detail]
    if log_hint:
        lines += [log_hint]
    lines.append("=" * (len(head) + 34))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- the run journal

class _Tee(io.TextIOBase):
    """Writes to the original stream and into the run log."""

    def __init__(self, orig, runlog):
        self.orig = orig
        self.runlog = runlog

    def write(self, text):
        try:
            if self.orig is not None:
                self.orig.write(text)
        except Exception:  # noqa: BLE001  a closed terminal must not stop the program
            pass
        self.runlog.output(text)
        return len(text)

    def flush(self):
        try:
            if self.orig is not None:
                self.orig.flush()
        except Exception:  # noqa: BLE001
            pass

    def isatty(self):
        try:
            return bool(self.orig is not None and self.orig.isatty())
        except Exception:  # noqa: BLE001
            return False

    def fileno(self):
        return self.orig.fileno()

    @property
    def encoding(self):
        return getattr(self.orig, "encoding", "utf-8")


class RunLog:
    def __init__(self, program, argv=None, folder=None, notify=None, clock=time.time):
        self.program = re.sub(r"[^\w.-]+", "-", program).strip("-") or "bridge"
        self.argv = list(argv if argv is not None else sys.argv)
        self.folder = folder or log_dir()
        self.clock = clock
        self.started = clock()
        self.lock = threading.RLock()
        self.tail = collections.deque(maxlen=TAIL_LINES)
        self.partial = ""
        self.problems = collections.OrderedDict()   # signature -> record
        self.written = 0
        self.journal = None
        self.journal_path = None
        self.saved_path = None
        self.finished = False
        self.popups = 0
        self.notify = notify if notify is not None else _notify
        self.children = {}           # child name -> {"trace": [lines] or None}
        self.orig = (sys.stdout, sys.stderr, sys.excepthook, getattr(threading, "excepthook", None))
        self.installed = False

    # ---- set up
    def header(self):
        return ["SC Motion Haptic Bridge (scm_errlog %s) - %s" % (VERSION, self.program),
                "started    : %s" % datetime.datetime.fromtimestamp(self.started).strftime("%Y-%m-%d %H:%M:%S"),
                "command    : %s" % " ".join(self.argv),
                "python     : %s %s" % (sys.executable, platform.python_version()),
                "system     : %s" % platform.platform(),
                "desktop    : %s / %s" % (os.environ.get("XDG_CURRENT_DESKTOP", "-"), os.environ.get("XDG_SESSION_TYPE", "-")),
                "pid        : %d" % os.getpid()]

    def install(self, capture=True):
        """Start the journal, catch uncaught exceptions (all threads) and hard crashes."""
        os.makedirs(self.folder, exist_ok=True)
        adopt_orphans(self.folder, exclude_pid=os.getpid())
        self.journal_path = os.path.join(self.folder, ".running-%s-%d.log" % (self.program, os.getpid()))
        self.journal = open(self.journal_path, "w", encoding="utf-8", errors="replace", buffering=1)
        self.journal.write("\n".join(self.header()) + "\n\n--- output ---\n")
        try:
            faulthandler.enable(file=self.journal, all_threads=True)
        except (ValueError, OSError, io.UnsupportedOperation, AttributeError):
            pass
        if capture:
            sys.stdout = _Tee(sys.stdout, self)
            sys.stderr = _Tee(sys.stderr, self)
        sys.excepthook = self._excepthook
        if hasattr(threading, "excepthook"):
            threading.excepthook = self._thread_hook
        atexit.register(self.finish)
        self.installed = True
        return self

    def uninstall(self):
        if not self.installed:
            return
        sys.stdout, sys.stderr, sys.excepthook = self.orig[0], self.orig[1], self.orig[2]
        if self.orig[3] is not None:
            threading.excepthook = self.orig[3]
        try:
            faulthandler.disable()
        except Exception:  # noqa: BLE001
            pass
        try:
            atexit.unregister(self.finish)
        except Exception:  # noqa: BLE001
            pass
        self.installed = False

    # ---- output
    def output(self, text):
        with self.lock:
            stamp = time.strftime("%H:%M:%S ")
            self.partial += text
            *done, self.partial = self.partial.split("\n")
            if len(self.partial) > 20000:            # a line without end must not grow forever
                done.append(self.partial)
                self.partial = ""
            for line in done:
                self.tail.append(stamp + line)
                if self.journal and self.written < JOURNAL_LIMIT:
                    self.written += len(line) + 10
                    try:
                        self.journal.write(stamp + line + "\n")
                        if self.written >= JOURNAL_LIMIT:
                            self.journal.write("[journal limit reached; the last %d lines are kept in memory]\n" % TAIL_LINES)
                    except (OSError, ValueError):
                        pass

    # ---- children (capture server, bridge): called for every line of their output
    def child_line(self, name, line):
        """Spot a Python traceback in a child's output and record it (once per distinct error)."""
        line = line.rstrip("\r\n")
        with self.lock:
            st = self.children.setdefault(name, {"trace": None})
            if line.startswith("Traceback (most recent call last):"):
                st["trace"] = [line]
                return
            tr = st["trace"]
            if tr is None:
                return
            if len(tr) >= CHILD_TRACE_LINES:
                st["trace"] = None
                return
            tr.append(line)
            if line and not line[0].isspace() and not line.startswith(("During handling", "The above exception")):
                st["trace"] = None
                if line.split(":")[0].strip() in ("KeyboardInterrupt", "SystemExit"):
                    return            # the normal way a child is stopped (Ctrl-C from the launcher): not an error
                last = [x for x in tr if x.startswith('  File "')]
                where = ""
                if last:
                    m = re.match(r'  File "(.*)", line (\d+), in (.*)', last[-1])
                    if m:
                        where = "%s: %s line %s (%s)" % (name, os.path.basename(m.group(1)), m.group(2), m.group(3))
                self.report("error", text="Traceback in the %s: %s" % (name, line), where=where or name,
                            trace="\n".join(tr) + "\n")
            elif line == "" and len(tr) > 1 and not tr[-2].startswith(("  ", "Traceback")):
                st["trace"] = None

    def child_exit(self, name, code, logpath=None, during="play"):
        """A child stopped on its own. -> advice."""
        sig = ""
        if code is not None and code < 0:
            sig = " (killed by signal %d)" % -code
        tail = _tail(logpath, 30) if logpath else ""
        text = ("The %s has stopped during %s with exit code %s%s." % (name, during, code, sig))
        return self.report("error", text=text, where=name, trace=("last lines of %s:\n%s" % (logpath, tail)) if tail else "")

    # ---- problems
    def report(self, level="error", exc=None, text="", where="", tb=None, quiet=False, trace=""):
        """Record a problem and show it in plain English. -> the advice dict."""
        if level not in LEVELS:
            level = "error"
        tb = tb if tb is not None else (exc.__traceback__ if exc is not None else None)
        where = where or where_of(tb)
        advice = explain(exc, text, where)
        detail = ("%s: %s" % (type(exc).__name__, exc) if exc is not None else text).strip()
        if where:
            detail += "  [%s]" % where
        sig = (advice["code"], where, detail[:200])
        now = self.clock()
        with self.lock:
            rec = self.problems.get(sig)
            first = rec is None
            if first:
                if not trace and exc is not None and tb:
                    trace = "".join(traceback.format_exception(type(exc), exc, tb))
                rec = self.problems[sig] = {"level": level, "advice": advice, "detail": detail, "count": 0,
                                            "first": now, "last": now, "trace": trace}
            rec["count"] += 1
            rec["last"] = now
            if LEVELS.index(level) > LEVELS.index(rec["level"]):
                rec["level"] = level
        if first and not quiet:
            hint = ("A log of this run will be saved in %s when this program closes." % self.folder) if level != "warning" else ""
            box = format_box(advice, level, detail, hint)
            target = self.orig[1] if self.orig[1] is not None else sys.__stderr__
            try:
                target.write(box)
                target.flush()
            except Exception:  # noqa: BLE001
                pass
            self.output(box)
            if rec["trace"] and self.journal:
                self.output(rec["trace"])
            if level != "warning":
                self._popup(level, advice)
        return advice

    def _popup(self, level, advice):
        if self.popups >= POPUP_LIMIT or os.environ.get("SC_MOTION_NO_POPUPS") == "1":
            return
        try:
            tty = self.orig[1] is not None and self.orig[1].isatty()
        except Exception:  # noqa: BLE001
            tty = False
        if tty:
            return
        self.popups += 1
        self.notify("SC Motion: " + advice["title"], advice["meaning"] + ("\nTry: " + advice["steps"][0] if advice["steps"] else ""))

    def _excepthook(self, etype, value, tb):
        if issubclass(etype, KeyboardInterrupt):
            return self.orig[2](etype, value, tb)
        self.report("crash", value, tb=tb)

    def _thread_hook(self, args):
        if args.exc_type is SystemExit:
            return
        self.report("crash", args.exc_value, tb=args.exc_traceback,
                    where="thread %s: %s" % (getattr(args.thread, "name", "?"), where_of(args.exc_traceback)))

    def worst(self):
        with self.lock:
            levels = [r["level"] for r in self.problems.values()]
        return max(levels, key=LEVELS.index) if levels else None

    # ---- end of run
    def render(self, exit_code=None, orphan_text=None):
        worst = self.worst() or "error"
        out = self.header()
        ended = datetime.datetime.fromtimestamp(self.clock()).strftime("%Y-%m-%d %H:%M:%S")
        out += ["ended      : %s (exit code %s)" % (ended, exit_code), "result     : %s" % worst.upper(), ""]
        out.append("=== WHAT HAPPENED (plain English) ===")
        with self.lock:
            recs = list(self.problems.values())
        recs.sort(key=lambda r: -LEVELS.index(r["level"]))
        for n, r in enumerate(recs, 1):
            a = r["advice"]
            out += ["", "%d. [%s] %s%s" % (n, r["level"].upper(), a["title"], (" (x%d)" % r["count"]) if r["count"] > 1 else ""),
                    "   " + a["meaning"]]
            if a["steps"]:
                out.append("   What to try:")
                out += ["     - " + s for s in a["steps"]]
            out.append("   Details: " + r["detail"])
            out.append("   First seen %s, last seen %s" % (time.strftime("%H:%M:%S", time.localtime(r["first"])),
                                                         time.strftime("%H:%M:%S", time.localtime(r["last"]))))
        traces = [r["trace"] for r in recs if r["trace"]]
        if traces:
            out += ["", "=== TECHNICAL DETAILS ==="] + traces
        out += ["", "=== OUTPUT OF THIS RUN (last %d lines) ===" % TAIL_LINES]
        with self.lock:
            out += list(self.tail) + ([self.partial] if self.partial else [])
        if orphan_text:
            out += ["", "=== JOURNAL ===", orphan_text]
        return "\n".join(out) + "\n"

    def signature(self):
        with self.lock:
            return ",".join(sorted({r["advice"]["code"] + "@" + r["detail"][:80]
                                    for r in self.problems.values() if r["level"] != "warning"}))

    def finish(self, exit_code=None):
        """End of run: save a log if anything went wrong, else drop the journal. Safe to call twice."""
        if self.finished:
            return self.saved_path
        self.finished = True
        if exit_code not in (None, 0) and not any(r["level"] != "warning" for r in self.problems.values()):
            self.report("error", text="The program ended with exit code %s (see the output above for the reason)." % exit_code,
                        quiet=True)
        try:
            faulthandler.disable()
        except Exception:  # noqa: BLE001
            pass
        bad = any(r["level"] != "warning" for r in self.problems.values())
        if bad:
            try:
                self.saved_path = save_log(self.folder, self.program, self.worst(), self.render(exit_code),
                                           self.signature(), self.clock())
                msg = "SC Motion: a log of this run's problems was saved: %s\n" % redact(self.saved_path)
                try:
                    (self.orig[1] or sys.__stderr__).write(msg)
                except Exception:  # noqa: BLE001
                    pass
            except OSError as exc:
                try:
                    (self.orig[1] or sys.__stderr__).write("SC Motion: could not save the error log (%s)\n" % exc)
                except Exception:  # noqa: BLE001
                    pass
        try:
            if self.journal:
                self.journal.close()
            if self.journal_path and os.path.exists(self.journal_path):
                os.remove(self.journal_path)
        except OSError:
            pass
        self.uninstall()
        return self.saved_path


# ---------------------------------------------------------------- the log folder

def _tail(path, n):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return "".join(collections.deque(fh, maxlen=n))
    except OSError:
        return ""


def list_logs(folder=None):
    """-> [(path, mtime)] newest first."""
    folder = folder or log_dir()
    try:
        names = [n for n in os.listdir(folder) if LOG_RE.match(n)]
    except OSError:
        return []
    out = []
    for n in names:
        p = os.path.join(folder, n)
        try:
            out.append((p, os.path.getmtime(p)))
        except OSError:
            pass
    return sorted(out, key=lambda x: (x[1], x[0]), reverse=True)


SIG_LINE = "signature  : "
REPEAT_LINE = "repeats    : "


def _read_head(path, lines=16):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return [fh.readline().rstrip("\n") for _ in range(lines)]
    except OSError:
        return []


def _homes():
    out = set()
    for h in (os.path.expanduser("~"), os.environ.get("USERPROFILE"), os.environ.get("HOME")):
        if h and len(h.rstrip("/\\")) >= 3:
            h = h.rstrip("/\\")
            out.update({h, h.replace("\\", "/"), h.replace("/", "\\"), os.path.realpath(h)})
    return sorted(out, key=len, reverse=True)


def redact(text):
    """Home folders (their names are the user name) -> ~, so a log can be shared as it is.
    Also the user name on its own where it follows /home/, /Users/ or C:\\Users\\."""
    if not text:
        return text
    flags = re.I if WIN else 0
    for h in _homes():
        text = re.sub(r"(?<![A-Za-z0-9_.-])" + re.escape(h) + r"(?![A-Za-z0-9_.-])", "~", text, flags=flags)
    user = os.environ.get("USERNAME") or os.environ.get("USER") or os.path.basename(os.path.expanduser("~"))
    if user and len(user) >= 2:
        text = re.sub(r"(?i)([/\\](?:home|users)[/\\])" + re.escape(user) + r"(?![A-Za-z0-9_.-])", r"\1<user>", text)
    return text


def save_log(folder, program, level, text, signature="", when=None):
    """Write a log file; keep the KEEP newest; a repeat of the newest log's problem replaces it."""
    os.makedirs(folder, exist_ok=True)
    when = when or time.time()
    signature = redact(signature)
    repeats = 1
    logs = list_logs(folder)
    if logs and signature:
        head = _read_head(logs[0][0])
        old_sig = next((h[len(SIG_LINE):] for h in head if h.startswith(SIG_LINE)), None)
        if old_sig == signature and ("_%s_" % program) in os.path.basename(logs[0][0]):
            repeats = 1 + next((int(h[len(REPEAT_LINE):].split()[0]) for h in head if h.startswith(REPEAT_LINE)
                                and h[len(REPEAT_LINE):].split()[0].isdigit()), 1)
            try:
                os.remove(logs[0][0])
            except OSError:
                pass
            logs = logs[1:]
    name = "%s_%s_%s.log" % (time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(when)), program,
                             level if level == "crash" else "error")
    path = os.path.join(folder, name)
    n = 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(folder, name.replace(".log", "-%d.log" % n))
    text = redact(text)
    lines = text.split("\n")
    extra = [SIG_LINE + signature, REPEAT_LINE + ("%d (the same problem in %d runs in a row; this is the latest)" % (
        repeats, repeats) if repeats > 1 else "1")]
    text = "\n".join(lines[:1] + extra + lines[1:])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    for old, _t in list_logs(folder)[KEEP:]:
        try:
            os.remove(old)
        except OSError:
            pass
    return path


def _pid_alive(pid):
    if pid <= 0:
        return False
    if WIN:
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259    # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def adopt_orphans(folder=None, exclude_pid=None):
    """Journals of runs that never closed -> crash logs. -> list of saved paths."""
    folder = folder or log_dir()
    saved = []
    try:
        names = os.listdir(folder)
    except OSError:
        return saved
    for n in sorted(names):
        m = re.match(r"^\.running-([\w.-]+)-(\d+)\.log$", n)
        if not m:
            continue
        pid = int(m.group(2))
        if pid == exclude_pid or _pid_alive(pid):
            continue
        path = os.path.join(folder, n)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                journal = fh.read()
            when = os.path.getmtime(path)
        except OSError:
            continue
        rl = RunLog(m.group(1), argv=[], folder=folder, notify=lambda *_a: None, clock=lambda w=when: w)
        rl.started = when
        hard = "Fatal Python error" in journal or "Segmentation fault" in journal
        rl.report("crash", text="The %s run (pid %d) ended without closing.%s" % (
            m.group(1), pid, " Fatal Python error (faulthandler): see the journal." if hard else ""), quiet=True)
        rl.header = lambda prog=m.group(1), t=when: [
            "SC Motion Haptic Bridge (scm_errlog %s) - %s (a run that never closed, found at the next start)" % (VERSION, prog),
            "last written: %s (the run's own details are in the JOURNAL section)" % datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")]
        text = rl.render("unknown", orphan_text=journal[-200_000:])
        try:
            saved.append(save_log(folder, m.group(1), "crash", text, rl.signature(), when))
            os.remove(path)
        except OSError:
            continue
    return saved


def _notify(title, body):
    """A desktop notification (no terminal to show the message in). Never blocks, never raises."""
    try:
        if WIN:
            import ctypes

            def box():
                ctypes.windll.user32.MessageBoxW(None, body, title, 0x30 | 0x40000)   # warning icon, topmost
            threading.Thread(target=box, daemon=True).start()
        elif shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "-a", "SC Motion", "-u", "critical", "-i", "dialog-warning", title, body],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- module-level helpers

def start(program, argv=None, capture=True, **kw):
    """Start logging this run (once per process). -> the RunLog. Never raises: if the log folder
    cannot be used, the program runs without a log."""
    global _current
    if _current is None:
        try:
            _current = RunLog(program, argv, **kw).install(capture=capture)
        except OSError as exc:
            sys.stderr.write("SC Motion: error log not available (%s)\n" % exc)
            return None
    return _current


def current():
    return _current


def report(level="error", exc=None, text="", where="", quiet=False, trace=""):
    """Report a problem through the current run log (or just print it when logging is not running)."""
    if _current is not None:
        return _current.report(level, exc, text, where, quiet=quiet, trace=trace)
    advice = explain(exc, text, where)
    if not quiet:
        sys.stderr.write(format_box(advice, level if level in LEVELS else "error",
                                    ("%s: %s" % (type(exc).__name__, exc)) if exc is not None else text))
    return advice


def child_line(name, line):
    if _current is not None:
        _current.child_line(name, line)


def child_exit(name, code, logpath=None, during="play"):
    if _current is not None:
        return _current.child_exit(name, code, logpath, during)
    return None


def finish(exit_code=None):
    global _current
    rl, _current = _current, None
    return rl.finish(exit_code) if rl is not None else None


def log_title(path):
    """The first problem's title in a saved log (for lists)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = re.match(r"^1\. \[(\w+)\] (.*)$", line.strip())
                if m:
                    return "%s: %s" % (m.group(1).lower(), m.group(2))
    except OSError:
        pass
    return "?"


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "list"
    folder = log_dir()
    if cmd == "folder":
        print(folder)
        return 0
    adopt_orphans(folder)
    logs = list_logs(folder)
    if cmd == "list":
        print("SC Motion error logs in %s (%d of at most %d kept):" % (redact(folder), len(logs), KEEP))
        for i, (p, t) in enumerate(logs, 1):
            print("  %d. %s  %s" % (i, os.path.basename(p), log_title(p)))
        if not logs:
            print("  none (no run had an error)")
        return 0
    if cmd == "show":
        try:
            n = int(argv[2]) if len(argv) > 2 else 1
        except ValueError:
            n = 0
        if not 1 <= n <= len(logs):
            print("no log %s (there are %d)" % (argv[2] if len(argv) > 2 else 1, len(logs)))
            return 3
        with open(logs[n - 1][0], "r", encoding="utf-8", errors="replace") as fh:
            sys.stdout.write(fh.read())
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
