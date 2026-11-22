"""scm_control.py - the engine behind the control window (gui/sc_control.py). No GUI code here,
so all of it can be tested without a display.

It drives the same launcher (scripts/sc_start.py, imported unchanged) step by step instead of
from a terminal, in the same order and with the same checks:
  pipeline   update check (the window asks first) -> blockers -> scripts -> SimHub's game
             definition -> certificate check -> leftovers stopped -> capture server -> TLS
             self-test -> bridge -> bridge self-test (only while SimHub is NOT running, so
             nothing reaches the rig) -> add-ons -> SimHub (never a second one)
  game       the RSI Launcher starts only when the bridge runs and answers, SimHub runs (not
             stuck), the game is not already running, and you confirmed the rig check
             (E-stop, your hardware's start order, SimHub connected). This replaces the
             terminal's "press Enter".
  after      when StarCitizen.exe closes, the launcher and Wine in the game prefix are closed
             after 30 s (cancellable), like the terminal launcher.
Platforms
  linux      the launcher is scripts/sc_start.py of this repository; SimHub runs under Wine and
             the game through LUG-Helper's sc-launch.sh.
  windows    the launcher is the installed %USERPROFILE%\\sc-motion\\sc_start_win.py (installer
             step 6), whose patch() points sc_start's helpers at Windows (processes, ports,
             certificate tool, SimHub detection; no Wine). SimHub and the RSI Launcher are
             started directly. The window itself runs from the repository folder, the one the
             updates keep current.
Problems are written to the project error log (scm_errlog) by the launcher's own functions:
children's tracebacks, a child that stops, a failed step.

State is read with snapshot() (a copy, thread-safe). Actions run one at a time in a worker
thread (run_task); a second action while one runs is refused with a message.
"""
import collections
import copy
import glob
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
SCRIPTS = os.path.join(REPO, "scripts")
COMMON = os.path.join(REPO, "setup", "common")
HOME = os.path.expanduser("~")
# SC_CONTROL_PLATFORM: tests only (exercise the Windows path on Linux with stand-ins)
PLATFORM = os.environ.get("SC_CONTROL_PLATFORM") or ("windows" if os.name == "nt" else "linux")
if PLATFORM == "windows":
    SETTINGS_PATH = os.path.join(os.environ.get("APPDATA") or os.path.join(HOME, "AppData", "Roaming"),
                                 "sc-motion", "control.json")
else:
    SETTINGS_PATH = os.path.join(HOME, ".config", "sc-motion", "control.json")
# Windows: where the installer copied the runtime files (installer step 6)
INSTALL_DIR = os.environ.get("SC_SCM_INSTALL_DIR", os.path.join(HOME, "sc-motion"))
SETTINGS = {"autostart": True, "start_simhub": True, "check_updates": True, "mfd_source": "~/SC-Motion-MFD",
            "start_launcher": True, "wine_cleanup_on_close": False, "simhub_rig_check": True}
HB_STALL = 5.0          # seconds without a bridge status line while the bridge runs = not responding
TASK_NAMES = {"start": "starting the motion pipeline", "stop": "stopping the motion pipeline",
              "simhub": "starting SimHub", "simhub-restart": "ending the stuck SimHub and starting it again",
              "game": "starting the RSI Launcher", "repair": "repairing", "wine": "cleaning up Wine",
              "update-check": "checking for updates", "update": "updating"}


def load_settings(path=SETTINGS_PATH):
    s = dict(SETTINGS)
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        for k, v in saved.items():
            if k in SETTINGS and isinstance(v, type(SETTINGS[k])):
                s[k] = v
    except (OSError, ValueError, AttributeError):
        pass
    return s


def save_settings(s, path=SETTINGS_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({k: s[k] for k in SETTINGS if k in s}, f, indent=1)
    os.replace(tmp, path)


def load_registry(dev, profile, path=None, platform=None):
    """The window's cards (gui/panels.json) -> [entry] in order: the public cards, then (developer
    mode) the profile's cards, each placed after the card its "after" names. Entries whose module
    file is missing, or whose "platforms" list does not name this platform, are dropped. No Qt
    here, so sc_control.py --check works without PySide6."""
    platform = platform or PLATFORM
    with open(path or os.path.join(HERE, "panels.json"), encoding="utf-8") as f:
        entries = [dict(e) for e in json.load(f)["panels"] if e.get("tier", "public") == "public"
                   and platform in e.get("platforms", (platform,))]
    if dev and profile is not None:
        for e in getattr(profile, "PANELS", []):
            e = dict(e, tier="dev")
            ids = [x["id"] for x in entries]
            pos = ids.index(e["after"]) + 1 if e.get("after") in ids else len(entries)
            entries.insert(pos, e)
    out = []
    for e in entries:
        p = e["module"] if os.path.isabs(e["module"]) else os.path.join(REPO, e["module"])
        if os.path.isfile(p):
            out.append(dict(e, path=p))
    return out


class LauncherMissing(Exception):
    """The Windows launcher is not installed (no %USERPROFILE%\\sc-motion\\sc_start_win.py)."""


def load_launcher(profile=None, platform=None):
    """Linux: import scripts/sc_start.py (unchanged). Windows: import the installed
    sc_start_win.py and let it patch sc_start for Windows. A profile may adjust it (developer tee)."""
    platform = platform or PLATFORM
    if platform == "windows":
        if not os.path.isfile(os.path.join(INSTALL_DIR, "sc_start_win.py")):
            raise LauncherMissing("the motion pipeline is not installed in %s: run setup\\windows\\Install-SCMotion.ps1 "
                                  "-Apply from this repository folder first" % INSTALL_DIR)
        if INSTALL_DIR in sys.path:
            sys.path.remove(INSTALL_DIR)
        sys.path.insert(0, INSTALL_DIR)    # the installed copies first (sc_start, winproc, scm_*)
        import sc_start_win
        sc_start = sc_start_win.for_control_window()
        if profile is not None and hasattr(profile, "configure_launcher"):
            sc_start = profile.configure_launcher(sc_start) or sc_start
        return sc_start
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)
    import sc_start
    if profile is not None and hasattr(profile, "configure_launcher"):
        sc_start = profile.configure_launcher(sc_start) or sc_start
    return sc_start


