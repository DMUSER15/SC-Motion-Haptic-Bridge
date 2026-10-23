#!/usr/bin/env python3
"""sc_motion_guide.py - guided install of the two programs the motion setup depends on, on
Linux: D-BOX HaptiSync Center (into the Star Citizen prefix) and SimHub (linux-simracing-utils).

  python3 setup/linux/sc_motion_guide.py dbox   [--installer FILE] [--game-prefix PATH] [--apply]
  python3 setup/linux/sc_motion_guide.py simhub [--apply]

Without --apply: read-only, shows every step and its state. With --apply: walks through the
steps and asks before each one that changes something ([y/N]); answer n to stop.

dbox steps (HaptiSync Center 1.4.0 was the version installed and working on 2026-10-15; other
versions are found and offered, but the Wine workarounds are only proven with 1.4.0):
  1 installer   finds HSC_<version>_setup.exe / *HaptiSync*.exe in ~/Downloads, ~, ~/dbox-install
                and picks the newest version (or --installer FILE)
  2 prefix      the Star Citizen prefix (same choice as the installer's D2; game must be closed)
  3 powershell  PowerShell 7 in the prefix (winetricks powershell_core powershell, as on the
                working machine)
  4 shim        builds the sc.exe shim from setup/linux/dbox-wine/sc_shim.c (mingw-w64-gcc)
  5 stage       copies installer + sc.exe into ~/dbox-install (the shim must sit next to it)
  6 netsecurity NetSecurity stand-in module into the prefix's PowerShell 7 Modules folder
  7 wmic        wmic.bat into C:\\wmicshim in the prefix
  8 run         runs the installer with the LUG runner from sc-launch.sh, WINEPATH=C:\\wmicshim;
                log in ~/dbox-install/install_<time>.log (the HaptiSync Center window that may
                open afterwards crashes under Wine; that is expected and not needed)
  9 check       LiveMotionConnector present; C:\\scshim.log summary
simhub steps:
  1 tools       wine, curl, unzip, git (prints the pacman line)
  2 download    git clone https://github.com/srounce/linux-simracing-utils ~/linux-simracing-utils
  3 install     bash install.sh in that folder (interactive; answer yes for SimHub)
  4 check       SimHub at ~/linux-simracing-utils/pfx, the path sc_start.py uses; other copies listed
  5 motion-hw   optional: connect your motion hardware in SimHub as usual (any platform SimHub
                supports); the installer's step 10 records the choice. Example with a helper
                tool: THANOS AMC, simhub-thanos-linux (docs/THANOS-LINUX.md)
Nothing runs as root; commands that need sudo are printed for you to run.
Exit codes: 0 done, 1 usage, 2 refused (game running / no terminal), 5 steps left.
"""
import argparse, datetime, glob, os, re, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(REPO, "setup", "common")]
import scm_common as C  # noqa: E402
import scm_linux as L  # noqa: E402
import scm_ui as U  # noqa: E402

H = L.HOME
WORK = os.path.join(H, "dbox-install")
DW = os.path.join(REPO, "setup", "linux", "dbox-wine")
PROVEN = "1.4.0"
CC = "x86_64-w64-mingw32-gcc"
CFLAGS = ["-Os", "-s", "-nostdlib", "-ffreestanding", "-fno-builtin", "-fno-tree-loop-distribute-patterns",
          "-fno-stack-protector", "-fno-asynchronous-unwind-tables", "-e", "mainCRTStartup",
          "-Wl,--subsystem,console"]
LSU = os.path.join(H, "linux-simracing-utils")
LSU_URL = "https://github.com/srounce/linux-simracing-utils"


class Guide:
    def __init__(self, apply):
        self.apply = apply
        self.left = 0

    def step(self, n, name, state, detail):
        print("  %-2s %-12s %-8s %s" % (n, name, state, detail))
        if state in ("TODO", "BLOCKED"):
            self.left += 1

    def ask(self, question):
        """Apply mode only: True on yes; anything else stops the guide (exit 5)."""
        try:
            a = input("     %s [y/N] " % question).strip().lower()
        except EOFError:
            a = ""
        if a not in ("y", "yes"):
            print("     stopped; nothing more was changed.")
            raise SystemExit(5)
        return True


