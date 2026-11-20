#!/usr/bin/env python3
"""sc_start.py - start the Star Citizen motion pipeline, check it, then start the game.

Run from a terminal (it asks you to press Enter once):
    python3 ~/sc_start.py

Order:
  0. Update check (setup/common/scm_update.py): asks GitHub whether a newer release of
     SC-Motion-Haptic-Bridge exists. Up to date, offline or a developer checkout: one line and
     on to step 1. A newer release: pause, list the files that would change, and offer to
     update (only those files; your effects.json, SimHub's settings and your certificates are
     never touched). If the update changes anything that shapes platform movement, a warning
     says so, asks you to type YES, and is repeated at the rig pause. SC_UPDATE_CHECK=0 skips it.
  1. Refuse to start if a recorder (lmrec.py), an old bridge (lm_bridge.py, lmcapture.py), a
     replay tool (lm_replay.py, lm_replay2.py) or the haptics test feed (sc_feedtest.py) is
     running. Those are never stopped automatically.
  1b. Certificate pre-flight (2026-10-18): python3 ~/sc_lmcert.py check (read-only).
     PASS continues; WARN (expires within 30 days) continues with a message; FAIL (game copy of
     livemotion.crt changed by a patch, key/cert mismatch, expired) asks: abort, or continue
     with a warning that motion telemetry may be disabled. Skipped with a warning if
     sc_lmcert.py is missing.
  1c. Bridge layout (2026-10-23): the bridge lm_simhub4t.py packs the fields of SimHub's
     registered game definition ("Star Citizen.simdef"); if that file cannot be read or has a
     field the bridge does not support, stop here with the reason.
  2. Stop a capture server or bridge (lm_simhub.py ... lm_simhub3t.py, lm_simhub4.py,
     lm_simhub4t.py) left over from an earlier run (crash leftovers).
  3. Start the capture server (SC_Motion_Server.py) and wait until TCP 127.0.0.1:61666 is listening.
  3b. TLS self-test: python3 ~/sc_lmcert.py selftest checks that the capture server just started
     validates against the certificate the game trusts. FAIL asks the same question as 1b.
  4. Start the bridge and wait until UDP 61700 is bound.
  5. Self-test, only while SimHub is NOT running: listen on UDP 30777 ourselves, send all-zero
     test frames (plus a "seated" ev10 post for the seat gate) to 61700, and check the bridge
     answers with packets of the size and signature in SimHub's game definition (91 bytes for
     the 9-field layout, 121 for the 18-field Motion-haptics layout) and zero motion.
     SimHub is not running at that moment, so nothing can reach the rig.
  6. Start SimHub in its own prefix, unless it is already running (never a second instance).
     Warn (not stop) if the SC Haptics plugin DLL is not installed in SimHub.
  6b. Warn (not stop) if SimHub's log says its active game is not Star Citizen: SimHub then
     waits for that other game's data ("Waiting for data") and nothing reaches the rig.
  7. Off by default since 2026-11-18 (see RIG_PAUSE): wait for Enter while you bring the rig up.
  8. Start the game with LUG-Helper's sc-launch.sh, unchanged.
  9. Keep the bridge and capture server running while the game and the RSI Launcher run. Nothing
     is closed for you (2026-11-18): when you close the launcher, the bridge and capture server
     stop. SimHub is left running; close it yourself. Wine left over from the game or SimHub:
     python3 ~/sc_start.py cleanup (see WINE CLEAN-UP below).

If the game is already running (for example the bridge crashed), steps 6-8 skip what is
already up and the script just restarts the capture server and bridge and keeps watching.

    python3 ~/sc_start.py stop
closes the launcher and Wine in the game prefix (refuses while StarCitizen.exe runs) and stops
any capture server and bridge. SimHub is left running.
    python3 ~/sc_start.py cleanup [game|simhub|all] [--yes] [--rig-off]
ends Wine left over in the game prefix (RSI Launcher, Gamescope, Wine helpers) and/or SimHub's
prefix (SimHub itself too). Lists what it found and asks first; --yes answers yes
(--rig-off is the older name of --yes).

Logs: ~/lm_capture/launch/ (capture server and bridge logs rotate at 5 MB).
"""
import json
import re
import logging
import logging.handlers
import os
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

try:  # error and crash logs (scm_errlog.py next to this file; optional: an older install runs without it)
    sys.path.insert(0, os.path.dirname(os.path.realpath(os.path.abspath(__file__))))
    import scm_errlog as EL
except Exception:  # noqa: BLE001
    EL = None

HOME = os.path.expanduser("~")
# The repository this launcher belongs to (~/sc_start.py is a symlink to <repo>/scripts/sc_start.py).
REPO_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def _shared(name):
    """A shared module of the kit (setup/common): next to this file in a Windows install folder,
    in the repository otherwise."""
    here = os.path.dirname(os.path.realpath(os.path.abspath(__file__)))
    for d in (here, os.path.join(REPO_DIR, "setup", "common")):
        if os.path.isfile(os.path.join(d, name + ".py")):
            if d not in sys.path:
                sys.path.append(d)
            return __import__(name)
    sys.exit("sc_start: %s.py not found next to this launcher or in %s. The installation is incomplete: "
             "run the installer's apply (or update) again." % (name, os.path.join(REPO_DIR, "setup", "common")))


PORTS = _shared("scm_ports")   # the one port table
PROC = _shared("scm_proc")     # the one process-matching rule


def saved_setting(key):
    """A location the Linux installer saved in ~/.config/sc-motion/config.json, or None."""
    try:
        with open(os.path.join(HOME, ".config", "sc-motion", "config.json")) as f:
            return json.load(f).get(key) or None
    except (OSError, ValueError, AttributeError):
        return None

# ----------------------------------------------------------------------------- settings
CAPTURE_SCRIPT = os.path.join(HOME, "SC_Motion_Server.py")
CAPTURE_CMD = [sys.executable, "-u", CAPTURE_SCRIPT]
# The bridge (2026-10-23: lm_simhub4t.py = lm_simhub4 behind the tee that also feeds the SC
# Haptics SimHub plugin on UDP 61701; lm_simhub4 = lm_simhub3's unshaped motion + seat gate,
# plus the SimHub Motion-haptics fields, packed as SimHub's registered .simdef says).
# The previous bridge stays available for one start (it only works with the 9-field .simdef):
#   env SC_BRIDGE_SCRIPT=$HOME/lm_simhub3t.py python3 ~/sc_start.py
BRIDGE_SCRIPT = os.environ.get("SC_BRIDGE_SCRIPT", os.path.join(HOME, "lm_simhub4t.py"))
BRIDGE_CMD = [sys.executable, "-u", BRIDGE_SCRIPT, "print"]
# Every bridge version; a leftover of any of them is stopped before the start (they all bind 61700).
ALL_BRIDGE_NEEDLES = ("lm_simhub.py", "lm_simhub2.py", "lm_simhub3.py", "lm_simhub3t.py",
                      "lm_simhub4.py", "lm_simhub4t.py")