# ------------------------------------------------------------------ bridge heartbeat
LOG_TIME = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d{3}) (.*)$")
STATUS_RE = re.compile(r"^active=(\d) seat=(\d) g=([-\d.]+)")
TEE_RE = re.compile(r"^tee: rx=(\d+)")


def _line_time(line):
    m = LOG_TIME.match(line)
    if not m:
        return None, line
    try:
        t = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")) + int(m.group(2)) / 1000.0
    except ValueError:
        return None, m.group(3)
    return t, m.group(3)


def heartbeat(path, now=None, tail_bytes=24000):
    """Read the end of the bridge log. -> {"seen": bool, "age", "active", "seated", "rx", "rx_rate",
    "seq": time of the last status line}. The bridge prints a status line every second ("print"
    mode) and the tee a packet count every 10 s."""
    now = time.time() if now is None else now
    hb = {"seen": False, "age": None, "active": False, "seated": False, "rx": None, "rx_rate": None, "seq": None}
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - tail_bytes))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return hb
    tees = []
    for line in reversed(lines):
        lm = LOG_TIME.match(line)
        if not lm:
            continue
        text = lm.group(3)
        ms = STATUS_RE.match(text) if hb["seq"] is None else None
        mt = TEE_RE.match(text) if len(tees) < 2 else None
        if not ms and not mt:
            continue                      # the time is parsed only for the lines that are used
        t, _ = _line_time(line)
        if t is None:
            continue
        if ms:
            hb.update(seen=True, seq=t, age=max(0.0, now - t), active=ms.group(1) == "1", seated=ms.group(2) == "1")
        if mt:
            tees.append((t, int(mt.group(1))))
        if hb["seq"] is not None and len(tees) >= 2:
            break
    if tees:
        hb["rx"] = tees[0][1]
        if len(tees) == 2 and tees[0][0] > tees[1][0] and tees[0][1] >= tees[1][1]:
            hb["rx_rate"] = round((tees[0][1] - tees[1][1]) / (tees[0][0] - tees[1][0]), 1)
    return hb


def bridge_light(alive, hb, now_ok=True):
    """-> (light, text) for the heartbeat indicator."""
    if not alive:
        return "off", "not running"
    if not hb["seen"] or hb["age"] is None:
        return "amber", "running; waiting for its first status line"
    if hb["age"] > HB_STALL:
        return "red", "NOT RESPONDING: no status line for %.0f s" % hb["age"]
    rate = " (%s packets/s from the game)" % hb["rx_rate"] if hb.get("rx_rate") else ""
    if hb["active"]:
        return "green", "telemetry flowing%s; %s" % (rate, "seated" if hb["seated"] else "not seated (motion gated)")
    return "blue", "running and answering; no game telemetry yet" + rate


