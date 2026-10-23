#!/usr/bin/env python3
"""sc_haptics_install.py - install / check / remove the SC Haptics plugin DLL in SimHub.

  python3 sc_haptics_install.py check     built DLL vs installed DLL (exit 0 = installed and current)
  python3 sc_haptics_install.py install   copy the built DLL into the SimHub folder (SimHub must be closed);
                                          creates ~/sc_haptics/effects.json from effects.default.json if missing
                                          (never overwrites an existing effects.json)
  python3 sc_haptics_install.py remove    move the installed DLL to ~/sc_haptics/backup (SimHub must be closed)

Touches only SCHaptics.dll in the SimHub folder; previous copies are kept in
~/sc_haptics/backup (outside SimHub, so SimHub never loads two versions).
Nothing here touches Star Citizen, its prefix or the bridge.
SimHub folder: env SC_SIMHUB_DIR, else the one the Linux installer saved in
~/.config/sc-motion/config.json, else ~/linux-simracing-utils/pfx/drive_c/Program Files (x86)/SimHub.
Env: SC_SIMHUB_DIR, SC_HAPTICS_DLL to override the paths below.
"""
import hashlib, json, os, shutil, sys, time

HOME = os.path.expanduser("~")


def _saved_simhub_dir():
    try:
        with open(os.path.join(HOME, ".config", "sc-motion", "config.json")) as f:
            return json.load(f).get("simhub_dir") or None
    except (OSError, ValueError, AttributeError):
        return None


SIMHUB_DIR = (os.environ.get("SC_SIMHUB_DIR") or _saved_simhub_dir() or
              os.path.join(HOME, "linux-simracing-utils", "pfx", "drive_c", "Program Files (x86)", "SimHub"))
BUILT = os.environ.get("SC_HAPTICS_DLL", os.path.join(HOME, "sc_haptics/src/bin/Release/net48/SCHaptics.dll"))
DEST = os.path.join(SIMHUB_DIR, "SCHaptics.dll")
BACKUP = os.path.join(HOME, "sc_haptics/backup")
CONFIG = os.path.join(HOME, "sc_haptics/effects.json")
DEFAULT_CONFIG = os.path.join(HOME, "sc_haptics/effects.default.json")


def ensure_config():
    if os.path.isfile(CONFIG):
        print("config   : %s (kept)" % CONFIG)
    elif os.path.isfile(DEFAULT_CONFIG):
        shutil.copyfile(DEFAULT_CONFIG, CONFIG)
        print("config   : created %s from %s" % (CONFIG, os.path.basename(DEFAULT_CONFIG)))
    else:
        print("config   : MISSING %s (plugin runs with no effects)" % CONFIG)


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def simhub_running():
    """pid of a process with an argument ending in SimHubWPF.exe (the Wine process), else 0."""
    me = os.getpid()
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == me:
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as f:
                args = f.read().split(b"\0")
        except OSError:
            continue
        if any(a.strip().lower().endswith(b"simhubwpf.exe") for a in args):
            return int(pid)
    return 0


def backup(path):
    os.makedirs(BACKUP, exist_ok=True)
    dst = os.path.join(BACKUP, "SCHaptics.dll.%s" % time.strftime("%Y%m%d_%H%M%S"))
    shutil.move(path, dst)
    return dst


def check():
    if not os.path.isdir(SIMHUB_DIR):
        print("SimHub folder not found: %s" % SIMHUB_DIR)
        return 2
    b = sha(BUILT) if os.path.isfile(BUILT) else None
    d = sha(DEST) if os.path.isfile(DEST) else None
    print("built    : %s  %s" % (b or "MISSING", BUILT))
    print("installed: %s  %s" % (d or "MISSING", DEST))
    print("config   : %s  %s" % ("present" if os.path.isfile(CONFIG) else "MISSING", CONFIG))
    if d is None:
        print("status: NOT INSTALLED")
        return 1
    if b is None:
        print("status: INSTALLED (no local build to compare)")
        return 0
    print("status: %s" % ("OK (installed = built)" if b == d else "DIFFERENT (run install)"))
    return 0 if b == d else 1


def install():
    if not os.path.isfile(BUILT):
        sys.exit("no built DLL at %s (build first)" % BUILT)
    if not os.path.isdir(SIMHUB_DIR):
        sys.exit("SimHub folder not found: %s" % SIMHUB_DIR)
    pid = simhub_running()
    if pid:
        sys.exit("SimHub is running (pid %d). Close SimHub first." % pid)
    b = sha(BUILT)
    if os.path.isfile(DEST):
        if sha(DEST) == b:
            print("already installed, sha256 %s" % b)
            ensure_config()
            return 0
        print("previous DLL moved to %s" % backup(DEST))
    tmp = DEST + ".tmp"
    shutil.copyfile(BUILT, tmp)
    os.replace(tmp, DEST)
    d = sha(DEST)
    print("installed %s\nsha256 %s %s" % (DEST, d, "(verified)" if d == b else "MISMATCH"))
    ensure_config()
    return 0 if d == b else 1


def remove():
    if not os.path.isfile(DEST):
        print("not installed")
        return 0
    pid = simhub_running()
    if pid:
        sys.exit("SimHub is running (pid %d). Close SimHub first." % pid)
    print("moved %s -> %s" % (DEST, backup(DEST)))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "check":
        sys.exit(check())
    if cmd == "install":
        sys.exit(install())
    if cmd == "remove":
        sys.exit(remove())
    sys.exit(__doc__)