SIMDEF_UUID = "2f10c0be-479b-4b35-8671-a1b16ce4b874"   # the Star Citizen External Sim

SIMHUB_PREFIX = os.path.join(HOME, "linux-simracing-utils", "pfx")
SIMHUB_WINE = os.path.join(HOME, "linux-simracing-utils", "var", "wine", "bin", "wine")
SIMHUB_EXE = r"C:\Program Files (x86)\SimHub\SimHubWPF.exe"
# SimHub's folder: env SC_SIMHUB_DIR, else the installer's saved choice, else the default.
SIMHUB_DIR = (os.environ.get("SC_SIMHUB_DIR") or saved_setting("simhub_dir")
              or os.path.join(SIMHUB_PREFIX, "drive_c", "Program Files (x86)", "SimHub"))
SIMHUB_LOG = os.path.join(SIMHUB_DIR, "Logs", "SimHub.txt")
# SC Haptics plugin DLL (installed by sc_haptics_install.py); only checked, never changed here.
HAPTICS_DLL = os.path.join(SIMHUB_DIR, "SCHaptics.dll")

# CERTIFICATE PRE-FLIGHT (2026-10-18)
# sc_lmcert.py (with sc_certswap.py next to it) checks the LiveMotion TLS certificate: 'check'
# before anything starts, 'selftest' once the capture server listens. Both are read-only.
# On FAIL the script asks: abort, or continue with a warning that motion may be disabled; for
# 'check' also: repair = sc_lmcert.py repair (recreates missing copies, puts ~/lm.crt back into every livemotion.crt copy,
# backups first; refuses while the game runs), then check again.
# SC_START_CERT_ON_FAIL=abort|continue|repair answers that question without a prompt (needed when
# there is no terminal; without a terminal and without this setting a FAIL aborts).
LMCERT_SCRIPT = os.path.join(HOME, "sc_lmcert.py")
CERT_TOOL_CMD = "python3 ~/sc_lmcert.py"   # how the messages name the certificate tool (Windows sets its own)
CERT_ON_FAIL = os.environ.get("SC_START_CERT_ON_FAIL", "ask").lower()

# The LUG-Helper prefix: env SC_GAME_PREFIX, else the installer's saved choice, else LUG-Helper's
# default location.
GAME_PREFIX = (os.environ.get("SC_GAME_PREFIX") or saved_setting("game_prefix")
               or os.path.join(HOME, "Games", "star-citizen"))
SC_LAUNCH = os.path.join(GAME_PREFIX, "sc-launch.sh")

LOG_DIR = os.path.join(HOME, "lm_capture", "launch")

# RIG PAUSE (2026-10-17)
# The rig is brought up by hand, in the order the motion hardware needs (for example: motion
# power (cabinet) OFF before SimHub starts, SimHub connects to the controller, then the cabinet is
# switched ON; reset the connection if SimHub shows "Actuator is unpowered"). Wording made
# vendor-neutral 2026-10-19 (kit 1.2). The script waits for Enter at that point.
# 2026-11-18: off by default. Opening the RSI Launcher is safe for the rig: the game sends no
# motion before you are spawned, so there is time to bring the rig up while the launcher and the
# game load. To wait for Enter before the launcher starts: env SC_START_PAUSE=1 (or set
# RIG_PAUSE = True here). With the pause on, the launcher needs a terminal.
RIG_PAUSE = os.environ.get("SC_START_PAUSE") == "1"
# Test-only override, so the script can be exercised without a terminal. Not for normal use.
if os.environ.get("SC_START_NO_PAUSE") == "1":
    RIG_PAUSE = False

# WINE CLEAN-UP (2026-11-18; replaces AUTO CLOSE of 2026-10-17)
# Nothing closes Wine by itself any more: the 30 s auto-close after the game and sc_gs_exit.sh's
# watcher are gone (they also closed the game's crash messages before anyone could read them).
# Leftovers are ended on request: python3 ~/sc_start.py cleanup [game|simhub|all], the control
# window's "Clean up Wine" button, or its option to clean up when the window closes.
#   game   = every process of this user whose WINEPREFIX is the game prefix (RSI Launcher, Wine
#            helpers, sc-launch.sh) and Gamescope started for the launcher. Refused while
#            StarCitizen.exe runs.
#   simhub = SimHub (asked to close first) and every process whose WINEPREFIX is SimHub's prefix,
#            a stuck SimHubWPF.exe included. Asked separately (a rig without a standby park:
#            switch the motion power OFF first).
# Order: SimHub's own close (wine taskkill, up to 15 s) -> the prefix's 'wineserver -k' -> up to
# 5 s -> SIGKILL whatever is left. Shells with that WINEPREFIX (sc-launch.sh shell) are listed
# but never ended.
WINE_PARTS = ("game", "simhub")
WINE_KEEP = ("bash", "sh", "dash", "zsh", "fish")

CAPTURE_PORT_TCP = PORTS.CAPTURE_TCP
BRIDGE_PORT_UDP = PORTS.TEE_UDP      # the tee listens here; the bridge behind it on PORTS.BRIDGE_UDP
SIMHUB_PORT_UDP = PORTS.SIMHUB_UDP

# Lower-case program names used to recognise processes.
GAME_EXE = "starcitizen.exe"
LAUNCHER_EXE = "rsi launcher.exe"
GAME_NEEDLES = (LAUNCHER_EXE, GAME_EXE)
SIMHUB_NEEDLE = "simhubwpf.exe"
# Recorders, superseded bridges and replay tools (a replay would feed recorded flight into the
# live bridge). Never stopped automatically.
BLOCKER_NEEDLES = ("lmrec.py", "lm_bridge.py", "lmcapture.py", "lm_replay.py", "lm_replay2.py",
                   "sc_feedtest.py")
