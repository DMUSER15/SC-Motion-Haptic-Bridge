#!/usr/bin/env python3
"""sc_start_win.py - Windows version of sc_start.py: start the Star Citizen motion pipeline,
check it, start SimHub, wait for the rig, then start the RSI Launcher.

    py -3 %USERPROFILE%\\sc-motion\\sc_start_win.py         start everything
    py -3 %USERPROFILE%\\sc-motion\\sc_start_win.py stop    stop a capture server / bridge left running

Same order and checks as sc_start.py on Linux, reusing its helpers (imported unchanged):
  0  update check (scm_update.py): newer public release? Pause only then; [u]pdate replaces
     only the changed files in the repository folder the installer saved, copies the runtime
     files here again, and restarts. Your effects.json, SimHub's settings and your
     certificates are never touched; a change to anything that shapes platform movement is
     announced with a warning (type YES) that is repeated at the rig pause
  1  refuse while a recorder / replay / old bridge runs
  1b certificate check   (sc_cert_win.py lmcert check; FAIL -> abort or continue prompt)
  2  stop a capture server / bridge left over from a crash
  3  capture server SC_Motion_Server.py, wait for 127.0.0.1:61666
  3b TLS self-test against the certificate the game trusts (sc_cert_win.py lmcert selftest)
  1c bridge layout: SimHub's registered "Star Citizen" game definition (abort if unusable)
  4  tee + bridge (lm_simhub4t.py), wait for UDP 61700
  5  self-test with all-zero frames (only while SimHub is not running: nothing reaches the rig)
  6  SimHub (never a second instance); warn if SCHaptics.dll is missing
  6b warn if SimHub's active game (from its log) is not Star Citizen
  7  rig pause: press Enter when the rig is powered and SimHub shows it connected
  8  RSI Launcher (not started again if Star Citizen already runs)
  9  keep the pipeline running; AUTO_STOP_AFTER_GAME s after StarCitizen.exe exits, stop the
     capture server and bridge (the RSI Launcher and SimHub stay open)
Settings: the installer's saved choice (%APPDATA%\\sc-motion\\config.json), overridden by
environment variables:
  SC_SIMHUB_DIR     default C:\\Program Files (x86)\\SimHub
  SC_RSI_LAUNCHER   default C:\\Program Files\\Roberts Space Industries\\RSI Launcher\\RSI Launcher.exe
  SC_BRIDGE_SCRIPT  lm_simhub4t.py next to this file (motion + SimHub Motion haptics fields from
                    the registered game definition); lm_simhub3t.py = the fixed 9 motion fields
  SC_START_CERT_ON_FAIL=abort|continue   answer the certificate question without a prompt
  SC_START_NO_PAUSE=1                    skip the rig pause (tests only)
  SC_UPDATE_CHECK=0                      skip the update check
Logs: %USERPROFILE%\\lm_capture\\launch\\ (same files as on Linux).
"""
import os, signal, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import winproc  # noqa: E402
import sc_start as S  # noqa: E402  (the Linux launcher, imported unchanged for its helpers)

HOME = os.path.expanduser("~")
DRIVE = os.environ.get("SC_CERT_DRIVE_C", os.environ.get("SystemDrive", "C:") + "\\")
PF86 = os.environ.get("ProgramFiles(x86)", os.path.join(DRIVE, "Program Files (x86)"))
PF = os.environ.get("ProgramFiles", os.path.join(DRIVE, "Program Files"))
try:
    import winlocate  # noqa: E402  (locations assigned by the installer)
    _CFG = winlocate.load()
except Exception:  # noqa: BLE001
    _CFG = {}
SIMHUB_DIR = os.environ.get("SC_SIMHUB_DIR") or _CFG.get("simhub_dir") or os.path.join(PF86, "SimHub")
SIMHUB_EXE = os.environ.get("SC_SIMHUB_EXE", os.path.join(SIMHUB_DIR, "SimHubWPF.exe"))
RSI_LAUNCHER = os.environ.get("SC_RSI_LAUNCHER") or _CFG.get("rsi_launcher") or os.path.join(
    PF, "Roberts Space Industries", "RSI Launcher", "RSI Launcher.exe")