def mtime(path):
    """Modification time, or 0 for a file that was removed meanwhile (logs come and go)."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _gone(pid):
    """The process has exited (a zombie counts as gone)."""
    if not os.path.isdir("/proc"):
        return True                        # only used for Wine orphans, which Windows never has
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except (OSError, IndexError):
        return True


# ------------------------------------------------------------------ the engine
class Control:
    def __init__(self, sc, platform="linux", settings=None, profile=None, clock=time.time):
        self.sc = sc
        self.platform = platform
        self.settings = settings if settings is not None else load_settings()
        self.profile = profile
        self.clock = clock
        self.lock = threading.RLock()
        self.lines = collections.deque(maxlen=2000)
        self.nline = 0
        self.task = None
        self.stop_event = threading.Event()
        self.monitor_thread = None
        self.child_warned = set()
        self.addons_warned = set()
        self.game_seen = False
        self.st = {
            "pipeline": "idle",          # idle | starting | running | needs_decision | failed | stopped
            "pipeline_detail": "",
            "steps": [],                 # [(name, "ok"|"warn"|"fail"|"run"|"decide", text)]
            "decision": None,            # {"kind": "cert", "text": ...}
            "cert": {"check": None, "selftest": None, "text": "not checked yet", "light": "off", "override": False},
            "bridge": {"light": "off", "text": "not running", "alive": False, "external": [], "hb": {}},
            "server": {"alive": False},
            "selftest": None,
            "simhub": {"state": "none", "pids": [], "text": "not running", "light": "off"},
            "game": {"game": False, "launcher": False, "text": "not running", "light": "off"},
            "addons": [],
            "rig_confirmed": False,
            "wine": None,                 # last Wine clean-up: {"ok", "text", "time"}
            "motion_changed": False,
            "repair": None,
            "updates": None,              # list of app records (public view) after a check
            "task": None,
        }
        self._updates = []
        sc.say = self.say
        sc.ESPEAK = None              # no spoken messages: everything is shown in the window

    # ---- output
    def say(self, text, speak=False):  # noqa: ARG002  speak: accepted from sc_start's calls, never spoken
        line = "%s %s" % (time.strftime("%H:%M:%S"), text)
        with self.lock:
            self.lines.append(line)
            self.nline += 1
        try:
            print(line, flush=True)
        except (OSError, ValueError):
            pass

    def lines_since(self, n):
        """-> (new count, [lines after n])."""
        with self.lock:
            total = self.nline
            k = min(total - n, len(self.lines))
            return total, list(self.lines)[-k:] if k > 0 else []

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.st)

    def _set(self, **kw):
        with self.lock:
            self.st.update(kw)

    def _step(self, name, status, text):
        with self.lock:
            steps = [s for s in self.st["steps"] if s[0] != name]
            steps.append((name, status, text))
            self.st["steps"] = steps
        self.say("%s: %s" % (name, text))

    # ---- tasks
    def busy(self):
        return self.task is not None

    def run_task(self, name, fn, *args, **kw):
        """Run fn in a worker thread. -> False (with a message) if another task runs."""
        with self.lock:
            if self.stop_event.is_set():
                self.say("not started (%s): the window is closing" % TASK_NAMES.get(name, name))
                return False
            if self.task is not None:
                self.say("busy (%s); try again when it has finished" % TASK_NAMES.get(self.task, self.task))
                return False
            self.task = name
            self.st["task"] = TASK_NAMES.get(name, name)

        def body():
            try:
                fn(*args, **kw)
            except Exception as e:  # noqa: BLE001  recorded, the window keeps running
                self.say("ERROR while %s: %s: %s" % (TASK_NAMES.get(name, name), type(e).__name__, e))
                EL = getattr(self.sc, "EL", None)
                if EL is not None and EL.current() is not None:
                    EL.report("crash", e)
            finally:
                with self.lock:
                    self.task = None
                    self.st["task"] = None
        threading.Thread(target=body, daemon=True, name="sc-control-" + name).start()
        return True

    def wait_idle(self, timeout=None):
        end = None if timeout is None else time.time() + timeout
        while self.task is not None and (end is None or time.time() < end):
            time.sleep(0.05)
        return self.task is None

    # ---- probing (cheap, the monitor calls it every 2 s)
    def probe(self):
        sc = self.sc
        now = self.clock()
        game = bool(sc.scan(sc.GAME_EXE))
        # Each scan walks every process. While the game runs nothing uses the launcher state (the
        # card and the close-after-game read it only once the game is gone), so those walks are
        # skipped for the whole flight.
        lpids = [] if game else sorted({p for p, _ in sc.scan(sc.LAUNCHER_EXE)} | set(sc.gamescope_for_launcher()))
        launcher = True if game else bool(lpids)
        if game:
            g = ("green", "Star Citizen is running")
        elif launcher and self.platform == "windows":
            g = ("blue", "RSI Launcher is open, the game is not running (pid %s)" % ", ".join(map(str, lpids[:4])))
        elif launcher:
            g = ("blue", "RSI Launcher is open, the game is not running (pid %s). No launcher window? "
                         "Restart it, or end it with Wine clean-up." % ", ".join(map(str, lpids[:4])))
        else:
            g = ("off", "not running")
        sh_state, pids = sc.simhub_state()
        if sh_state == "running" and len(pids) > 1:
            s = ("amber", "%d SimHub processes are running (pids %s); one can hold the plugin's port" % (len(pids), ", ".join(map(str, pids))))
        elif sh_state == "running":
            s = ("green", "running (pid %s)" % pids[0])
        elif sh_state == "stuck":
            s = ("red", "ORPHAN: SimHubWPF.exe (pid %s) is listed but its Wine server is gone; it has no window "
                        "and does nothing" % ", ".join(map(str, pids)))
        else:
            s = ("off", "not running")
        brg = sc.STATE.get("bridge")
        srv = sc.STATE.get("server")
        ours = brg is not None and brg.poll() is None
        external = [] if ours else [pid for pid, _ in sc.scan(*sc.BRIDGE_NEEDLES)]
        alive = ours or bool(external)
        hb = heartbeat(os.path.join(sc.LOG_DIR, "bridge.log"), now)
        light, text = bridge_light(alive, hb)
        if external:
            text += " (started outside this window, pid %s)" % ", ".join(map(str, external))
        addons = [(name, proc.poll() is None) for proc, name, _log in sc.STATE.get("addons", [])]
        with self.lock:
            self.st["game"] = {"game": game, "launcher": launcher, "light": g[0], "text": g[1]}
            self.st["simhub"] = {"state": sh_state, "pids": pids, "light": s[0], "text": s[1]}
            self.st["bridge"] = {"light": light, "text": text, "alive": alive, "ours": ours, "external": external, "hb": hb}
            self.st["server"] = {"alive": srv is not None and srv.poll() is None}
            self.st["addons"] = addons
        return self.snapshot()

    def watch_children(self):
        """Children that stopped by themselves are recorded once (error log)."""
        sc = self.sc
        if self.st["pipeline"] != "running":
            return
        for key, name, log in (("bridge", "bridge", "bridge.log"), ("server", "capture server", "capture_server.log")):
            p = sc.STATE.get(key)
            if p is not None and p.poll() is not None and key not in self.child_warned:
                self.child_warned.add(key)
                self.say("WARNING: the %s has stopped (exit code %s); motion output has stopped. Use Restart "
                         "motion in this window (the game can keep running)." % (name, p.returncode))
                sc.child_stopped(p, name, os.path.join(sc.LOG_DIR, log))
                self._set(pipeline="failed", pipeline_detail="the %s stopped" % name)
        self.addons_warned = sc.check_addons(self.addons_warned)

    def game_watch(self):
        """Follows the game for the rig check: once the game has run and the launcher is closed,
        the next launch asks for the rig check again. The window never closes the launcher or Wine."""
        g = self.st["game"]
        if g["game"]:
            self.game_seen = True
        elif self.game_seen and not g["launcher"]:
            self.game_seen = False
            self._set(rig_confirmed=False)

    # ---- Wine clean-up (sc_start.py WINE CLEAN-UP): on request only, never by itself
    def wine_supported(self):
        return callable(getattr(self.sc, "wine_leftovers", None)) and callable(getattr(self.sc, "wine_cleanup", None))

    def wine_scan(self):
        """-> {"game": info, "simhub": info} (see sc_start.wine_leftovers), or {} with an older launcher."""
        if not self.wine_supported():
            return {}
        out = {}
        for part in ("game", "simhub"):
            try:
                out[part] = self.sc.wine_leftovers(part)
            except Exception as e:  # noqa: BLE001  one unreadable part must not hide the other
                out[part] = {"end": [], "keep": [], "blocked": "could not be checked (%s)" % e}
        return out

    def wine_cleanup(self, parts):
        """End leftover Wine of parts ("game", "simhub"). The caller has asked first (and, for
        SimHub, had the motion power switched off). -> True when nothing is left."""
        if not self.wine_supported():
            self.say("Wine clean-up needs the updated sc_start.py")
            return False
        ok, lines = True, []
        for part in parts:
            label = "game prefix" if part == "game" else "SimHub's prefix"
            try:
                done = bool(self.sc.wine_cleanup(part))
            except Exception as e:  # noqa: BLE001
                self.say("Wine clean-up of the %s failed: %s" % (label, e))
                done = False
            ok = ok and done
            lines.append("%s: %s" % (label, "clean" if done else "NOT clean (see the messages)"))
        self._set(wine={"ok": ok, "text": "; ".join(lines), "time": time.time()})
        self.probe()
        return ok

    def start_monitor(self, every=2.0):
        def loop():
            while not self.stop_event.is_set():
                try:
                    self.probe()
                    self.watch_children()
                    self.game_watch()
                except Exception as e:  # noqa: BLE001
                    self.say("status check failed (%s: %s)" % (type(e).__name__, e))
                self.stop_event.wait(every)
        self.monitor_thread = threading.Thread(target=loop, daemon=True, name="sc-control-monitor")
        self.monitor_thread.start()

    # ---- the pipeline
    def _fail(self, msg, trace=""):
        self._step("stopped", "fail", msg)
        EL = getattr(self.sc, "EL", None)
        if EL is not None and EL.current() is not None:
            EL.report("error", text=msg, trace=trace)
        try:
            self.sc.cleanup()
        except Exception as e:  # noqa: BLE001  the state below must still be set
            self.say("cleanup after the failure did not finish (%s: %s)" % (type(e).__name__, e))
        self._set(pipeline="failed", pipeline_detail=msg)
        return False

    def _wait(self, check, proc, name, logname, timeout):
        end = time.monotonic() + timeout
        log = os.path.join(self.sc.LOG_DIR, logname)
        while time.monotonic() < end:
            if proc.poll() is not None:
                t = self.sc.tail(log)
                return self._fail("%s exited during start-up (code %s); log %s" % (name, proc.returncode, log),
                                  "last lines of %s:\n%s" % (log, t))
            if check():
                return True
            time.sleep(0.25)
        t = self.sc.tail(log)
        return self._fail("%s did not become ready within %d s; log %s" % (name, timeout, log),
                          "last lines of %s:\n%s" % (log, t))

    def cert_check(self):
        rc, out = self.sc.run_lmcert("check")
        last = out.splitlines()[-1] if out else ""
        if rc is None:
            light, text = "amber", "not checked (%s)" % out
        elif rc == 0:
            light, text = "green", "installed correctly: " + last
        elif rc == 9:
            light, text = "amber", "WARN: " + last
        else:
            light, text = "red", "FAIL (sc_lmcert exit %s): %s" % (rc, last)
        with self.lock:
            self.st["cert"].update(check=rc, text=text, light=light, out=out)
        return rc

    def start_pipeline(self, override_cert=False):
        """The start-up steps. An unexpected error in any step stops what was started (bridge,
        capture server, add-ons) and leaves the pipeline "failed", never "starting"."""
        try:
            return self._start_pipeline(override_cert)
        except Exception as e:  # noqa: BLE001
            import traceback
            trace = traceback.format_exc()
            if self.st["pipeline"] == "running":
                # The bridge is up and answering; only a later step (SimHub) failed. Motion keeps
                # running: stopping it here would be worse than the failed step.
                self._step("after start", "warn", "%s: %s (motion keeps running)" % (type(e).__name__, e))
                EL = getattr(self.sc, "EL", None)
                if EL is not None and EL.current() is not None:
                    EL.report("error", text="control window: a step after the bridge start failed: %s" % e, trace=trace)
                return True
            return self._fail("start-up stopped by an error: %s: %s" % (type(e).__name__, e), trace)

    def _start_pipeline(self, override_cert=False):
        sc = self.sc
        self.child_warned = set()
        self._set(pipeline="starting", pipeline_detail="", steps=[], decision=None, selftest=None)
        os.makedirs(sc.LOG_DIR, exist_ok=True)
        blockers = sc.scan(*sc.BLOCKER_NEEDLES)
        if blockers:
            return self._fail("a recorder, replay tool or old bridge is running: %s. Stop it first." %
                              "; ".join("pid %d %s" % b for b in blockers))
        for p in (sc.CAPTURE_SCRIPT, sc.BRIDGE_SCRIPT, sc.BRIDGE_CMD[2]):
            if not os.path.exists(p):
                return self._fail("missing %s" % p)
        pkt, _, layout = sc.bridge_layout()
        if pkt is None:
            return self._fail("SimHub game definition: " + layout)
        self._step("layout", "ok", layout)
        rc = self.cert_check()
        cert_ok = rc in (0, 9, None)
        if not cert_ok and not override_cert:
            self._step("certificate", "decide", self.st["cert"]["text"])
            self._set(pipeline="needs_decision", decision={
                "kind": "cert", "text": "The certificate check failed: the game will probably reject the capture "
                                        "server, so motion telemetry and shaker effects may be disabled.\n\n%s\n\n"
                                        "Repair puts your working certificate back (backups first; game closed). "
                                        "Continue starts with motion possibly disabled." % self.st["cert"]["text"]})
            self.say("CERTIFICATE CHECK FAILED: choose Repair or Continue in the window")
            return False
        with self.lock:
            self.st["cert"]["override"] = not cert_ok
        self._step("certificate", "ok" if rc == 0 else "warn", self.st["cert"]["text"])
        for needles, name in ((sc.BRIDGE_NEEDLES, "bridge"), (sc.CAPTURE_NEEDLES, "capture server")):
            for pid, _cmd in sc.scan(*needles):
                self.say("stopping leftover %s (pid %d)" % (name, pid))
                sc.stop_pid(pid, name)
        addons = sc.addon_commands()
        for pid, name in sc.addon_leftovers(addons):
            self.say("stopping leftover add-on %s (pid %d)" % (name, pid))
            sc.stop_pid(pid, "add-on %s" % name)
        busy = [x for x in (sc.listening("tcp", sc.CAPTURE_PORT_TCP), sc.listening("udp", sc.BRIDGE_PORT_UDP)) if x]
        if busy:
            return self._fail("port %d/tcp or %d/udp is still in use by something else: %s" % (
                sc.CAPTURE_PORT_TCP, sc.BRIDGE_PORT_UDP, "; ".join(busy)))
        srv, _ = sc.start_logged(sc.CAPTURE_CMD, "capture server", "capture_server.log")
        sc.STATE["server"] = srv
        if not self._wait(lambda: sc.listening("tcp", sc.CAPTURE_PORT_TCP), srv, "capture server", "capture_server.log", 15):
            return False
        self._step("capture server", "ok", "listening on 127.0.0.1:%d" % sc.CAPTURE_PORT_TCP)
        rc, out = sc.run_lmcert("selftest")
        last = out.splitlines()[-1] if out else ""
        with self.lock:
            self.st["cert"]["selftest"] = rc
        if rc not in (0, None):
            if not override_cert:
                self.sc.cleanup()
                self._step("TLS self-test", "decide", "FAIL: %s" % last)
                self._set(pipeline="needs_decision", decision={
                    "kind": "cert", "text": "The TLS self-test failed: the capture server does not validate against "
                                            "the certificate the game trusts.\n\n%s" % last})
                return False
            self._step("TLS self-test", "warn", "FAIL (continuing as you chose): %s" % last)
        else:
            self._step("TLS self-test", "ok" if rc == 0 else "warn", last or "skipped")
        brg, _ = sc.start_logged(sc.BRIDGE_CMD, "bridge", "bridge.log")
        sc.STATE["bridge"] = brg
        if not self._wait(lambda: sc.listening("udp", sc.BRIDGE_PORT_UDP), brg, "bridge", "bridge.log", 10):
            return False
        self._step("bridge", "ok", "listening on UDP %d (%s)" % (sc.BRIDGE_PORT_UDP, os.path.basename(sc.BRIDGE_CMD[2])))
        sh_state, _ = sc.simhub_state()
        game = bool(sc.scan(sc.GAME_EXE))
        if sh_state == "running":
            self._step("bridge self-test", "warn", "skipped: SimHub already owns UDP %d" % sc.SIMHUB_PORT_UDP)
            self._set(selftest="skipped")
        else:
            ok, msg = sc.selftest(expect_zero=not game)
            if ok is False:
                return self._fail("bridge self-test failed: " + msg)
            self._step("bridge self-test", "ok" if ok else "warn", msg)
            self._set(selftest="passed" if ok else "skipped")
        sc.start_addons(addons)
        if addons:
            self._step("add-ons", "ok", "started: %s" % ", ".join(n for n, _ in addons))
        self._set(pipeline="running", pipeline_detail="")
        if not os.path.exists(sc.HAPTICS_DLL):
            self._step("SC Haptics plugin", "warn", "not installed in SimHub; motion works, shaker effects will not")
        if self.settings.get("start_simhub", True):
            if sh_state == "none":
                self.start_simhub()
            elif sh_state == "stuck":
                self._step("SimHub", "decide", "an orphan SimHub process is in the way: use End orphan and restart")
            else:
                self._step("SimHub", "ok", "already running; not starting another one")
                self.active_game_check()
        return True

    def stop_pipeline(self, why="stopped from the window"):
        self.sc.cleanup()
        self._set(pipeline="stopped", pipeline_detail=why, steps=[], selftest=None)
        self.say("bridge and capture server stopped (%s); SimHub left running" % why)

    def restart_pipeline(self, override_cert=False):
        self.sc.cleanup()
        return self.start_pipeline(override_cert)

    # ---- SimHub
    def active_game_check(self):
        active = self.sc.simhub_active_game()
        if active is None:
            self._step("SimHub game", "warn", "could not read SimHub's active game; make sure Star Citizen is selected in SimHub")
        elif self.sc.SIMDEF_UUID not in active:
            self._step("SimHub game", "warn", "SimHub's active game is %s, not Star Citizen: select Star Citizen in "
                                              "SimHub's game list, or the rig gets nothing" % active)
            self.say("SimHub's active game is not Star Citizen")
        else:
            self._step("SimHub game", "ok", "Star Citizen")

    def start_simhub(self, settle=5.0):
        sc = self.sc
        state, _ = sc.simhub_state()
        if state == "running":
            self._step("SimHub", "ok", "already running; not starting another one")
            return True
        if state == "stuck":
            self._step("SimHub", "decide", "an orphan SimHub process is in the way: use End orphan and restart")
            return False
        try:
            if self.platform == "windows":
                if not os.path.isfile(sc.SIMHUB_EXE):
                    self._step("SimHub", "fail", "SimHub not found: %s (run Install-SCMotion.ps1 -Apply -Select, "
                                                 "or set SC_SIMHUB_DIR)" % sc.SIMHUB_EXE)
                    return False
                subprocess.Popen([sc.SIMHUB_EXE], cwd=os.path.dirname(sc.SIMHUB_EXE), creationflags=sc.DETACHED,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            else:
                env = dict(os.environ, WINEPREFIX=sc.SIMHUB_PREFIX)
                with open(os.path.join(sc.LOG_DIR, "simhub.log"), "ab") as lf:
                    subprocess.Popen([sc.SIMHUB_WINE, sc.SIMHUB_EXE], env=env, stdout=lf, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            self._step("SimHub", "fail", "could not start SimHub (%s)" % e)
            return False
        end = time.monotonic() + 60
        while time.monotonic() < end and sc.simhub_state()[0] != "running":
            time.sleep(1)
        if sc.simhub_state()[0] != "running":
            self._step("SimHub", "fail", "SimHub did not appear within 60 s" + (
                "" if self.platform == "windows" else " (log %s)" % os.path.join(sc.LOG_DIR, "simhub.log")))
            return False
        self._step("SimHub", "ok", "started (it keeps running when this window closes)")
        time.sleep(settle)
        self.active_game_check()
        return True

    def end_orphan_simhub(self, confirmed):
        """End stuck SimHub processes (you confirmed: cabinet OFF, nobody in the rig), then start SimHub."""
        if not confirmed:
            self.say("not confirmed; the orphan SimHub process was left as it is")
            return False
        state, pids = self.sc.simhub_state()
        if state != "stuck":
            self.say("SimHub is not stuck (%s); nothing ended" % state)
            return False
        for pid in pids:
            for sig, wait in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 2.0)):
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    break
                except PermissionError:
                    self._step("SimHub", "fail", "no permission to end pid %d" % pid)
                    return False
                end = time.monotonic() + wait
                while time.monotonic() < end and not _gone(pid):
                    time.sleep(0.2)
                if _gone(pid):
                    break
        state, left = self.sc.simhub_state()
        if state != "none":
            self._step("SimHub", "fail", "could not end the orphan SimHub process (pid %s)" % ", ".join(map(str, left)))
            return False
        self.say("orphan SimHub process ended")
        return self.start_simhub()

    # ---- the game
    def game_gate(self, rig_confirmed=None, snap=None):  # noqa: ARG002  rig_confirmed: old callers
        """-> [reasons the RSI Launcher cannot start now]; empty = may start. Opening the launcher
        is always safe for the rig (no motion before you are spawned in the game, session 32), so
        only a running game or a missing sc-launch.sh stop it. snap: a snapshot the caller has."""
        s = snap if snap is not None else self.snapshot()
        why = []
        if s["game"]["game"]:
            why.append("Star Citizen is already running")
        if not os.path.exists(self.sc.SC_LAUNCH):
            why.append("missing %s" % self.sc.SC_LAUNCH)
        return why

    def launch_notes(self, snap=None):
        """Things worth knowing before a launch that do not stop it."""
        s = snap if snap is not None else self.snapshot()
        notes = []
        if s["pipeline"] != "running":
            notes.append("the motion bridge is not running: start it before the game for motion and shaker effects")
        elif s["bridge"]["light"] in ("off", "red", "amber"):
            notes.append("the bridge is not answering (%s)" % s["bridge"]["text"])
        if s["simhub"]["state"] != "running":
            notes.append("SimHub is not running" + (" (orphan process)" if s["simhub"]["state"] == "stuck" else ""))
        return notes

    def launch_game(self, rig_confirmed=None):  # noqa: ARG002
        why = self.game_gate(rig_confirmed)
        if why:
            self.say("RSI Launcher not started: " + "; ".join(why))
            return False
        self._set(rig_confirmed=True)
        if self.platform == "windows":
            try:
                p = subprocess.Popen([self.sc.SC_LAUNCH], cwd=os.path.dirname(self.sc.SC_LAUNCH),
                                     creationflags=self.sc.DETACHED, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            except OSError as e:
                self.say("could not start the RSI Launcher (%s)" % e)
                return False
            self.say("started the RSI Launcher (pid %d); launch Star Citizen from it" % p.pid)
        else:
            try:
                p = subprocess.Popen([self.sc.SC_LAUNCH], cwd=self.sc.GAME_PREFIX, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True)
            except OSError as e:
                self.say("could not start sc-launch.sh (%s)" % e)
                return False
            self.say("started sc-launch.sh (pid %d); its own log is sc-launch.log in the game prefix" % p.pid)
        for n in self.launch_notes():
            self.say("note: " + n)
        if not self.st["cert"].get("check") in (0, 9, None):
            self.say("REMINDER: the certificate check failed; motion telemetry may be disabled")
        return True

    # ---- repair
    def repair(self):
        """sc_lmcert.py repair (vault copies back, backups first), then check; starts the pipeline
        again if it was running (or waiting for the certificate decision). The game must be closed
        (sc_lmcert refuses otherwise)."""
        if self.sc.scan(self.sc.GAME_EXE):
            self._set(repair={"rc": 2, "text": "Star Citizen is running: close the game, then repair."})
            self.say("repair refused: the game is running")
            return False
        was = self.st["pipeline"] in ("running", "needs_decision", "failed")
        if was:
            self.sc.cleanup()
            self._set(pipeline="stopped", pipeline_detail="stopped for the repair")
        rc, out = self.sc.run_lmcert("repair")
        self.say("repair (sc_lmcert exit %s):" % rc)
        for line in (out or "").splitlines()[-25:]:
            self.say("  " + line)
        rc2 = self.cert_check()
        ok = rc == 0 and rc2 in (0, 9)
        self._set(repair={"rc": rc, "check": rc2, "ok": ok, "text": out or ""})
        self.say("repair %s; certificate check: %s" % ("done" if ok else "DID NOT FIX IT", self.st["cert"]["text"]))
        if was:
            self.start_pipeline()
        return ok

    # ---- logs and helpers
    def log_sources(self):
        """[(label, path)] of the logs worth opening; a profile can add its own."""
        out = []
        EL = getattr(self.sc, "EL", None)
        if EL is not None:
            for path, _m in EL.list_logs()[:5]:
                out.append(("error log", path))
        for p in glob.glob(os.path.join(self.sc.LOG_DIR, "*.log")):
            out.append(("launch log", p))
        if self.profile is not None and hasattr(self.profile, "log_sources"):
            try:
                out += list(self.profile.log_sources())
            except Exception:  # noqa: BLE001
                pass
        return [(lab, p) for lab, p in out if os.path.isfile(p)]

    def latest_log(self):
        logs = self.log_sources()
        return max(logs, key=lambda lp: mtime(lp[1]))[1] if logs else None

    def open_path(self, path):
        opener = "xdg-open" if sys.platform.startswith("linux") else None
        if not path or not os.path.exists(path):
            self.say("nothing to open (%s)" % path)
            return False
        try:
            if opener:
                subprocess.Popen([opener, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 stdin=subprocess.DEVNULL, start_new_session=True)
            else:
                os.startfile(path)  # noqa: S606  Windows
        except (OSError, AttributeError) as e:
            self.say("could not open %s (%s)" % (path, e))
            return False
        return True

    def mfd_info(self):
        """-> {"installed": bool, "version", "dir", "configurator": [argv] or None}."""
        if COMMON not in sys.path:
            sys.path.append(COMMON)
        try:
            import scm_addons
            st = scm_addons.installed().get("mfd")
        except Exception:  # noqa: BLE001
            st = None
        if not st:
            return {"installed": False}
        d = scm_addons.version_dir("mfd", st["current"])
        main = os.path.join(d, "sc_mfd.py")
        return {"installed": True, "version": st["current"], "dir": d,
                "configurator": [sys.executable, main, "edit"] if os.path.isfile(main) else None}

    def open_mfd_configurator(self):
        info = self.mfd_info()
        if not info.get("configurator"):
            self.say("the MFD add-on is not installed (or its sc_mfd.py is missing)")
            return False
        try:
            subprocess.Popen(info["configurator"], cwd=info["dir"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            self.say("could not start the MFD configurator (%s)" % e)
            return False
        self.say("MFD configurator started (MFD %s)" % info["version"])
        return True

    def _mfd_cli(self, *args, timeout=10):
        """Run the installed MFD's sc_mfd.py with args. -> (code, stdout, stderr) or (None, "", why)."""
        info = self.mfd_info()
        main = os.path.join(info.get("dir", ""), "sc_mfd.py")
        if not info.get("installed") or not os.path.isfile(main):
            return None, "", "the MFD add-on is not installed"
        try:
            r = subprocess.run([sys.executable, main] + list(args), cwd=info["dir"], capture_output=True, text=True,
                               timeout=timeout, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            return None, "", str(e)
        return r.returncode, r.stdout, r.stderr

    def mfd_surfaces(self):
        """-> [{"name", "type", "enabled", ...}] from the MFD's own settings, or None."""
        code, out, _err = self._mfd_cli("surfaces", "--json")
        if code != 0:
            return None
        try:
            rows = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return None
        return rows if isinstance(rows, list) else None

    def mfd_set_surface(self, name, on):
        """Switch one MFD surface on/off in the MFD's settings; a running MFD follows by itself."""
        code, out, err = self._mfd_cli("surfaces", "--set", "%s=%s" % (name, "on" if on else "off"))
        self.say((out or err).strip().splitlines()[-1] if (out or err).strip() else "MFD surfaces: no answer")
        return code == 0

    def mfd_process(self):
        """The MFD add-on process this window started -> (proc or None, running)."""
        for proc, name, _log in self.sc.STATE.get("addons", []):
            if name == "mfd":
                return proc, proc.poll() is None
        return None, False

    def mfd_restart(self):
        """Restart the MFD without touching motion. Running: it restarts its surfaces in place (SIGHUP:
        same process, same telemetry port and virtual keyboard). Stopped while motion runs: started
        again the way the launcher starts it. -> True when something was done."""
        sc = self.sc
        proc, running = self.mfd_process()
        if running:
            if not hasattr(signal, "SIGHUP"):
                self.say("Restart MFD: not available on this system; stop and start motion instead")
                return False
            try:
                os.kill(proc.pid, signal.SIGHUP)
            except OSError as e:
                self.say("Restart MFD: could not signal the MFD (%s)" % e)
                return False
            self.say("MFD: restarting its surfaces (motion, SimHub and the bridge are not touched)")
            return True
        if self.st["pipeline"] != "running":
            self.say("Restart MFD: motion is not running; the MFD starts with it (it gets its game data from the bridge)")
            return False
        cmds = [c for c in sc.addon_commands() if c[0] == "mfd"]
        if not cmds:
            self.say("Restart MFD: the MFD add-on is not set up to start with the bridge (its tap file is missing)")
            return False
        left = sc.addon_leftovers(cmds)
        if left:
            self.say("Restart MFD: an MFD is already running outside this window (pid %s); asking it to restart"
                     % ", ".join(str(pid) for pid, _n in left))
            code, _out, _err = self._mfd_cli("restart")
            return code == 0
        sc.STATE["addons"] = [a for a in sc.STATE.get("addons", []) if a[1] != "mfd"]
        sc.start_addons(cmds)
        self.addons_warned.discard("mfd")
        self.say("MFD started again (motion, SimHub and the bridge are not touched)")
        return True

    # ---- updates
    def check_updates(self):
        import scm_appupdate as AU
        recs = AU.check_all(self.platform, self.settings)
        self._updates = recs
        self._set(updates=[AU.public(r) for r in recs])
        for r in recs:
            if r.get("update"):
                self.say("update available: %s %s -> %s%s" % (r["title"], r["current"], r["available"],
                                                              " (CHANGES PLATFORM MOVEMENT VALUES)" if r["motion"] else ""))
            elif r.get("problem"):
                self.say("update check %s: %s" % (r["title"], r["problem"]))
            else:
                self.say("update check %s: up to date (%s)" % (r["title"], r["current"]))
        return recs

    def apply_updates(self, ids, motion_confirmed=False):
        """-> restart_needed. Apps with motion changes need motion_confirmed (typed YES)."""
        import scm_appupdate as AU
        restart = False
        for r in self._updates:
            if r["id"] not in ids or not r.get("update"):
                continue
            if r.get("motion") and not motion_confirmed:
                self.say("%s: not updated (platform movement values change; YES was not typed)" % r["title"])
                continue
            ok, text, rs = AU.apply(r, self.say, lambda: bool(self.sc.scan(self.sc.GAME_EXE)),
                                    lambda: self.sc.simhub_state()[0] == "running", platform=self.platform,
                                    install_dir=getattr(self.sc, "INSTALL_DIR", None),
                                    simhub_dir=getattr(self.sc, "SIMHUB_DIR", None))
            self.say(("updated: " if ok else "NOT updated: ") + text)
            if ok and r.get("motion"):
                self._set(motion_changed=True)
            restart = restart or (ok and rs)
        return restart

    # ---- end
    def shutdown(self, stop_pipeline=True):
        self.stop_event.set()
        if stop_pipeline:
            self.sc.cleanup()