def version_key(path):
    m = re.search(r"(\d+(?:\.\d+)+)", os.path.basename(path))
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def find_installers():
    pats = ("HSC_*setup*.exe", "HSC*Setup*.exe", "*HaptiSync*Setup*.exe", "*HaptiSync*Center*.exe")
    found = {}
    for d in (os.path.join(H, "Downloads"), H, WORK):
        for p in pats:
            for f in glob.glob(os.path.join(d, p)):
                if os.path.isfile(f) and "App" not in os.path.basename(f):
                    found.setdefault(os.path.realpath(f), f)
    out, shas = [], set()
    for f in sorted(found.values(), key=lambda f: (version_key(f), os.path.getmtime(f)), reverse=True):
        h = C.sha256(f)
        if h not in shas:  # the same installer in two folders counts once
            shas.add(h)
            out.append(f)
    return out


def runner_wine(prefix):
    try:
        for line in open(os.path.join(prefix, "sc-launch.sh")):
            m = re.match(r'\s*export\s+wine_path="([^"]+)"', line)
            if m:
                w = os.path.join(m.group(1), "wine")
                return w if os.path.exists(w) else None
    except OSError:
        pass
    return None


def imports_ok(exe):
    od = shutil.which("x86_64-w64-mingw32-objdump")
    if not od:
        return None
    r = subprocess.run([od, "-p", exe], capture_output=True, text=True)
    dlls = {x.split(":", 1)[1].strip().lower() for x in r.stdout.splitlines() if "DLL Name" in x}
    return dlls == {"kernel32.dll"}


def same(a, b):
    return os.path.isfile(a) and os.path.isfile(b) and C.sha256(a) == C.sha256(b)


