#!/usr/bin/env python3
"""sc_motion_clean.py - undo the motion setup's changes to the game and SimHub, so Star Citizen
runs exactly as without it. App-menu entry: "Star Citizen - Remove Motion Setup".

  python3 ~/sc_motion_clean.py          same as plan
  python3 ~/sc_motion_clean.py plan     read-only: what apply would do
  python3 ~/sc_motion_clean.py apply    do it (asks you to type CLEAN in a terminal)

apply, in this order (each part is skipped when already clean, so it can be run again):
  1 pipeline     stop the capture server and bridge (python3 ~/sc_start.py stop; also closes an
                 RSI Launcher left open)
  2 certificate  every livemotion.crt copy in the game prefix back to D-BOX's original
                 (python3 ~/sc_certswap.py original; backups in ~/sc_certswap_backup/)
  3 connector    move the folder ProgramData\\D-BOX\\Gaming\\LiveMotion\\StarCitizen (the
                 copy installer step 1 made) to the backup folder, so the game finds no connector
                 there. --keep-connector skips this part.
  4 plugin       move SimHub's SCHaptics.dll to the backup folder (SimHub must be closed)
  5 menu         move the "Start StarCitizen - Motion Enabled" entry to the backup folder
Backup folder: ~/sc_motion_clean_backup/<time>/ with manifest.json. Nothing is deleted.

Not touched: this repository and the links in your home folder, ~/lm.crt, ~/lm.key, ~/lm_certs,
~/livemotion.crt.orig, effects.json, recordings, SimHub's own settings (the "Star Citizen"
game definition and its registration, ShakeIt effects, motion profile, COM ports), the D-BOX
HaptiSync install itself (remove it with D-BOX's uninstaller in the game prefix if you want),
and this menu entry.
To set everything up again: python3 <this repository>/setup/linux/sc_motion_install.py apply
(it recreates the connector folder, certificate copies, plugin and menu entry).

Refuses while StarCitizen.exe or SimHub runs. Never runs as root.
Exit codes: 0 clean (or plan shown), 1 usage, 2 game or SimHub running, 3 not confirmed,
6 a part failed (the parts after it were not run; run apply again after fixing it).
Test overrides: SC_CERT_DRIVE_C (game prefix drive_c, as sc_certswap.py), SC_SIMHUB_DIR,
SC_CLEAN_BACKUP, SC_CLEAN_CONFIRM=CLEAN (no prompt).
"""
import json, os, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sc_certswap as cs  # noqa: E402  (game prefix location and process checks)

VERSION = "1.0"
H = os.path.expanduser("~")
CONNECTOR_REL = os.path.join("ProgramData", "D-BOX", "Gaming", "LiveMotion", "StarCitizen")
CONFIG = os.path.join(H, ".config", "sc-motion", "config.json")
APPS = os.path.join(H, ".local", "share", "applications")
MOTION_DESKTOP = os.path.join(APPS, "sc-start-motion.desktop")
BACKUP_ROOT = os.environ.get("SC_CLEAN_BACKUP", os.path.join(H, "sc_motion_clean_backup"))
SIMHUB_DEFAULT = os.path.join(H, "linux-simracing-utils", "pfx", "drive_c", "Program Files (x86)", "SimHub")


def simhub_dir():
    if os.environ.get("SC_SIMHUB_DIR"):
        return os.environ["SC_SIMHUB_DIR"]
    try:
        with open(CONFIG) as f:
            return json.load(f).get("simhub_dir") or SIMHUB_DEFAULT
    except (OSError, ValueError):
        return SIMHUB_DEFAULT


def pipeline_running():
    return any(cs.procs_with_arg(n) for n in cs.SERVER_NAMES + ("lm_simhub3t.py", "lm_simhub3.py", "lm_simhub4t.py", "lm_simhub4.py"))


def simhub_running():
    return bool(cs.procs_with_arg("simhubwpf.exe"))


def parts(keep_connector):
    """[(name, needed?, what)] - read-only."""
    out = []
    out.append(("pipeline", pipeline_running(),
                "capture server/bridge running: stop them" if pipeline_running() else "not running"))
    copies = cs.find_copies()
    not_orig = [p for p in copies if cs.sha(p) != cs.ORIG_SHA]
    out.append(("certificate", bool(not_orig),
                ("%d of %d copies are not D-BOX's original: restore them" % (len(not_orig), len(copies)))
                if not_orig else "%d copies, all D-BOX's original" % len(copies)))
    conn = os.path.join(cs.DRIVE_C, CONNECTOR_REL)
    if keep_connector:
        out.append(("connector", False, "kept (--keep-connector)"))
    else:
        out.append(("connector", os.path.isdir(conn),
                    "move %s to the backup" % cs.rel(conn) if os.path.isdir(conn) else "no StarCitizen connector folder"))
    dll = os.path.join(simhub_dir(), "SCHaptics.dll")
    out.append(("plugin", os.path.isfile(dll),
                "move %s to the backup" % dll if os.path.isfile(dll) else "SCHaptics.dll not in SimHub"))
    out.append(("menu", os.path.isfile(MOTION_DESKTOP),
                "move %s to the backup" % MOTION_DESKTOP if os.path.isfile(MOTION_DESKTOP) else "no motion menu entry"))
    return out