CAPTURE_NEEDLE = os.path.basename(CAPTURE_SCRIPT).lower()
# the capture server under its old name (lm_server2.py) is also stopped as a leftover
CAPTURE_NEEDLES = (CAPTURE_NEEDLE, "lm_server2.py")
BRIDGE_NEEDLE = os.path.basename(BRIDGE_SCRIPT).lower()
BRIDGE_NEEDLES = tuple(sorted(set(ALL_BRIDGE_NEEDLES + (BRIDGE_NEEDLE,))))

PKT = struct.Struct("<IIHHQBQBBQBBBBdI9f")  # SimHub External Sim packet, 9-field layout (91 bytes)


def bridge_layout():
    """-> (struct, telemetry signature or None, text). lm_simhub4*: the layout of SimHub's
    registered .simdef (read with lm_simhub4's own code, so both always agree); an error
    returns (None, None, reason). Any other bridge: the fixed 9-field packet."""
    real = os.path.realpath(BRIDGE_SCRIPT)
    if os.path.basename(real).lower() not in ("lm_simhub4.py", "lm_simhub4t.py"):
        return PKT, None, "fixed 9-field layout (%d bytes)" % PKT.size
    d = os.path.dirname(real)
    if d not in sys.path:
        sys.path.insert(0, d)
    try:
        import lm_simhub4
        path, how = lm_simhub4.find_simdef()
        sd = lm_simhub4.load_simdef(path)
    except SystemExit as e:
        return None, None, str(e)
    except Exception as e:
        return None, None, "cannot load lm_simhub4 (%s)" % e
    note = lm_simhub4.unfilled_note(sd) if hasattr(lm_simhub4, "unfilled_note") else ""
    return (struct.Struct(sd["fmt"]), sd["tele_sig"],
            "%s: %d fields, %d bytes, signature 0x%08X%s" % (path, len(sd["fields"]), struct.calcsize(sd["fmt"]), sd["tele_sig"],
                                                           ("; " + note) if note else ""))


def simhub_active_game(log=None):
    """-> the game SimHub's log last started a game manager for, or None if unknown.
    log: SimHub's Logs/SimHub.txt (default: SIMHUB_LOG; sc_start_win.py sets it)."""
    log = log or SIMHUB_LOG
    try:
        with open(log, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 512 * 1024))
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    hits = re.findall(r"\(Re\)Starting (\S+) Game Manager", text)
    return hits[-1] if hits else None

# ----------------------------------------------------------------------------- helpers
def say(text, speak=False):  # noqa: ARG001  speak: kept for callers; nothing is spoken (2026-11-18)
    print(time.strftime("%H:%M:%S"), text, flush=True)


_base = PROC.base
_program = PROC.program


def scan(*needles):
    """Running processes whose program is one of needles: [(pid, cmdline)] (rule: scm_proc.py)."""
    return PROC.scan(*needles)


def simhub_state():
    """-> ("none" | "running" | "stuck", [pids]). "stuck": a SimHubWPF.exe process whose Wine
    server is gone; it has no window and does nothing, but it used to count as "running" and
    the launcher then never started SimHub. The Windows launcher replaces this."""
    return PROC.wine_state(SIMHUB_NEEDLE)


def clear_stuck_simhub(pids):
    """Ask, then end stuck SimHub processes. -> True when none is left."""
    say("WARNING: SimHub is in the process list (pid %s) but its Wine server is gone: it is stuck, "
        "has no window and cannot be used." % ", ".join(str(p) for p in pids))
    if not sys.stdin.isatty():
        fail("a stuck SimHub process is in the way and there is no terminal to ask; end it "
             "(cabinet OFF): kill %s" % " ".join(str(p) for p in pids))
    say("Rig check first: motion cabinet OFF, nobody in the rig.")
    try:
        ans = input("End the stuck SimHub process and start SimHub fresh? [y]es / [q]uit: ").strip().lower()
    except EOFError:
        ans = "q"
    if ans not in ("y", "yes"):
        fail("stopped at your request (stuck SimHub process left as it is)")
    for pid in pids:
        for sig, wait in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 2.0)):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                break
            except PermissionError:
                fail("no permission to end pid %d" % pid)
            end = time.monotonic() + wait
            while time.monotonic() < end and os.path.exists("/proc/%d" % pid):
                time.sleep(0.2)
            if not os.path.exists("/proc/%d" % pid):
                break
    state, left = simhub_state()
    if state != "none":
        fail("could not end the stuck SimHub process (pid %s)" % ", ".join(str(p) for p in left))
    say("stuck SimHub process ended")
    return True


def listening(proto, port):
    """Describe local sockets bound to this port (TCP: listening only), from /proc/net."""
    out = []
    for fam in ("", "6"):
        path = "/proc/net/%s%s" % (proto, fam)
        try:
            with open(path) as f:
                rows = f.readlines()[1:]
        except OSError:
            continue
        for row in rows:
            parts = row.split()
            if len(parts) < 4:
                continue
            local, state = parts[1], parts[3]
            if int(local.rsplit(":", 1)[1], 16) != port:
                continue
            if proto == "tcp" and state != "0A":
                continue
            out.append("%s%s port %d bound (%s)" % (proto, fam, port, local))
    return "\n".join(out)


def _gone(pid):
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"  # exited, not yet collected
    except OSError:
        return True


def stop_pid(pid, name):
    """Stop a process that is not our child (a leftover from an earlier run)."""
    for sig, wait in ((signal.SIGINT, 5.0), (signal.SIGTERM, 3.0), (signal.SIGKILL, 1.0)):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return
        end = time.monotonic() + wait
        while time.monotonic() < end:
            if _gone(pid):
                return
            time.sleep(0.1)
    say("WARNING: could not stop %s (pid %d)" % (name, pid))


def stop_proc(proc, name):
    """Stop one of our own children: Ctrl-C first, then terminate, then kill."""
    if proc is None or proc.poll() is not None:
        return
    say("stopping %s" % name)
    for action, wait in ((lambda: proc.send_signal(signal.SIGINT), 5.0),
                         (proc.terminate, 3.0), (proc.kill, 2.0)):
        try:
            action()
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue
    say("WARNING: could not stop %s (pid %d)" % (name, proc.pid))