CAPTURE_SCRIPT = os.environ.get("SC_CAPTURE_SCRIPT", os.path.join(HERE, "SC_Motion_Server.py"))
BRIDGE_SCRIPT = os.environ.get("SC_BRIDGE_SCRIPT", os.path.join(HERE, "lm_simhub4t.py"))
CERT_WIN = os.path.join(HERE, "sc_cert_win.py")
LOG_DIR = os.path.join(HOME, "lm_capture", "launch")
AUTO_STOP_AFTER_GAME = int(os.environ.get("SC_START_AUTO_STOP", "30"))  # seconds
RIG_PAUSE = os.environ.get("SC_START_NO_PAUSE") != "1"
DETACHED = 0x00000008 | 0x00000200 if os.name == "nt" else 0  # DETACHED_PROCESS | NEW_PROCESS_GROUP


# ---------------------------------------------------------------- Windows versions of helpers
def stop_proc(proc, name):
    """Stop one of our own children (Windows has no Ctrl-C for other processes)."""
    if proc is None or proc.poll() is not None:
        return
    S.say("stopping %s" % name)
    for action, wait in ((proc.terminate, 3.0), (proc.kill, 2.0)):
        try:
            action()
            proc.wait(timeout=wait)
            return
        except (subprocess.TimeoutExpired, OSError):
            continue
    S.say("WARNING: could not stop %s (pid %d)" % (name, proc.pid))


def stop_pid(pid, name):
    winproc.kill(pid)
    end = time.monotonic() + 5
    while time.monotonic() < end:
        if not any(p == pid for p, _, _ in winproc.processes()):
            return
        time.sleep(0.25)
    S.say("WARNING: could not stop %s (pid %d)" % (name, pid))