def show(ps):
    for name, need, what in ps:
        print("  %-12s %-6s %s" % (name, "TODO" if need else "clean", what))


def move(src, bdir, man, label):
    dst = os.path.join(bdir, label)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    man.append({"part": label, "from": src, "to": dst})
    print("      moved %s -> %s" % (src, dst))


def apply(keep_connector):
    if cs.game_pids():
        print("REFUSED: Star Citizen is running. Quit the game first.")
        return 2
    if simhub_running():
        print("REFUSED: SimHub is running. Close SimHub first (the plugin DLL is in use).")
        return 2
    ps = parts(keep_connector)
    print("sc_motion_clean %s - game prefix drive_c: %s" % (VERSION, cs.DRIVE_C))
    show(ps)
    if not any(need for _, need, _ in ps):
        print("RESULT: already clean")
        return 0
    if os.environ.get("SC_CLEAN_CONFIRM") != "CLEAN":
        if not sys.stdin.isatty():
            print("REFUSED: needs a terminal to confirm (or SC_CLEAN_CONFIRM=CLEAN)")
            return 3
        try:
            a = input("Type CLEAN to remove the motion setup's changes (anything else cancels): ").strip()
        except EOFError:
            a = ""
        if a != "CLEAN":
            print("cancelled; nothing changed")
            return 3
    bdir = os.path.join(BACKUP_ROOT, time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(bdir, exist_ok=True)
    man = []

    def done(rc_ok, name, detail):
        print("  %-12s %s  %s" % (name, "DONE" if rc_ok else "FAILED", detail))
        return rc_ok

    ok = True
    try:
        ok = run_parts(ps, bdir, man, done)
    finally:
        with open(os.path.join(bdir, "manifest.json"), "w") as f:
            json.dump({"tool": "sc_motion_clean", "version": VERSION, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "drive_c": cs.DRIVE_C, "moved": man}, f, indent=1)
    if not ok:
        print("RESULT: stopped at a failed part (see above); backup %s" % bdir)
        return 6
    print("RESULT: clean. Backup: %s" % bdir)
    print("Set up again later: python3 %s apply" % os.path.join(
        os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "setup", "linux", "sc_motion_install.py"))
    return 0


def run_parts(ps, bdir, man, done):
    ok = True
    for name, need, _ in ps:
        if not ok:
            break
        if not need:
            continue
        if name == "pipeline":
            r = subprocess.run([sys.executable, os.path.join(H, "sc_start.py"), "stop"], capture_output=True, text=True)
            out = (r.stdout + r.stderr).strip().splitlines()
            ok = done(not pipeline_running(), name, out[-1] if out else "sc_start.py stop exit %d" % r.returncode)
        elif name == "certificate":
            r = subprocess.run([sys.executable, os.path.join(HERE, "sc_certswap.py"), "original"],
                               capture_output=True, text=True)
            out = (r.stdout + r.stderr).strip()
            print("\n".join("      " + x for x in out.splitlines()))
            ok = done(r.returncode == 0, name, "sc_certswap.py original exit %d" % r.returncode)
        elif name == "connector":
            move(os.path.join(cs.DRIVE_C, CONNECTOR_REL), bdir, man, "game_prefix/" + CONNECTOR_REL.replace(os.sep, "/"))
            ok = done(True, name, "")
        elif name == "plugin":
            move(os.path.join(simhub_dir(), "SCHaptics.dll"), bdir, man, "simhub/SCHaptics.dll")
            ok = done(True, name, "")
        elif name == "menu":
            move(MOTION_DESKTOP, bdir, man, "applications/sc-start-motion.desktop")
            try:
                subprocess.run(["update-desktop-database", APPS], capture_output=True, timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                pass  # the menu refreshes by itself; this only speeds it up
            ok = done(True, name, "")
    return ok


def main(a):
    keep = "--keep-connector" in a
    args = [x for x in a[1:] if x != "--keep-connector"]
    if args in ([], ["plan"]):
        print("sc_motion_clean %s plan (read-only) - game prefix drive_c: %s" % (VERSION, cs.DRIVE_C))
        ps = parts(keep)
        show(ps)
        print("RESULT: %s" % ("already clean" if not any(n for _, n, _ in ps) else
                              "apply would change the TODO parts (python3 ~/sc_motion_clean.py apply)"))
        return 0
    if args == ["apply"]:
        rc = apply(keep)
        if os.environ.get("SC_START_HOLD") == "1" and sys.stdin.isatty():
            try:
                input("Press Enter to close this window.")
            except EOFError:
                pass
        return rc
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