# ----------------------------------------------------------------------------- dbox
def guide_dbox(g, installer, option):
    print("D-BOX HaptiSync Center (guided install into the Star Citizen prefix)%s"
          % ("" if g.apply else "  [read-only]"))
    # 1 installer
    if installer:
        insts = [installer] if os.path.isfile(installer) else []
    else:
        insts = find_installers()
    if not insts:
        g.step(1, "installer", "BLOCKED", "no HaptiSync installer found; download HaptiSync Center from "
               "https://www.d-box.com/en/software-downloads into ~/Downloads")
        return
    pick = 0
    if g.apply and len(insts) > 1:
        pick = U.choose_one("Several HaptiSync installers found. Which one?",
                            ["%s  (version %s)" % (f, ".".join(str(x) for x in version_key(f)) or "?") for f in insts], 0)
        if pick is None:
            raise SystemExit(5)
    inst = insts[pick]
    v = ".".join(str(x) for x in version_key(inst)) or "unknown version"
    note = "" if v == PROVEN else "  (NOTE: workarounds proven with %s only; this is %s)" % (PROVEN, v)
    g.step(1, "installer", "DONE", "%s%s" % (inst, note))
    for other in insts:
        if other != inst:
            print("                         also found: %s" % other)
    # 2 prefix
    if g.apply:
        pfx, how, cands, problem = L.pick_game_prefix(option)
    else:
        pfx, how, cands, problem = L.choose_game_prefix(option)
    if not pfx:
        g.step(2, "prefix", "BLOCKED", problem if g.apply else
               "%s found; --apply shows a menu, or use --game-prefix" % ("%d prefixes" % len(cands) if cands else "no prefix"))
        return
    if L.scan(L.GAME_EXE) or L.scan(L.LAUNCHER_EXE):
        g.step(2, "prefix", "BLOCKED", "Star Citizen or the RSI Launcher is running; close them")
        return
    dc = os.path.join(pfx, "drive_c")
    wine = runner_wine(pfx)
    if not wine:
        g.step(2, "prefix", "BLOCKED", "%s: no usable wine_path in sc-launch.sh (LUG-Helper runner)" % pfx)
        return
    if os.path.isfile(os.path.join(C.connector_dir(dc), "dbxLive64.dll")):
        g.step(2, "prefix", "DONE", "%s: D-BOX connector already installed; nothing to do" % pfx)
        print("\nNext: python3 setup/linux/sc_motion_install.py plan")
        return
    g.step(2, "prefix", "DONE", "%s (%s), runner %s" % (pfx, how, os.path.dirname(os.path.dirname(wine))))
    env = dict(os.environ, WINEPREFIX=pfx)
    # 3 powershell
    pwsh = os.path.join(dc, "Program Files", "PowerShell", "7", "pwsh.exe")
    if os.path.isfile(pwsh):
        g.step(3, "powershell", "DONE", "PowerShell 7 in the prefix")
    else:
        wt = shutil.which("winetricks")
        cmd = ["winetricks", "-q", "powershell_core", "powershell"]
        g.step(3, "powershell", "TODO" if wt else "BLOCKED",
               "env WINEPREFIX=\"%s\" WINE=\"%s\" %s" % (pfx, wine, " ".join(cmd)) +
               ("" if wt else "   (winetricks missing: sudo pacman -S --needed winetricks)"))
        if not wt:
            return
        if g.apply and g.ask("Install PowerShell 7 into the prefix with winetricks now?"):
            subprocess.run(cmd, env=dict(env, WINE=wine))
            if not os.path.isfile(pwsh):
                print("     PowerShell 7 still missing; see the winetricks output above.")
                return
    # 4 shim
    shim = os.path.join(WORK, "sc.exe")
    if os.path.isfile(shim) and imports_ok(shim) is not False:
        g.step(4, "shim", "DONE", "%s present" % shim)
    else:
        cc = shutil.which(CC)
        g.step(4, "shim", "TODO" if cc else "BLOCKED", "build %s from sc_shim.c%s" % (
            shim, "" if cc else "   (needs: sudo pacman -S --needed mingw-w64-gcc)"))
        if not cc:
            return
        if g.apply and g.ask("Build the sc.exe shim now?"):
            os.makedirs(WORK, exist_ok=True)
            r = subprocess.run([cc] + CFLAGS + ["-o", shim, os.path.join(DW, "sc_shim.c"), "-lkernel32"],
                               capture_output=True, text=True)
            if r.returncode or imports_ok(shim) is False:
                print("     build failed:\n" + (r.stdout + r.stderr)[-800:])
                return
            print("     built %s" % shim)
    # 5 stage
    staged = os.path.join(WORK, os.path.basename(inst))
    if same(inst, staged):
        g.step(5, "stage", "DONE", "%s next to sc.exe" % staged)
    else:
        g.step(5, "stage", "TODO", "copy the installer to %s" % WORK)
        if g.apply and g.ask("Copy the installer next to the shim?"):
            os.makedirs(WORK, exist_ok=True)
            shutil.copyfile(inst, staged)
    # 6 netsecurity
    mdir = os.path.join(dc, "Program Files", "PowerShell", "7", "Modules", "NetSecurity")
    files = ("NetSecurity.psd1", "NetSecurity.psm1")
    if all(same(os.path.join(DW, "NetSecurity", f), os.path.join(mdir, f)) for f in files):
        g.step(6, "netsecurity", "DONE", mdir)
    else:
        g.step(6, "netsecurity", "TODO", "copy the stand-in module to %s" % mdir)
        if g.apply and g.ask("Install the NetSecurity stand-in module into the prefix?"):
            os.makedirs(mdir, exist_ok=True)
            for f in files:
                shutil.copyfile(os.path.join(DW, "NetSecurity", f), os.path.join(mdir, f))
    # 7 wmic
    wdst = os.path.join(dc, "wmicshim", "wmic.bat")
    wsrc = os.path.join(DW, "wmic.bat")
    if os.path.isfile(wdst) and (not os.path.isfile(wsrc) or same(wsrc, wdst)):
        g.step(7, "wmic", "DONE", wdst)
    elif not os.path.isfile(wsrc):
        g.step(7, "wmic", "BLOCKED", "setup/linux/dbox-wine/wmic.bat is not in the repository yet")
        return
    else:
        g.step(7, "wmic", "TODO", "copy wmic.bat to %s" % wdst)
        if g.apply and g.ask("Install the wmic stand-in into the prefix?"):
            os.makedirs(os.path.dirname(wdst), exist_ok=True)
            shutil.copyfile(wsrc, wdst)
    # 8 run
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log = os.path.join(WORK, "install_%s.log" % stamp)
    g.step(8, "run", "TODO", "cd %s; env WINEPREFIX=\"%s\" WINEPATH='C:\\wmicshim' \"%s\" ./%s   (log %s)"
           % (WORK, pfx, wine, os.path.basename(staged), log))
    if not g.apply:
        g.step(9, "check", "INFO", "after the installer: LiveMotionConnector present, C:\\scshim.log summary")
        return
    g.ask("Run the HaptiSync installer now? (follow its window; accept the defaults)")
    scl = os.path.join(dc, "scshim.log")
    if os.path.exists(scl):
        os.replace(scl, scl + "." + stamp)
    with open(log, "w") as lf:
        p = subprocess.Popen([wine, "./" + os.path.basename(staged)], cwd=WORK,
                             env=dict(env, WINEPATH="C:\\wmicshim"), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, errors="replace")
        for line in p.stdout:
            lf.write(line)
            if "fixme" not in line and "find_reg_tz" not in line:
                print("     " + line.rstrip())
        p.wait()
    # 9 check
    ok = os.path.isfile(os.path.join(C.connector_dir(dc), "dbxLive64.dll"))
    summ = ""
    if os.path.isfile(scl):
        t = open(scl, errors="replace").read()
        summ = "; scshim.log: %d no-op, %d passed through, start DboxHaptiSyncApi %s" % (
            t.count("NOOP "), t.count("PASS "), "1053 (tolerated)" if "EXIT 1053" in t else "ok")
    g.step(9, "check", "DONE" if ok else "BLOCKED",
           ("LiveMotionConnector installed" if ok else "connector not found; see %s" % log) + summ)
    if ok:
        print("\nNext: python3 setup/linux/sc_motion_install.py plan")