def run_lmcert(action):
    if not os.path.exists(CERT_WIN):
        return None, "%s not found" % CERT_WIN
    try:
        r = subprocess.run([sys.executable, CERT_WIN, "lmcert", action], capture_output=True,
                           text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return 99, "sc_cert_win.py lmcert %s did not finish within 60 s" % action
    return r.returncode, (r.stdout + r.stderr).strip()


def patch():
    """Point the imported sc_start helpers at Windows equivalents and our paths."""
    S.scan = winproc.scan
    S.listening = winproc.listening
    S.stop_proc = stop_proc
    S.stop_pid = stop_pid
    S.run_lmcert = run_lmcert
    S.LOG_DIR = LOG_DIR
    S.CAPTURE_SCRIPT = CAPTURE_SCRIPT
    S.CAPTURE_CMD = [sys.executable, "-u", CAPTURE_SCRIPT]
    S.BRIDGE_SCRIPT = BRIDGE_SCRIPT
    S.BRIDGE_CMD = [sys.executable, "-u", BRIDGE_SCRIPT, "print"]
    S.CAPTURE_NEEDLE = os.path.basename(CAPTURE_SCRIPT).lower()
    S.CAPTURE_NEEDLES = (S.CAPTURE_NEEDLE, "lm_server2.py")  # old name: still stopped as a leftover
    S.SIMHUB_LOG = os.path.join(SIMHUB_DIR, "Logs", "SimHub.txt")
    S.BRIDGE_NEEDLES = tuple(sorted(set(S.ALL_BRIDGE_NEEDLES + (os.path.basename(BRIDGE_SCRIPT).lower(),))))


def cmd_stop():
    os.makedirs(LOG_DIR, exist_ok=True)
    for needles, name in ((S.BRIDGE_NEEDLES, "bridge"), (S.CAPTURE_NEEDLES, "capture server")):
        for pid, cmd in S.scan(*needles):
            S.say("stopping %s (pid %d)" % (name, pid))
            stop_pid(pid, name)
    S.say("done; SimHub and the RSI Launcher left running")


def on_signal(signum, frame):
    raise KeyboardInterrupt


def update_gate():
    """Step 0 (see the docstring). -> True when an update applied in this start changed what
    reaches the motion platform."""
    try:
        import scm_update  # noqa: E402  (installed next to this file)
    except Exception as e:  # noqa: BLE001
        S.say("update check skipped (%s)" % e)
        return False
    return scm_update.gate("windows", _CFG.get("repo"), HOME, S.say,
                           game_running=lambda: bool(S.scan(S.GAME_EXE)),
                           simhub_running=lambda: bool(S.scan(S.SIMHUB_NEEDLE)),
                           install_dir=HERE, simhub_dir=SIMHUB_DIR)


def main():
    signal.signal(signal.SIGTERM, on_signal)
    os.makedirs(LOG_DIR, exist_ok=True)
    S.say("Star Citizen motion start-up (Windows)")
    if RIG_PAUSE and not sys.stdin.isatty():
        S.fail("the rig pause needs a console for the Enter key")
    S.STATE["motion_changed"] = update_gate()

    blockers = S.scan(*S.BLOCKER_NEEDLES)
    if blockers:
        for pid, cmd in blockers:
            print("  pid %d: %s" % (pid, cmd))
        S.fail("a recorder or old bridge is running (listed above). Stop it first.")
    for p in (CAPTURE_SCRIPT, BRIDGE_SCRIPT):
        if not os.path.exists(p):
            S.fail("missing %s" % p)
    pkt, _, layout_text = S.bridge_layout()
    if pkt is None:
        S.fail("SimHub game definition: %s. Run the installer again (py -3 setup\\windows\\sc_install_win.py "
               "apply) with SimHub closed" % layout_text)
    S.say("bridge layout: " + layout_text)

    S.STATE["cert_ok"] = S.cert_gate("check", "check")
    game_running = bool(S.scan(S.GAME_EXE))
    simhub_running = bool(S.scan(S.SIMHUB_NEEDLE))
    if not game_running and not os.path.exists(RSI_LAUNCHER):
        S.fail("RSI Launcher not found: %s (set SC_RSI_LAUNCHER)" % RSI_LAUNCHER)
    S.say("game already running: %s   SimHub already running: %s" % (
        "yes" if game_running else "no", "yes" if simhub_running else "no"))

    for needles, name in ((S.BRIDGE_NEEDLES, "bridge"), (S.CAPTURE_NEEDLES, "capture server")):
        for pid, cmd in S.scan(*needles):
            S.say("stopping leftover %s (pid %d)" % (name, pid))
            stop_pid(pid, name)
    busy_tcp = S.listening("tcp", S.CAPTURE_PORT_TCP)
    busy_udp = S.listening("udp", S.BRIDGE_PORT_UDP)
    if busy_tcp or busy_udp:
        print(busy_tcp)
        print(busy_udp)
        who = winproc.owner("tcp", S.CAPTURE_PORT_TCP) or winproc.owner("udp", S.BRIDGE_PORT_UDP)
        S.fail("port %d/tcp or %d/udp is in use by %s. If that is D-BOX's Motion Engine, see "
               "docs/INSTALL-WINDOWS.md (port 61666)" % (S.CAPTURE_PORT_TCP, S.BRIDGE_PORT_UDP, who or "another program"))

    srv, srv_log = S.start_logged(S.CAPTURE_CMD, "capture server", "capture_server.log")
    S.STATE["server"] = srv
    S.wait_for(lambda: S.listening("tcp", S.CAPTURE_PORT_TCP), srv, "capture server", srv_log, 15)
    S.say("capture server listening on 127.0.0.1:%d" % S.CAPTURE_PORT_TCP)
    if not S.cert_gate("selftest", "self-test"):
        S.STATE["cert_ok"] = False

    brg, brg_log = S.start_logged(S.BRIDGE_CMD, "bridge", "bridge.log")
    S.STATE["bridge"] = brg
    S.wait_for(lambda: S.listening("udp", S.BRIDGE_PORT_UDP), brg, "bridge", brg_log, 10)
    S.say("bridge listening on UDP %d" % S.BRIDGE_PORT_UDP)

    if simhub_running:
        S.say("self-test of bridge output skipped: SimHub already owns UDP %d" % S.SIMHUB_PORT_UDP)
    else:
        ok, msg = S.selftest(expect_zero=not game_running)
        if ok is False:
            S.fail("bridge self-test failed: " + msg)
        S.say(("bridge self-test passed: " if ok else "bridge self-test: ") + msg)

    dll = os.path.join(SIMHUB_DIR, "SCHaptics.dll")
    if os.path.exists(dll):
        S.say("SC Haptics plugin installed (SCHaptics.dll)")
    else:
        S.say("WARNING: SC Haptics plugin not installed in SimHub (no %s); motion works, shaker "
              "effects will not." % dll)
    if simhub_running:
        S.say("SimHub is already running; not starting another one")
    else:
        if not os.path.exists(SIMHUB_EXE):
            S.fail("SimHub not found: %s (set SC_SIMHUB_DIR)" % SIMHUB_EXE)
        subprocess.Popen([SIMHUB_EXE], cwd=os.path.dirname(SIMHUB_EXE), creationflags=DETACHED,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end = time.monotonic() + 60
        while time.monotonic() < end and not S.scan(S.SIMHUB_NEEDLE):
            time.sleep(1)
        if not S.scan(S.SIMHUB_NEEDLE):
            S.fail("SimHub did not appear within 60 s")
        S.say("SimHub started (it keeps running after this script ends)")
        time.sleep(5)   # let SimHub log which game it loaded

    active = S.simhub_active_game()
    if active is None:
        S.say("could not read SimHub's active game from its log; make sure Star Citizen is selected in SimHub")
    elif S.SIMDEF_UUID not in active:
        S.say("WARNING: SimHub's active game is %s, not Star Citizen. SimHub will wait for that game's "
              "data and the rig gets nothing. Select Star Citizen in SimHub's game list." % active)
    else:
        S.say("SimHub's active game: Star Citizen")

    if S.STATE.get("motion_changed"):
        S.motion_changed_reminder()
    if RIG_PAUSE:
        print()
        print("  Rig start-up (no motion rig: just press Enter):")
        print("   - E-stop within reach")
        print("   - follow your hardware's start order (e.g. motion power OFF while SimHub")
        print("     starts and connects to the controller, then ON)")
        print("   - SimHub shows your motion controller as connected (not stuck on 'Starting')")
        input("  Press Enter when the rig is powered on and SimHub shows it connected (Ctrl-C to stop): ")

    if game_running:
        S.say("game already running; not launching it again")
    else:
        subprocess.Popen([RSI_LAUNCHER], cwd=os.path.dirname(RSI_LAUNCHER), creationflags=DETACHED,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        S.say("started the RSI Launcher; launch Star Citizen from it")

    S.say("motion pipeline running. It stops %d s after Star Citizen closes, or press Ctrl-C."
          % AUTO_STOP_AFTER_GAME)
    if not S.STATE["cert_ok"]:
        S.say("REMINDER: a certificate check failed at start-up; motion telemetry may be disabled. "
              "If the game does not connect: py -3 sc_cert_win.py lmcert status")
    warned = False
    seen = game_running
    stop_at = None
    while True:
        time.sleep(5)
        if not warned and (brg.poll() is not None or srv.poll() is not None):
            which = "bridge" if brg.poll() is not None else "capture server"
            S.say("WARNING: the %s has stopped; motion output has stopped. Start this again to "
                  "restart it (the game keeps running)." % which)
            warned = True
        now = bool(S.scan(S.GAME_EXE))
        if now:
            if stop_at is not None:
                S.say("Star Citizen started again; stop cancelled")
            seen, stop_at = True, None
        elif seen:
            if stop_at is None:
                stop_at = time.monotonic() + AUTO_STOP_AFTER_GAME
                S.say("Star Citizen has closed. Stopping the pipeline in %d s; start the game "
                      "again to cancel." % AUTO_STOP_AFTER_GAME)
            elif time.monotonic() >= stop_at:
                break
    S.say("game has exited")


if __name__ == "__main__":
    patch()
    if sys.argv[1:] == ["stop"]:
        cmd_stop()
        sys.exit(0)
    if sys.argv[1:]:
        sys.exit(__doc__)
    try:
        main()
    except KeyboardInterrupt:
        print()
        S.say("interrupted")
    finally:
        S.cleanup()
        S.say("bridge and capture server stopped; SimHub and the RSI Launcher left running")
        if os.environ.get("SC_START_HOLD") == "1" and sys.stdin.isatty():
            try:
                input("Press Enter to close this window. ")
            except (EOFError, KeyboardInterrupt):
                pass
