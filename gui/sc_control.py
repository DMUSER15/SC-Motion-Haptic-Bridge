#!/usr/bin/env python3
"""sc_control.py - the Star Citizen motion control window (replaces the terminal launcher's
prompts with buttons; the terminal launcher scripts/sc_start.py stays as it is).

  python3 ~/sc-motion-bridge/gui/sc_control.py            normal mode
  python3 ~/sc-motion-bridge/gui/sc_control.py --dev      developer mode (private: developer tee,
                                                          logging tools card), if tools/dev exists
  python3 ~/sc-motion-bridge/gui/sc_control.py --check    no window: print what the window would
                                                          show (status of every part) and exit

What it shows and does (cards, top to bottom; gui/panels.json):
  Updates        checks every installed app when the window opens (motion bridge, the THANOS
                 connection tool on Linux, the MFD add-on) and asks before updating, with a
                 summary of the changes; platform-movement changes need YES typed
  Motion bridge  heartbeat (the light flashes with every bridge status line), certificates
                 (installed correctly or not), the start-up steps; Start / Stop / Repair
  SimHub         running, or an orphan process (listed but its Wine server is gone): End orphan
                 and restart (after a rig check); started automatically after the bridge
                 self-test, like the terminal launcher
  RSI Launcher   running or not; Launch only after the rig check, when the bridge answers and
                 SimHub runs; closes the launcher 30 s after the game exits (cancellable)
  MFD add-on     (if installed) running or not; the MFD configurator
  Logs           open the newest log in your text editor; a log viewer
  Logging tools  (developer mode only) choose, start, watch and stop the logging tools
Closing the window stops the bridge, capture server and add-ons (SimHub stays, like the
terminal launcher). Problems go to the project error log (program sc-control / sc-control-dev).
Needs PySide6 (sudo pacman -S --needed pyside6). Settings: ~/.config/sc-motion/control.json.
"""
import argparse
import importlib.util
import os
import signal
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
DEV_PROFILE = os.path.join(REPO, "tools", "dev", "gui_profile.py")
TITLE = "Star Citizen Motion"


ICON_FILES = ("config/icons/sc-motion.svg", "config/icons/sc-motion.png", "docs/logo.svg", "docs/logo.png")


def app_icon(repo=REPO):
    """The project's own logo for the window (never the game's RSI icon); None if no logo file."""
    for rel in ICON_FILES:
        path = os.path.join(repo, rel)
        if os.path.isfile(path):
            return path
    return None


def load_profile(dev):
    if not dev or not os.path.isfile(DEV_PROFILE):
        return None
    spec = importlib.util.spec_from_file_location("sc_gui_profile", DEV_PROFILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def print_check(control, profile):
    import scm_control as C
    s = control.probe()
    print("bridge:   %s (%s)" % (s["bridge"]["light"], s["bridge"]["text"]))
    print("simhub:   %s (%s)" % (s["simhub"]["light"], s["simhub"]["text"]))
    print("game:     %s (%s)" % (s["game"]["light"], s["game"]["text"]))
    rc = control.cert_check()
    print("certs:    %s (%s)" % (control.st["cert"]["light"], control.st["cert"]["text"]))
    mfd = control.mfd_info()
    print("mfd:      %s" % ("installed %s" % mfd["version"] if mfd.get("installed") else "not installed"))
    print("latest log: %s" % control.latest_log())
    print("game gate: %s" % ("; ".join(control.game_gate()) or "open"))
    entries = C.load_registry(profile is not None, profile)
    print("cards:    %s" % ", ".join("%s(%s)" % (e["id"], e.get("tier", "public")) for e in entries))
    return 0 if rc in (0, 9, None) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sc_control.py", description=__doc__.split("\n")[0],
                                 epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dev", action="store_true", help="developer mode (private)")
    ap.add_argument("--check", action="store_true", help="print the status and exit (no window)")
    ap.add_argument("--no-autostart", action="store_true", help="do not start the bridge when the window opens")
    a = ap.parse_args(argv)
    profile = load_profile(a.dev)
    if a.dev and profile is None:
        print("developer mode is not available in this copy (no tools/dev); starting in normal mode")
    import scm_control as C
    sc = C.load_launcher(profile)
    settings = C.load_settings()
    if a.no_autostart:
        settings["autostart"] = False
    control = C.Control(sc, platform="linux", settings=settings, profile=profile)
    if a.check:
        return print_check(control, profile)
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:
        print("The control window needs PySide6: sudo pacman -S --needed pyside6\n"
              "(the terminal launcher works without it: python3 ~/sc_start.py)")
        return 2
    EL = getattr(sc, "EL", None)
    if EL is not None:
        EL.start("sc-control-dev" if profile else "sc-control")
    code = 0
    try:
        import window
        app = QtWidgets.QApplication(sys.argv[:1])
        app.setApplicationName("sc-control")
        app.setDesktopFileName("sc-control-dev" if profile else "sc-control")
        icon = app_icon()
        if icon:
            app.setWindowIcon(QtGui.QIcon(icon))
        ctx = window.Ctx(control, profile, profile is not None)
        title = getattr(profile, "TITLE", None) or TITLE
        win = window.MainWindow(ctx, window.load_registry(profile is not None, profile), title)
        win.show()

        def _term(*_):
            win.force_close = True
            QtCore.QTimer.singleShot(0, win.close)
        for sg in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            signal.signal(sg, _term)            # handled at the window's next timer tick (every 0.5 s)
        control.start_monitor()
        QtCore.QTimer.singleShot(300, win.startup)
        code = app.exec()
    except BaseException as e:  # noqa: BLE001
        code = 1
        if EL is not None and EL.current() is not None and not isinstance(e, (KeyboardInterrupt, SystemExit)):
            EL.report("crash", e)
        raise
    finally:
        control.shutdown(stop_pipeline=True)
        if EL is not None and EL.current() is not None:
            EL.finish(code)
    return code


if __name__ == "__main__":
    sys.exit(main())