# ----------------------------------------------------------------------------- simhub
def guide_simhub(g):
    print("SimHub with linux-simracing-utils%s" % ("" if g.apply else "  [read-only]"))
    miss = [t for t in ("wine", "curl", "unzip", "git") if not shutil.which(t)]
    g.step(1, "tools", "BLOCKED" if miss else "DONE",
           "missing %s: sudo pacman -S --needed wine curl unzip git" % ", ".join(miss) if miss else "wine, curl, unzip, git")
    if miss:
        return
    if os.path.isfile(os.path.join(LSU, "install.sh")):
        g.step(2, "download", "DONE", LSU)
    else:
        g.step(2, "download", "TODO", "git clone %s %s" % (LSU_URL, LSU))
        if os.path.exists(LSU):
            print("     %s exists but has no install.sh; move it away first." % LSU)
            return
        if g.apply and g.ask("Download linux-simracing-utils with git now?"):
            if subprocess.run(["git", "clone", LSU_URL, LSU]).returncode:
                return
    exe = os.path.join(L.SIMHUB_DIR_DEFAULT, "SimHubWPF.exe")
    if os.path.isfile(exe) and os.path.exists(L.SIMHUB_WINE):
        g.step(3, "install", "DONE", "SimHub installed")
    else:
        g.step(3, "install", "TODO", "cd %s; bash install.sh   (answer yes for SimHub)" % LSU)
        if g.apply and os.path.isfile(os.path.join(LSU, "install.sh")) and \
                g.ask("Run linux-simracing-utils' install.sh now? (it asks its own questions)"):
            subprocess.run(["bash", "install.sh"], cwd=LSU)
    others = [x["prefix"] for x in L.find_simhub_copies() if not x["used"]]
    ok = os.path.isfile(exe) and os.path.exists(L.SIMHUB_WINE)
    g.step(4, "check", "DONE" if ok else "BLOCKED",
           ("%s" % exe if ok else "SimHub not found at %s" % exe) +
           ("; other copies (not used): %s" % ", ".join(others) if others else ""))
    if ok:
        g.step(5, "motion-hw", "INFO", "optional: connect your motion hardware in SimHub as usual "
               "(docs/INSTALL-LINUX.md 4); THANOS example: simhub-thanos-linux (docs/THANOS-LINUX.md)")


def main(argv):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("what", nargs="?")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--installer")
    ap.add_argument("--game-prefix")
    a, extra = ap.parse_known_args(argv[1:])
    if a.what not in ("dbox", "simhub") or extra:
        print(__doc__)
        return 1
    if a.apply and not sys.stdin.isatty():
        print("REFUSED: --apply asks questions and needs a terminal.")
        return 2
    g = Guide(a.apply)
    try:
        if a.what == "dbox":
            guide_dbox(g, a.installer, a.game_prefix)
        else:
            guide_simhub(g)
    except SystemExit as e:
        return e.code
    return 5 if g.left and not a.apply else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