def tail(path, n=20):
    try:
        with open(path, "r", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return "(no log)"


def start_logged(cmd, name, logname, **kw):
    """Start a child whose output goes to a rotating log (5 MB, one backup)."""
    path = os.path.join(LOG_DIR, logname)
    lg = logging.getLogger(logname)
    lg.setLevel(logging.INFO)
    lg.propagate = False
    for h in list(lg.handlers):
        lg.removeHandler(h)
    h = logging.handlers.RotatingFileHandler(path, maxBytes=5_000_000, backupCount=1)
    h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    lg.addHandler(h)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)

    def pump():
        for line in iter(proc.stdout.readline, b""):
            text = line.decode("utf-8", "replace").rstrip()
            lg.info(text)
            if EL is not None:
                EL.child_line(name, text)
        h.flush()

    threading.Thread(target=pump, daemon=True).start()
    say("started %s (pid %d), log %s" % (name, proc.pid, path))
    return proc, path


def wait_for(check, proc, name, logpath, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            t = tail(logpath)
            print(t)
            fail("%s exited during start-up (code %s); log above: %s" % (name, proc.returncode, logpath),
                 trace="last lines of %s:\n%s" % (logpath, t))
        if check():
            return
        time.sleep(0.25)
    t = tail(logpath)
    print(t)
    fail("%s did not become ready within %d s; log above: %s" % (name, timeout, logpath),
         trace="last lines of %s:\n%s" % (logpath, t))


def selftest(expect_zero):
    """Listen on 30777, feed zero frames to 61700, check the bridge's reply."""
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        rx.bind(("127.0.0.1", SIMHUB_PORT_UDP))
    except OSError as e:
        rx.close()
        return None, "could not listen on UDP %d (%s); output test skipped" % (SIMHUB_PORT_UDP, e)
    rx.settimeout(0.2)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    zero = [0.0, 0.0, 0.0]
    frame = {"t": 0.0, "ev": 3, "name": "sc_start_selftest",
             "m": [9, 35, 38, 40, 92], "v": [zero, zero, zero, zero, 0.0]}
    got = None
    end = time.monotonic() + 3.0
    try:
        while time.monotonic() < end and got is None:
            frame["t"] = time.time()
            seat = {"t": frame["t"], "ev": 10, "name": "sc_start_selftest", "m": [38], "v": [1]}
            tx.sendto(json.dumps(seat).encode(), ("127.0.0.1", BRIDGE_PORT_UDP))
            tx.sendto(json.dumps(frame).encode(), ("127.0.0.1", BRIDGE_PORT_UDP))
            try:
                got, _ = rx.recvfrom(4096)
            except socket.timeout:
                continue
    finally:
        rx.close()
        tx.close()
    if got is None:
        return False, "no packet from the bridge on UDP %d within 3 s" % SIMHUB_PORT_UDP
    pkt, sig, _ = bridge_layout()
    if pkt is None:
        return False, "the bridge's packet layout is unknown (SimHub game definition problem)"
    if len(got) != pkt.size:
        return False, "bridge packet is %d bytes, expected %d" % (len(got), pkt.size)
    u = pkt.unpack(got)
    if sig is not None and u[1] != sig:
        return False, "bridge packet signature 0x%08X, SimHub's definition says 0x%08X" % (u[1], sig)
    vals = u[16:25]   # the 9 motion fields follow the 16 header fields
    if expect_zero and any(abs(x) > 0.01 for x in vals):
        return False, "test packet has non-zero motion values: %s" % ", ".join("%.3f" % x for x in vals)
    return True, "bridge answered with %d-byte packets%s" % (
        len(got), ", motion values all zero" if expect_zero else "")


def game_wine_bin():
    """LUG-Helper's Wine runner bin directory, read from sc-launch.sh (export wine_path=...)."""
    try:
        with open(SC_LAUNCH) as f:
            for line in f:
                m = re.match(r'\s*export\s+wine_path="([^"]+)"', line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


def gamescope_for_launcher():
    """gamescope processes that were started for the RSI Launcher."""
    out = []
    for pid, cmd in scan("gamescope"):
        if LAUNCHER_EXE in cmd.lower():
            out.append(pid)
    return out


def _norm_path(p):
    try:
        return os.path.realpath(p.rstrip("/") or "/")
    except (OSError, ValueError):
        return p


def _ancestors():
    """This process and its parents (never ended, even if started with that WINEPREFIX)."""
    out, pid = set(), os.getpid()
    while pid > 1 and pid not in out:
        out.add(pid)
        try:
            with open("/proc/%d/stat" % pid) as f:
                pid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return out


def _proc_text(pid, name):
    try:
        with open("/proc/%d/%s" % (pid, name), "rb") as f:
            return f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
    except OSError:
        return ""


def prefix_processes(prefix):
    """[(pid, name, cmdline)] of this user whose environment has WINEPREFIX=prefix."""
    want, uid, skip, out = _norm_path(prefix), os.getuid(), _ancestors(), []
    for d in os.listdir("/proc"):
        if not d.isdigit() or int(d) in skip:
            continue
        try:
            if os.stat("/proc/" + d).st_uid != uid:
                continue
            with open("/proc/%s/environ" % d, "rb") as f:
                env = f.read()
        except OSError:
            continue
        i = env.find(b"WINEPREFIX=")
        if i < 0 or (i > 0 and env[i - 1:i] != b"\0"):
            continue
        val = env[i + 11:env.find(b"\0", i) if env.find(b"\0", i) > 0 else len(env)].decode("utf-8", "replace")
        if _norm_path(val) == want:
            pid = int(d)
            out.append((pid, _proc_text(pid, "comm"), _proc_text(pid, "cmdline")[:200]))
    return out


def wine_leftovers(which):
    """What a clean-up of "game" or "simhub" would end. -> {"end": [(pid, name, cmd)],
    "keep": [...], "blocked": reason or None, "simhub": "none"|"running"|"stuck" (simhub only)}."""
    prefix = GAME_PREFIX if which == "game" else SIMHUB_PREFIX
    if not os.path.isdir(os.path.join(prefix, "drive_c")):
        # a wrong setting (your home, "/") must never select every Wine program you run
        return {"end": [], "keep": [], "blocked": "%s is not a Wine prefix (no drive_c)" % prefix,
                "simhub": "none"}
    procs = prefix_processes(prefix)
    seen = {p for p, _, _ in procs}
    info = {"blocked": None}
    if which == "game":
        extra = gamescope_for_launcher()
        if scan(GAME_EXE):
            info["blocked"] = "Star Citizen is running"
    else:
        state, extra = simhub_state()
        info["simhub"] = state
    for pid in extra:
        if pid not in seen and pid not in _ancestors():
            procs.append((pid, _proc_text(pid, "comm"), _proc_text(pid, "cmdline")[:200]))
    keep = [p for p in procs if p[1] in WINE_KEEP and "sc-launch.sh" not in p[2] and "sc_gs_exit" not in p[2]]
    info["end"] = sorted(p for p in procs if p not in keep)
    info["keep"] = sorted(keep)
    return info


def _alive(pid):
    return os.path.exists("/proc/%d" % pid) and not _gone(pid)


def wine_cleanup(which):
    """End what wine_leftovers(which) lists (see WINE CLEAN-UP). -> True when nothing is left.
    The caller has asked (and, for SimHub, had the motion power switched off)."""
    label = "game prefix" if which == "game" else "SimHub's prefix"
    info = wine_leftovers(which)
    if info["blocked"]:
        say("Wine clean-up of the %s not done: %s" % (label, info["blocked"]))
        return False
    if not info["end"]:
        say("Wine clean-up of the %s: nothing left over" % label)
        return True
    prefix = GAME_PREFIX if which == "game" else SIMHUB_PREFIX
    env = dict(os.environ, WINEPREFIX=prefix)
    wbin = game_wine_bin() if which == "game" else os.path.dirname(SIMHUB_WINE)
    say("Wine clean-up of the %s: %d process(es)" % (label, len(info["end"])))
    if which == "simhub" and info.get("simhub") == "running":
        say("asking SimHub to close")
        try:
            subprocess.run([SIMHUB_WINE, "taskkill", "/IM", "SimHubWPF.exe"], env=env, timeout=20,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            pass
        end = time.monotonic() + 15
        while time.monotonic() < end and simhub_state()[0] != "none":
            time.sleep(0.5)
    server = os.path.join(wbin, "wineserver") if wbin else None
    if server and os.path.exists(server):
        try:
            subprocess.run([server, "-k"], env=env, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            say("wineserver -k did not finish; ending the processes directly")
    else:
        say("Wine runner of the %s not found; ending the processes directly" % label)
    end = time.monotonic() + 5
    while time.monotonic() < end and any(_alive(p) for p, _, _ in wine_leftovers(which)["end"]):
        time.sleep(0.5)
    left = wine_leftovers(which)
    if left["blocked"]:
        say("Wine clean-up of the %s stopped: %s" % (label, left["blocked"]))
        return False
    for pid, name, _ in left["end"]:
        say("ending %s (pid %d)" % (name or "process", pid))
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    end = time.monotonic() + 3
    while time.monotonic() < end and wine_leftovers(which)["end"]:
        time.sleep(0.3)
    final = wine_leftovers(which)["end"]
    if final:
        for pid, name, cmd in final:
            print("  still running: pid %d %s %s" % (pid, name, cmd))
        say("WARNING: Wine clean-up of the %s left %d process(es) (listed above)" % (label, len(final)))
        return False
    say("Wine clean-up of the %s: done" % label)
    return True


def cmd_cleanup(args):
    """python3 ~/sc_start.py cleanup [game|simhub|all] [--yes] [--rig-off]"""
    flags = {a for a in args if a.startswith("--")}
    words = [a for a in args if not a.startswith("--")] or ["all"]
    if len(words) != 1 or words[0] not in ("game", "simhub", "all") or flags - {"--yes", "--rig-off"}:
        sys.exit("usage: python3 ~/sc_start.py cleanup [game|simhub|all] [--yes] [--rig-off]")
    tty = sys.stdin.isatty()
    rc = 0
    for which in (WINE_PARTS if words[0] == "all" else (words[0],)):
        label = "game prefix" if which == "game" else "SimHub's prefix"
        info = wine_leftovers(which)
        print("%s: %d to end, %d shell(s) kept%s" % (label, len(info["end"]), len(info["keep"]),
                                                    ("; " + info["blocked"]) if info["blocked"] else ""))
        for pid, name, cmd in info["end"]:
            print("  pid %-7d %-16s %s" % (pid, name, cmd[:90]))
        if info["blocked"]:
            rc = 1
            continue
        if not info["end"]:
            continue
        if which == "simhub" and info.get("simhub") in ("running", "stuck") and not flags & {"--yes", "--rig-off"}:
            if not tty:
                say("SimHub is not closed without a terminal (add --yes)")
                rc = 1
                continue
            ans = input("Close SimHub? (It drives the motion rig: unless your rig parks by itself, switch the "
                        "motion power OFF first.) [y/N] ")
            if ans.strip().lower() not in ("y", "yes"):
                continue
        elif "--yes" not in flags and "--rig-off" not in flags:
            if not tty:
                say("not ended without a terminal (add --yes)")
                rc = 1
                continue
            if input("End these %d process(es)? [y/N] " % len(info["end"])).strip().lower() not in ("y", "yes"):
                continue
        if not wine_cleanup(which):
            rc = 1
    return rc


def cmd_stop():
    """python3 ~/sc_start.py stop : close the launcher and Wine (game must already be closed),
    then stop any capture server or bridge. SimHub is left running."""
    os.makedirs(LOG_DIR, exist_ok=True)
    if scan(GAME_EXE):
        fail("Star Citizen is still running. Exit the game first, then run stop again.")
    if scan(*GAME_NEEDLES) or gamescope_for_launcher():
        wine_cleanup("game")
    else:
        say("RSI Launcher is not running")
    for needles, name in ((BRIDGE_NEEDLES, "bridge"), (CAPTURE_NEEDLES, "capture server")):
        for pid, cmd in scan(*needles):
            say("stopping %s (pid %d)" % (name, pid))
            stop_pid(pid, name)
    for pid, name in addon_leftovers():
        say("stopping add-on %s (pid %d)" % (name, pid))
        stop_pid(pid, "add-on %s" % name)
    say("done; SimHub left running")


def run_lmcert(action):
    """Run sc_lmcert.py <action>; returns (exit code or None if not run, output text)."""
    if not os.path.exists(LMCERT_SCRIPT):
        return None, "%s not found" % LMCERT_SCRIPT
    try:
        r = subprocess.run([sys.executable, LMCERT_SCRIPT, action], capture_output=True,
                           text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return 99, "sc_lmcert.py %s did not finish within 60 s" % action
    out = (r.stdout + r.stderr).strip()
    return r.returncode, out


def cert_gate(action, what):
    """Certificate pre-flight. True = all good; False = user chose to continue after a FAIL.
    Aborting calls fail() (stops the capture server and bridge if they were started)."""
    rc, out = run_lmcert(action)
    last = out.splitlines()[-1] if out else ""
    if rc is None:
        say("WARNING: certificate %s skipped (%s)" % (what, out))
        return True
    if rc == 0:
        say("certificate %s: %s" % (what, last))
        return True
    if action == "check" and rc == 9:
        say("certificate %s: %s" % (what, last))
        say("  continuing; renew in time: %s new, then activate (game closed, "
            "capture server stopped)" % CERT_TOOL_CMD)
        return True
    print(out)
    say("CERTIFICATE %s FAILED: the game will probably reject the capture server, so motion "
        "telemetry and shaker effects may be disabled." % what.upper())
    say("  status: %s status    after a game/D-BOX update: %s repair (game closed)" % (CERT_TOOL_CMD, CERT_TOOL_CMD))
    can_repair = action == "check"
    choice = CERT_ON_FAIL
    if choice == "repair" and not can_repair:
        choice = "abort"
    if choice not in ("abort", "continue", "repair"):
        if not sys.stdin.isatty():
            fail("certificate %s failed and there is no terminal to ask; set "
                 "SC_START_CERT_ON_FAIL=repair or continue" % what)
        q = ("  [r]epair (put your working certificate back, backups first), [a]bort start-up, or "
             "[c]ontinue with motion possibly disabled? (r/a/c, Enter = abort): " if can_repair else
             "  [a]bort start-up, or [c]ontinue with motion possibly disabled? (a/c, Enter = abort): ")
        while True:
            try:
                a = input(q).strip().lower()
            except EOFError:
                a = "a"
            if a in ("", "a", "abort"):
                choice = "abort"
                break
            if a in ("c", "continue"):
                choice = "continue"
                break
            if can_repair and a in ("r", "repair"):
                choice = "repair"
                break
    if choice == "repair":
        rc, out = run_lmcert("repair")
        print("\n".join("  " + x for x in out.splitlines()))
        if rc == 0:
            rc, out = run_lmcert("check")
            last = out.splitlines()[-1] if out else ""
            if rc in (0, 9):
                say("certificate repaired: %s" % last)
                return True
        say("REPAIR DID NOT FIX IT (sc_lmcert exit %s); nothing else was changed" % rc)
        choice = "abort" if CERT_ON_FAIL == "repair" else None
        if choice is None:
            while True:
                try:
                    a = input("  [a]bort start-up, or [c]ontinue with motion possibly disabled? "
                              "(a/c, Enter = abort): ").strip().lower()
                except EOFError:
                    a = "a"
                if a in ("", "a", "abort"):
                    choice = "abort"
                    break
                if a in ("c", "continue"):
                    choice = "continue"
                    break
    if choice == "abort":
        fail("start-up aborted after the certificate %s" % what)
    say("WARNING: continuing after a failed certificate %s; motion telemetry may be disabled" % what)
    return False


def cmd_rollback():
    """Undo the newest update (setup/common/scm_update.py rollback). -> exit code."""
    scm_update = _shared("scm_update")
    return scm_update.rollback("linux", REPO_DIR, HOME, say,
                               game_running=lambda: bool(scan(GAME_EXE)),
                               simhub_running=lambda: simhub_state()[0] == "running")


def update_gate():
    """Step 0. -> True when an update applied in this start changed what reaches the motion
    platform (the warning is repeated at the rig pause). Never stops the start-up by itself,
    except when you choose [q]uit."""
    common = os.path.join(REPO_DIR, "setup", "common")
    if not os.path.isfile(os.path.join(common, "scm_update.py")):
        say("update check skipped (no setup/common/scm_update.py next to this launcher)")
        return False
    if common not in sys.path:
        sys.path.insert(0, common)
    try:
        import scm_update
    except Exception as e:  # noqa: BLE001
        say("update check skipped (%s)" % e)
        return False
    return scm_update.gate("linux", REPO_DIR, HOME, say,
                           game_running=lambda: bool(scan(GAME_EXE)),
                           simhub_running=lambda: simhub_state()[0] == "running")


def motion_changed_reminder():
    """Printed at the rig pause after an update that changed motion values."""
    print()
    print("  !! This start follows an update that CHANGED PLATFORM MOVEMENT VALUES.")
    print("  !! Check your SimHub Motion settings (Motion tab and Motion haptics) now, before")
    print("  !! anyone rides. First run: nobody in the rig and motion power OFF, then power ON")
    print("  !! with nobody in the rig, then a person.")
    say("Platform movement values have changed in this update. Check your SimHub motion settings "
        "before anyone rides.")


# ----------------------------------------------------------------------------- main
STATE = {"server": None, "bridge": None, "cert_ok": True, "motion_changed": False, "addons": []}


# ---------------------------------------------------------------- add-ons (taps with a "run" command)
PROCESSES = PROC.processes     # [(pid, argv)]; the Windows launcher replaces it
ADDON_BRIDGE_DIR = REPO_DIR    # given to add-ons as SC_BRIDGE_DIR; the Windows launcher sets its own


def _taps_module():
    try:
        import scm_taps     # next to this file (scripts/, or the Windows install folder)
        return scm_taps
    except Exception as e:  # noqa: BLE001  add-ons are optional
        say("add-ons not checked (%s)" % e)
        return None


def addon_commands():
    """[(name, argv)] of the tap "run" commands to start; prints why a tap is not started."""
    if os.environ.get("SC_START_NO_ADDONS") == "1":
        say("add-ons not started (SC_START_NO_ADDONS=1)")
        return []
    T = _taps_module()
    if T is None:
        return []
    taps, notes = T.load()
    for n in notes:
        say("add-on tap file " + n)
    out = []
    for t in taps:
        argv, why = T.run_command(t, sys.executable)
        if argv:
            out.append((t["name"], argv))
        elif why:
            say("add-on %s: not started (%s); it still gets its telemetry copy on UDP %d" % (t["name"], why, t["port"]))
    return out


def _same_command(argv, cmdline_argv):
    """A running process started with this add-on command (any python interpreter)."""
    return len(cmdline_argv) == len(argv) and PROC.program(cmdline_argv) == PROC.program(argv) \
        and cmdline_argv[1:] == argv[1:]


def addon_leftovers(cmds=None):
    """[(pid, name)] of add-on processes from an earlier run."""
    cmds = addon_commands() if cmds is None else cmds
    if not cmds:
        return []
    out = []
    for pid, argv in PROCESSES():
        for name, cmd in cmds:
            if _same_command(cmd, argv):
                out.append((pid, name))
    return out


def start_addons(cmds):
    """Start each add-on; one that fails to start is reported, never stops the launcher."""
    env = dict(os.environ, SC_BRIDGE_DIR=ADDON_BRIDGE_DIR)   # where the bridge (and its field database) is
    for name, argv in cmds:
        try:
            proc, log = start_logged(argv, "add-on %s" % name, "addon_%s.log" % name, stdin=subprocess.DEVNULL, env=env)
        except OSError as e:
            say("WARNING: add-on %s could not be started (%s)" % (name, e))
            continue
        STATE["addons"].append((proc, name, log))


def stop_addons():
    for proc, name, _log in STATE["addons"]:
        stop_proc(proc, "add-on %s" % name)
    STATE["addons"] = []


def check_addons(warned):
    """Report each add-on that stopped by itself (once). -> the updated set of warned names."""
    for proc, name, log in STATE["addons"]:
        if name not in warned and proc.poll() is not None:
            say("add-on %s has stopped (exit code %s); motion is not affected. Its log: %s" % (name, proc.returncode, log))
            if EL is not None:
                EL.child_exit("add-on %s" % name, proc.returncode, log, during="play")
            warned.add(name)
    return warned


def cleanup():
    stop_addons()
    stop_proc(STATE["bridge"], "bridge")
    stop_proc(STATE["server"], "capture server")
    STATE["bridge"] = STATE["server"] = None


def fail(msg, trace="", level="error"):
    say("STOPPED: " + msg)
    if EL is not None and EL.current() is not None:
        EL.report(level, text=msg, trace=trace)
    cleanup()
    sys.exit(1)


def child_stopped(proc, name, logpath):
    """A child stopped while the game runs: record it in the error log."""
    if EL is not None and proc is not None and proc.poll() is not None:
        EL.child_exit(name, proc.returncode, logpath, during="play")


def run_logged(program, body, closing):
    """Run body() with an error log (scm_errlog): problems are explained in plain English and a
    log is kept when the run had an error or a crash. closing() runs at the end in every case."""
    if EL is not None:
        EL.start(program)
    code = 0
    try:
        body()
    except KeyboardInterrupt:
        print()
        say("interrupted")
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        raise
    except BaseException as e:  # noqa: BLE001  recorded, then shown as usual
        code = 1
        if EL is not None:
            EL.report("crash", e)
        raise
    finally:
        try:
            closing()
        finally:
            if EL is not None:
                EL.finish(code)


def on_signal(signum, frame):
    raise KeyboardInterrupt


def main():
    for s in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, on_signal)
    os.makedirs(LOG_DIR, exist_ok=True)
    say("Star Citizen motion start-up")
    if RIG_PAUSE and not sys.stdin.isatty():
        fail("RIG_PAUSE needs a terminal for the Enter key; run this from a terminal")

    # 0. update check - see "Order" at the top
    STATE["motion_changed"] = update_gate()

    # 1. things we never stop automatically
    blockers = scan(*BLOCKER_NEEDLES)
    if blockers:
        for pid, cmd in blockers:
            print("  pid %d: %s" % (pid, cmd))
        fail("a recorder or old bridge is running (listed above). Stop it first.")
    for p in (CAPTURE_SCRIPT, BRIDGE_SCRIPT):
        if not os.path.exists(p):
            fail("missing %s" % p)
    # 1c. the bridge's packet layout (SimHub's registered game definition)
    pkt, _, layout_text = bridge_layout()
    if pkt is None:
        fail("SimHub game definition: " + layout_text)
    say("bridge layout: " + layout_text)

    # 1b. certificate pre-flight (read-only) - see CERTIFICATE PRE-FLIGHT above
    STATE["cert_ok"] = cert_gate("check", "check")

    # Only a running StarCitizen.exe counts as "game running". A launcher left in the tray from
    # an earlier session is restarted: sc-launch.sh itself runs 'wineserver -k' on the game
    # prefix before it starts the launcher (LUG-Helper behaviour).
    game_running = bool(scan(GAME_EXE))
    if not game_running and (scan(LAUNCHER_EXE) or gamescope_for_launcher()):
        say("an RSI Launcher from an earlier session is still open; sc-launch.sh will restart it")
    sh_state, sh_pids = simhub_state()
    if sh_state == "stuck":
        clear_stuck_simhub(sh_pids)
    simhub_running = sh_state == "running"
    if simhub_running and len(sh_pids) > 1:
        say("WARNING: %d SimHub processes are running (pids %s). A second SimHub can hold the plugin's UDP "
            "port; close SimHub and check with: pgrep -af SimHubWPF" % (len(sh_pids), ", ".join(map(str, sh_pids))))
    if not game_running and not os.path.exists(SC_LAUNCH):
        fail("missing %s" % SC_LAUNCH)
    say("game already running: %s   SimHub already running: %s" % (
        "yes" if game_running else "no", "yes" if simhub_running else "no"))

    # 2. leftovers from an earlier run
    for needles, name in ((BRIDGE_NEEDLES, "bridge"), (CAPTURE_NEEDLES, "capture server")):
        for pid, cmd in scan(*needles):
            say("stopping leftover %s (pid %d)" % (name, pid))
            stop_pid(pid, name)
    addons = addon_commands()
    for pid, name in addon_leftovers(addons):
        say("stopping leftover add-on %s (pid %d)" % (name, pid))
        stop_pid(pid, "add-on %s" % name)
    busy_tcp = listening("tcp", CAPTURE_PORT_TCP)
    busy_udp = listening("udp", BRIDGE_PORT_UDP)
    if busy_tcp or busy_udp:
        print(busy_tcp)
        print(busy_udp)
        fail("port %d/tcp or %d/udp is still in use by something else (above)" % (
            CAPTURE_PORT_TCP, BRIDGE_PORT_UDP))

    # 3. capture server
    srv, srv_log = start_logged(CAPTURE_CMD, "capture server", "capture_server.log")
    STATE["server"] = srv
    wait_for(lambda: listening("tcp", CAPTURE_PORT_TCP), srv, "capture server", srv_log, 15)
    say("capture server listening on 127.0.0.1:%d" % CAPTURE_PORT_TCP)
    # 3b. TLS self-test against the certificate the game trusts
    if not cert_gate("selftest", "self-test"):
        STATE["cert_ok"] = False

    # 4. bridge
    brg, brg_log = start_logged(BRIDGE_CMD, "bridge", "bridge.log")
    STATE["bridge"] = brg
    wait_for(lambda: listening("udp", BRIDGE_PORT_UDP), brg, "bridge", brg_log, 10)
    say("bridge listening on UDP %d" % BRIDGE_PORT_UDP)

    # 5. self-test (only while SimHub is not running, so nothing reaches the rig)
    if simhub_running:
        say("self-test of bridge output skipped: SimHub already owns UDP %d" % SIMHUB_PORT_UDP)
    else:
        ok, msg = selftest(expect_zero=not game_running)
        if ok is False:
            fail("bridge self-test failed: " + msg)
        say(("bridge self-test passed: " if ok else "bridge self-test: ") + msg)

    # 5b. add-ons (taps with a run command); after the self-test so they never see test frames
    start_addons(addons)

    # 6. SimHub, never a second instance
    if os.path.exists(HAPTICS_DLL):
        say("SC Haptics plugin installed (%s)" % os.path.basename(HAPTICS_DLL))
    else:
        say("WARNING: SC Haptics plugin not installed in SimHub (no %s); motion works, shaker "
            "effects will not. Install: python3 ~/sc_haptics_install.py install" % HAPTICS_DLL)
    if simhub_running:
        say("SimHub is already running; not starting another one")
    else:
        env = dict(os.environ, WINEPREFIX=SIMHUB_PREFIX)
        with open(os.path.join(LOG_DIR, "simhub.log"), "ab") as lf:
            subprocess.Popen([SIMHUB_WINE, SIMHUB_EXE], env=env, stdout=lf,
                             stderr=subprocess.STDOUT, start_new_session=True)
        end = time.monotonic() + 60
        while time.monotonic() < end and simhub_state()[0] != "running":
            time.sleep(1)
        if simhub_state()[0] != "running":
            fail("SimHub did not appear within 60 s (log %s)" % os.path.join(LOG_DIR, "simhub.log"))
        say("SimHub started (it keeps running after this script ends)")
        time.sleep(5)   # let SimHub log which game it loaded

    # 6b. SimHub's active game (from its log; warn only)
    active = simhub_active_game()
    if active is None:
        say("could not read SimHub's active game from its log; make sure Star Citizen is selected in SimHub")
    elif SIMDEF_UUID not in active:
        say("WARNING: SimHub's active game is %s, not Star Citizen. SimHub will wait for that game's "
            "data and the rig gets nothing. Select Star Citizen in SimHub's game list." % active)
    else:
        say("SimHub's active game: Star Citizen")

    # 7. rig pause - see RIG_PAUSE above
    if STATE["motion_changed"]:
        motion_changed_reminder()
    if RIG_PAUSE:
        print()
        print("  Rig start-up (no motion rig: just press Enter):")
        print("   - E-stop within reach")
        print("   - follow your hardware's start order (e.g. motion power OFF while SimHub")
        print("     starts and connects to the controller, then ON)")
        print("   - SimHub shows your motion controller as connected (not stuck on 'Starting')")
        # LED check removed 2026-10-17: the rig LED stays blue until telemetry from the game
        # is flowing, so it cannot confirm the SimHub connection before launch. Restore this
        # line if the controller's LED behaviour changes:
        # print("   - rig LED green")
        say("Rig check. Press Enter when the rig is powered on and SimHub shows it connected.")
        input("  Press Enter when the rig is powered on and SimHub shows it connected "
              "(Ctrl-C to stop): ")

    # 8. game
    game = None
    if game_running:
        say("game already running; not launching it again")
    else:
        game = subprocess.Popen([SC_LAUNCH], cwd=GAME_PREFIX, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
        say("started sc-launch.sh (pid %d); its own log is sc-launch.log in the game prefix" % game.pid)

    # 9. watch until the launcher and the game are gone
    say("motion pipeline running. Close the game and the RSI Launcher to stop it, or press Ctrl-C.")
    if not STATE["cert_ok"]:
        say("REMINDER: a certificate check failed at start-up; motion telemetry may be disabled. "
            "If the game does not connect: python3 ~/sc_lmcert.py status")
    gone = 0
    warned = False
    addons_warned = set()
    game_seen = bool(scan(GAME_EXE))
    told = False
    while True:
        time.sleep(5)
        if not warned and (brg.poll() is not None or srv.poll() is not None):
            which = "bridge" if brg.poll() is not None else "capture server"
            say("WARNING: the %s has stopped; motion output has stopped. Run python3 ~/sc_start.py "
                "again to restart it (the game keeps running)." % which)
            child_stopped(brg, "bridge", brg_log) if which == "bridge" else child_stopped(srv, "capture server", srv_log)
            warned = True
        addons_warned = check_addons(addons_warned)
        if scan(GAME_EXE):
            game_seen, told = True, False
        elif game_seen and not told:
            say("Star Citizen has closed. The launcher stays open (nothing is closed for you); close it "
                "when you are done. Leftover Wine: python3 ~/sc_start.py cleanup")
            told = True
        alive = (bool(scan(*GAME_NEEDLES)) or bool(gamescope_for_launcher())
                 or (game is not None and game.poll() is None))
        gone = 0 if alive else gone + 1
        if gone >= 3:
            break
    say("game and launcher have exited")


if __name__ == "__main__":
    if sys.argv[1:] == ["stop"]:
        cmd_stop()
        sys.exit(0)
    if sys.argv[1:] == ["rollback"]:
        sys.exit(cmd_rollback())
    if sys.argv[1:2] == ["cleanup"]:
        sys.exit(cmd_cleanup(sys.argv[2:]))
    if sys.argv[1:]:
        sys.exit("usage: python3 ~/sc_start.py           (start everything)\n"
                 "       python3 ~/sc_start.py stop      (close launcher + Wine after the game, "
                 "stop bridge and capture server; SimHub stays)\n"
                 "       python3 ~/sc_start.py rollback  (undo the newest update; asks first)\n"
                 "       python3 ~/sc_start.py cleanup [game|simhub|all] [--yes] [--rig-off]\n"
                 "                                       (end leftover Wine; lists and asks first)")
    def closing():
        cleanup()
        say("bridge and capture server stopped; SimHub left running")
        # The app-menu entry sets SC_START_HOLD=1 so its terminal window stays open until
        # Enter, letting you read the last status lines (or an error) before it closes.
        if os.environ.get("SC_START_HOLD") == "1" and sys.stdin.isatty():
            try:
                input("Press Enter to close this window. ")
            except (EOFError, KeyboardInterrupt):
                pass

    run_logged("sc_start", main, closing)
