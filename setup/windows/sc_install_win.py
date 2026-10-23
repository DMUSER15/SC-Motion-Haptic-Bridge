#!/usr/bin/env python3
"""sc_install_win.py - set up (or check) the Star Citizen -> SimHub motion pipeline on Windows.
Normally started by Install-SCMotion.ps1, which checks Python first.

  py -3 setup\\windows\\sc_install_win.py plan  [--rsi-launcher EXE] [--live-dir DIR] [--simhub-dir DIR] [--motion-hw HW]
  py -3 setup\\windows\\sc_install_win.py apply [same options] [--select]
  py -3 setup\\windows\\sc_install_win.py motion-hw none|thanos|other      save that choice only

First the install locations are found and assigned (winlocate.py looks at the environment
variables, the saved choice, Windows' installed-programs list and the usual folders on every
fixed drive). With several RSI Launchers, LIVE folders or SimHubs, apply shows a numbered menu
(Enter = the pre-selected one); --select shows the menus again to change a saved choice.
apply changes nothing until every dependency is DONE:
  D1 prereqs       Python modules grpcio + protobuf, openssl.exe
  D2 star-citizen  RSI Launcher (required); Star Citizen LIVE folder (shown; only used for checks)
  D3 dbox          D-BOX HaptiSync Center installed (connector in C:\\ProgramData\\D-BOX\\...)
  D4 simhub        SimHub installed
  D5 descriptor    scripts\\motion_desc.bin in this repository (the capture server's schema,
                   written by hand in tools\\motion_schema.py; installed with the scripts)
apply saves the locations in %APPDATA%\\sc-motion\\config.json (sc_start_win.py and
sc_verify_win.py use them), then runs the steps:
   1 dbox-folder    ...\\LiveMotion\\StarCitizen = copy of LiveMotionConnector (missing files only)
   2 dbox-config    connector config: 127.0.0.1:61666, LiveMotionComm (check only)
   3 port-61666     nothing else (e.g. D-BOX Motion Engine) listens on 127.0.0.1:61666
   4 dbox-original  %USERPROFILE%\\livemotion.crt.orig = D-BOX's original certificate
   5 cert-pair      %USERPROFILE%\\lm.crt + lm.key (created only if BOTH are absent)
   6 files          the runtime scripts (capture server, bridge lm_simhub4t.py + lm_simhub4.py,
                    lm_simhub3(t).py, sc_fx.py, certificate tools) + Windows helpers copied to
                    %USERPROFILE%\\sc-motion
   7 footprint      every copy of livemotion.crt under C:\\ProgramData\\D-BOX and C:\\Program Files\\D-BOX
                    = lm.crt (sc_cert_win.py lmcert footprint all; backups first). The connector
                    reads the LiveMotionConnector copy, so the StarCitizen copy alone is not enough
   8 simdef         SimHub's game definition "Star Citizen" (24 fields, data conversion revision 3):
                    config\\simhub\\Star Citizen.simdef copied to <SimHub>\\PluginsData\\Common\\
                    ExternalSims and registered in %LOCALAPPDATA%\\SimHub\\ExternalSims\\
                    Registrations. A registered definition the bridge can use is kept as it is;
                    an old file or registration is renamed to *.bak-<time> first. SimHub must be
                    closed. Afterwards select "Star Citizen" in SimHub's game list once.
   9 plugin         SCHaptics.dll in the SimHub folder (built with dotnet, or copied from Linux)
  10 effects        %USERPROFILE%\\sc_haptics\\effects.json (+ effects.default.json)
  11 shortcut       Start menu entry "Start StarCitizen - Motion Enabled" (the launcher checks
                    for updates of this repository folder, saved with the locations)
  12 motion-hw      OPTIONAL motion hardware choice, never a dependency (any platform SimHub
                    supports works; connect it in SimHub as usual): none (default) / thanos
                    (example; on Windows its COM port is a normal Windows port, nothing to set up)
                    / other. apply asks once and saves it; --select asks again.
Then: py -3 %USERPROFILE%\\sc-motion\\sc_verify_win.py

Steps 1 and 7 write under C:\\ProgramData\\D-BOX and may need an elevated PowerShell.
apply refuses while StarCitizen.exe runs. Exit codes: 0 all DONE, 1 usage, 2 game running,
5 steps left, 6 dependencies not met (apply changed nothing). Test overrides: SC_CERT_DRIVE_C
(the C: root), SCM_EXTRA_DRIVES, SC_SCM_INSTALL_DIR, SC_SCM_CONFIG.
"""
import argparse, glob, hashlib, os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(REPO, "setup", "common"), os.path.join(REPO, "tools"),
                os.path.join(REPO, "setup", "linux")]  # scm_ui (menus)
import scm_common as C  # noqa: E402
import scm_manifest as M  # noqa: E402
import winproc  # noqa: E402
import winlocate as WL  # noqa: E402
import scm_motion_hw as MH  # noqa: E402
import scm_simdef as SD  # noqa: E402

IS_WIN = os.name == "nt"
H = os.path.expanduser("~")
DRIVE = os.environ.get("SC_CERT_DRIVE_C", os.environ.get("SystemDrive", "C:") + "\\")
PF = os.environ.get("ProgramFiles", os.path.join(DRIVE, "Program Files"))
PF86 = os.environ.get("ProgramFiles(x86)", os.path.join(DRIVE, "Program Files (x86)"))
# assigned by resolve() before any step runs
RSI = LIVE = SIMHUB_DIR = None
MOTION_HW = None  # None = not chosen yet
LOC = {}
INSTALL = os.environ.get("SC_SCM_INSTALL_DIR", os.path.join(H, "sc-motion"))
CRT, KEY = os.path.join(H, "lm.crt"), os.path.join(H, "lm.key")
ORIG = os.path.join(H, "livemotion.crt.orig")
DESC = os.path.join(REPO, "scripts", C.DESC_FILE)
HAPTICS = os.path.join(H, "sc_haptics")
START_MENU = os.path.join(os.environ.get("APPDATA", H), "Microsoft", "Windows", "Start Menu", "Programs")
LNK = os.path.join(START_MENU, "Start StarCitizen - Motion Enabled.lnk")
CMD = os.path.join(INSTALL, "Start-SCMotion.cmd")
PIP = "py -3 -m pip install --user grpcio protobuf"
OPENSSL = "winget install -e --id ShiningLight.OpenSSL.Light   (then open a new PowerShell)"
ADMIN = "run Install-SCMotion.ps1 -Apply from an elevated PowerShell (Run as administrator)"


def files_to_install():
    """{install-dir name: source path}"""
    out = {}
    for n in M.RUNTIME:
        if n in M.LINUX_ONLY:
            continue
        out[n] = os.path.join(REPO, "scripts", n)
    for p in ("winproc.py", "winlocate.py", "sc_cert_win.py", "sc_start_win.py", "sc_verify_win.py"):
        out[p] = os.path.join(HERE, p)
    for p in ("scm_common.py", "scm_checks.py", "scm_motion_hw.py", "scm_simdef.py", "scm_update.py"):
        out[p] = os.path.join(REPO, "setup", "common", p)
    out["scm_manifest.py"] = os.path.join(REPO, "tools", "scm_manifest.py")
    # the release record (version + file list) of a downloaded or cloned public release; the
    # launcher's update check reads it. A private working repository has none.
    if os.path.isfile(os.path.join(REPO, "release.json")):
        out["release.json"] = os.path.join(REPO, "release.json")
    return out


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def cert_tool(*args):
    p = os.path.join(INSTALL, "sc_cert_win.py")
    if not os.path.exists(p):
        return None, "sc_cert_win.py not installed yet (step 6)"
    r = subprocess.run([sys.executable, p] + list(args), capture_output=True, text=True, timeout=600)
    return r.returncode, (r.stdout + r.stderr).strip()


def last(out):
    lines = [x for x in (out or "").splitlines() if x.strip()]
    return lines[-1].strip() if lines else ""


def writable(fn):
    def wrap():
        try:
            return fn()
        except PermissionError as e:
            return False, "permission denied (%s): %s" % (e.filename, ADMIN)
    return wrap


# ----------------------------------------------------------------------------- steps
def s_prereqs():
    mods = C.python_modules()
    miss = [m for m, v in mods.items() if v is None]
    o = C.find_openssl()
    msg = []
    if miss:
        msg.append("Python modules missing (%s): %s" % (", ".join(miss), PIP))
    if not o:
        msg.append("openssl.exe missing: " + OPENSSL)
    return ("BLOCKED", "; ".join(msg)) if msg else ("DONE", "grpc %s, protobuf %s, %s" % (
        mods["grpc"], mods["google.protobuf"], o))


def s_launcher():
    path, how, cands, problem = LOC["rsi_launcher"]
    if not path:
        return "BLOCKED", problem + ("" if cands else " (install the RSI Launcher, or use --rsi-launcher)")
    lpath, lhow, lcands, lproblem = LOC["live_dir"]
    live = ("; LIVE %s (%s)" % (lpath, lhow)) if lpath else ("; LIVE: %s (only used for checks)" % lproblem)
    return "DONE", "%s (%s)%s" % (path, how, live)


def s_dbox():
    if os.path.isfile(os.path.join(C.connector_dir(DRIVE), "dbxLive64.dll")):
        return "DONE", "LiveMotionConnector present"
    return "BLOCKED", "install D-BOX HaptiSync Center (docs/INSTALL-WINDOWS.md)"


def s_folder():
    miss = C.game_folder_missing(DRIVE)
    if miss is None:
        return "BLOCKED", "needs D3"
    if not miss:
        return "DONE", "StarCitizen connector folder complete"
    return "TODO", "copy %d files LiveMotionConnector -> StarCitizen" % len(miss)


def s_config():
    if not os.path.isdir(C.game_dir(DRIVE)):
        return "BLOCKED", "needs step 1"
    ok, det = C.connector_config_check(C.game_dir(DRIVE))
    return ("DONE" if ok else "INFO"), det


def s_port():
    if not winproc.listening("tcp", C.PORT_CAPTURE_TCP):
        return "DONE", "127.0.0.1:61666 is free"
    who = winproc.owner("tcp", C.PORT_CAPTURE_TCP)
    if who.lower().startswith(tuple(n.lower() for n in C.SERVER_NAMES)):
        return "DONE", "held by our capture server (%s)" % who
    return "BLOCKED", ("port 61666 is held by %s. If it is D-BOX's Motion Engine, set its service "
                       "to Manual and stop it (docs/INSTALL-WINDOWS.md, 'Port 61666')" % (who or "another program"))


def s_orig():
    if os.path.isfile(ORIG):
        if C.sha256(ORIG) == C.DBOX_ORIG_SHA:
            return "DONE", "livemotion.crt.orig is the known D-BOX original"
        return "BLOCKED", "livemotion.crt.orig is not the known D-BOX original (set SC_CERT_ORIG_SHA after checking)"
    if C.original_available(DRIVE, ORIG):
        return "TODO", "copy D-BOX's original livemotion.crt to %s" % ORIG
    return "BLOCKED", "no livemotion.crt matches the known D-BOX original (other HaptiSync version?)"


def s_pair():
    o = C.find_openssl()
    have = [os.path.exists(CRT), os.path.exists(KEY)]
    if all(have):
        if not o:
            return "BLOCKED", "needs openssl (D1)"
        ok, det = C.pair_ok(o, CRT, KEY)
        return ("DONE" if ok else "BLOCKED"), det
    if any(have):
        return "BLOCKED", "only one of lm.crt / lm.key exists; never overwritten"
    if not o:
        return "BLOCKED", "needs openssl (D1)"
    return "TODO", "create a new pair (RSA 4096, CN/SAN LiveMotionComm, CA:TRUE, 10 years)"


def s_desc():
    ok, det = C.desc_check(DESC)
    return ("DONE", det) if ok else ("BLOCKED", "%s %s: rebuild it with py -3 tools\\motion_schema.py build" % (DESC, det))


def s_files():
    want = files_to_install()
    miss = [n for n, p in want.items() if not os.path.isfile(p)]
    if miss:
        return "BLOCKED", "missing in the repository: " + ", ".join(miss)
    todo = [n for n, p in want.items()
            if not os.path.isfile(os.path.join(INSTALL, n)) or sha(os.path.join(INSTALL, n)) != sha(p)]
    if todo:
        return "TODO", "copy %d of %d files to %s" % (len(todo), len(want), INSTALL)
    return "DONE", "%d files in %s match the repository" % (len(want), INSTALL)


def s_footprint():
    for n, fn in ((4, s_orig), (5, s_pair), (6, s_files)):
        if fn()[0] != "DONE":
            return "BLOCKED", "needs step %d" % n
    rc, out = cert_tool("lmcert", "check")
    if rc in (0, 9):
        return "DONE", last(out)
    copies = C.dbox_cert_copies(DRIVE)
    known = (C.DBOX_ORIG_SHA, C.sha256(CRT))
    if copies and all(C.sha256(p) in known for p in copies):
        return "TODO", "some copies are D-BOX's original: lmcert footprint all"
    return "BLOCKED", last(out) + "; run: py -3 %s lmcert status" % os.path.join(INSTALL, "sc_cert_win.py")


def s_simhub():
    path, how, cands, problem = LOC["simhub_dir"]
    if not path:
        return "BLOCKED", problem + ("" if cands else " (install SimHub, or use --simhub-dir)")
    others = [x["path"] for x in cands if os.path.normcase(x["path"]) != os.path.normcase(path)]
    return "DONE", "%s (%s)%s" % (path, how, ("; also found, not used: " + ", ".join(others)) if others else "")


def simdef_place():
    la = os.environ.get("LOCALAPPDATA") or os.path.join(H, "AppData", "Local")
    return SD.Place(REPO, SIMHUB_DIR, la)


def s_simdef():
    if not SIMHUB_DIR or not os.path.isdir(SIMHUB_DIR):
        return "BLOCKED", "needs D4"
    st, det = SD.state(simdef_place())
    if st == "TODO" and winproc.scan("simhubwpf.exe"):
        return "BLOCKED", "SimHub is running; close it, then apply again (%s)" % det
    return st, det


def s_plugin():
    dst = os.path.join(SIMHUB_DIR, "SCHaptics.dll")
    if os.path.isfile(dst):
        return "DONE", "SCHaptics.dll installed"
    src = os.path.join(REPO, "plugin", "src", "SCHaptics.csproj")
    if not os.path.isfile(src):
        return "BLOCKED", "plugin source not in the repository"
    if not shutil.which("dotnet"):
        return "BLOCKED", ("dotnet SDK missing (winget install Microsoft.DotNet.SDK.8), or copy the "
                           "SCHaptics.dll built on Linux into %s with SimHub closed" % SIMHUB_DIR)
    if winproc.scan("simhubwpf.exe"):
        return "BLOCKED", "SimHub is running; close it"
    return "TODO", "dotnet build + copy SCHaptics.dll into SimHub"


def a_plugin():
    proj = os.path.join(REPO, "plugin", "src", "SCHaptics.csproj")
    shd = SIMHUB_DIR.rstrip("\\/") + os.sep
    env = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1")
    r = subprocess.run(["dotnet", "build", proj, "-c", "Release", "-p:SimHubDir=" + shd],
                       capture_output=True, text=True, env=env)
    if r.returncode:
        print("\n".join("      " + x for x in (r.stdout + r.stderr).strip().splitlines()[-15:]))
        return False, "dotnet build failed"
    built = glob.glob(os.path.join(REPO, "plugin", "src", "bin", "Release", "*", "SCHaptics.dll"))
    if not built:
        return False, "build output SCHaptics.dll not found"
    shutil.copyfile(built[0], os.path.join(SIMHUB_DIR, "SCHaptics.dll"))
    return True, "installed %s" % built[0]


def s_effects():
    cfg = os.path.join(HAPTICS, "effects.json")
    if os.path.isfile(cfg):
        ok, det = C.json_ok(cfg)
        return ("DONE" if ok else "BLOCKED"), det
    if not os.path.isfile(os.path.join(REPO, "plugin", "effects.default.json")):
        return "BLOCKED", "plugin/effects.default.json missing in the repository"
    return "TODO", "create %s from the repository" % cfg


def a_effects():
    os.makedirs(HAPTICS, exist_ok=True)
    src_default = os.path.join(REPO, "plugin", "effects.default.json")
    shutil.copyfile(src_default, os.path.join(HAPTICS, "effects.default.json"))
    tuned = os.path.join(REPO, "config", "effects.json")
    shutil.copyfile(tuned if os.path.isfile(tuned) else src_default, os.path.join(HAPTICS, "effects.json"))
    return True, "written (%s)" % ("your tuned config/effects.json" if os.path.isfile(tuned) else "defaults")


def s_shortcut():
    if not IS_WIN:
        return "INFO", "Windows only"
    if os.path.isfile(LNK) and os.path.isfile(CMD):
        return "DONE", LNK
    return "TODO", "create %s" % LNK


def a_shortcut():
    with open(CMD, "w") as f:
        f.write('@echo off\r\nset SC_START_HOLD=1\r\n"%s" "%%~dp0sc_start_win.py" %%*\r\n' % sys.executable)
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s'); $s.TargetPath='%s'; "
          "$s.WorkingDirectory='%s'; $s.Description='Star Citizen motion pipeline, SimHub, then the RSI Launcher'; "
          "$s.Save()" % (LNK.replace("'", "''"), CMD.replace("'", "''"), INSTALL.replace("'", "''")))
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True)
    return os.path.isfile(LNK), (r.stdout + r.stderr).strip() or "created"


def s_motion():
    if MOTION_HW is None:
        return "INFO", "optional, not chosen: apply asks once; or: py -3 setup\\windows\\sc_install_win.py motion-hw none|thanos|other"
    if MOTION_HW == "none":
        return "DONE", "none: no setup help needed (set up any motion hardware in SimHub as usual)"
    if MOTION_HW == "thanos":
        return "DONE", ("THANOS (example): pick its Windows COM port in SimHub's controller setup "
                        "(docs/INSTALL-WINDOWS.md 5)")
    return "DONE", "other: set your motion hardware up in SimHub as usual (docs/INSTALL-WINDOWS.md 5)"


def a_files():
    os.makedirs(INSTALL, exist_ok=True)
    n = 0
    for name, src in files_to_install().items():
        dst = os.path.join(INSTALL, name)
        if not os.path.isfile(dst) or sha(dst) != sha(src):
            shutil.copyfile(src, dst)
            n += 1
    return True, "copied %d files" % n


DEPS = [
    ("D1", "prereqs", s_prereqs),
    ("D2", "star-citizen", s_launcher),
    ("D3", "dbox", s_dbox),
    ("D4", "simhub", s_simhub),
    ("D5", "descriptor", s_desc),
]
STEPS = [
    (1, "dbox-folder", s_folder, writable(lambda: C.ensure_game_folder(DRIVE))),
    (2, "dbox-config", s_config, None),
    (3, "port-61666", s_port, None),
    (4, "dbox-original", s_orig, lambda: C.backup_dbox_original(DRIVE, ORIG)),
    (5, "cert-pair", s_pair, lambda: C.bootstrap_pair(C.find_openssl(), CRT, KEY)),
    (6, "files", s_files, a_files),
    (7, "footprint", s_footprint, writable(lambda: (lambda r: (r[0] == 0, last(r[1])))(cert_tool("lmcert", "footprint", "all")))),
    (8, "simdef", s_simdef, writable(lambda: SD.apply(simdef_place()))),
    (9, "plugin", s_plugin, writable(a_plugin)),
    (10, "effects", s_effects, a_effects),
    (11, "shortcut", s_shortcut, a_shortcut),
    (12, "motion-hw", s_motion, None),
]


def resolve(opts, menu=False, force=False):
    """Assign RSI / LIVE / SIMHUB_DIR (module globals) for the steps."""
    global RSI, LIVE, SIMHUB_DIR, MOTION_HW
    MOTION_HW = getattr(opts, "motion_hw", None) or MH.saved(WL.load())
    for kind in ("rsi_launcher", "live_dir", "simhub_dir"):
        LOC[kind] = WL.choose(kind, getattr(opts, kind, None), menu=menu, force_menu=force)
    RSI, LIVE, SIMHUB_DIR = LOC["rsi_launcher"][0], LOC["live_dir"][0], LOC["simhub_dir"][0]
    if SIMHUB_DIR is None:
        SIMHUB_DIR = os.path.join(PF86, "SimHub")  # only for messages; D4 is BLOCKED


def check(fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        return "BLOCKED", "check error: %s" % e


def show_locations():
    for kind in ("rsi_launcher", "live_dir", "simhub_dir"):
        path, how, cands, problem = LOC[kind]
        print("%s found:" % WL.KINDS[kind]["title"])
        if not cands:
            print("  none")
        for x in cands:
            mark = "->" if path and os.path.normcase(x["path"]) == os.path.normcase(path) else "  "
            print("  %s %s  [%s]" % (mark, x["path"], ", ".join(x["sources"])))
    print()


def show_plan():
    print("sc_install_win plan (read-only)  repo %s" % REPO)
    print()
    show_locations()
    print("Dependencies (must all be DONE before apply changes anything):")
    dst = []
    for n, name, chk in DEPS:
        st, det = check(chk)
        dst.append(st)
        print("  %-3s %-14s %-8s %s" % (n, name, st, det))
    print("Install steps:")
    states = []
    for n, name, chk, _ in STEPS:
        st, det = check(chk)
        states.append(st)
        print("  %-3s %-14s %-8s %s" % (n, name, st, det))
    print()
    if any(x != "DONE" for x in dst):
        print("RESULT: dependencies not met (%d of %d DONE); apply will change nothing until they are"
              % (dst.count("DONE"), len(dst)))
        return 6
    left = [x for x in states if x in ("TODO", "BLOCKED")]
    print("RESULT: %s" % ("all steps DONE; next: py -3 \"%s\"" % os.path.join(INSTALL, "sc_verify_win.py")
                          if not left else "dependencies DONE; %d TODO, %d BLOCKED" % (states.count("TODO"), states.count("BLOCKED"))))
    return 0 if not left else 5


def do_apply(opts):
    if winproc.scan("starcitizen.exe"):
        print("REFUSED: StarCitizen.exe is running. Quit the game first.")
        return 2
    resolve(opts, menu=True, force=opts.select)
    bad = [(n, name, check(chk)) for n, name, chk in DEPS]
    bad = [x for x in bad if x[2][0] != "DONE"]
    if bad:
        show_locations()
        print("Dependencies not met, nothing was changed:")
        for n, name, (st, det) in bad:
            print("  %-3s %-14s %-8s %s" % (n, name, st, det))
        return 6
    global MOTION_HW
    MOTION_HW, hw_how = MH.choose(opts.motion_hw, WL.load(), menu=True, force_menu=opts.select)
    vals = {k: v for k, v in (("rsi_launcher", RSI), ("live_dir", LIVE), ("simhub_dir", SIMHUB_DIR)) if v}
    vals[MH.KEY] = MOTION_HW
    vals["repo"] = REPO  # the launcher's update check updates this folder
    WL.save(vals)
    print("Locations assigned (saved in %s):" % WL.CONFIG)
    print("  RSI Launcher: %s\n  LIVE:         %s\n  SimHub:       %s" % (RSI, LIVE or "(not found; only used for checks)", SIMHUB_DIR))
    print("  Motion hardware: %s (%s; optional)" % (MH.SHORT[MOTION_HW], hw_how))
    for n, name, chk, act in STEPS:
        st, det = check(chk)
        if st == "TODO" and act:
            print("  %-3s %-14s applying: %s" % (n, name, det))
            ok, res = act()
            print("  %-3s %-14s %s %s" % (n, name, "done:" if ok else "FAILED:", res))
            if not ok:
                print("Stopped at step %s. Nothing after it was changed." % n)
                break
    print()
    resolve(opts)
    return show_plan()


def main(argv):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("cmd", nargs="?")
    ap.add_argument("--rsi-launcher", dest="rsi_launcher")
    ap.add_argument("--live-dir", dest="live_dir")
    ap.add_argument("--simhub-dir", dest="simhub_dir")
    ap.add_argument("--select", action="store_true")
    ap.add_argument("--motion-hw", dest="motion_hw")
    ap.add_argument("value", nargs="?")
    a, extra = ap.parse_known_args(argv[1:])
    if a.cmd == "motion-hw":
        if not MH.valid(a.value) or extra or a.select or a.motion_hw:
            print(__doc__)
            return 1
        old = MH.saved(WL.load())
        WL.save({MH.KEY: a.value})
        print("Motion hardware: %s (was %s), saved in %s" % (MH.SHORT[a.value], MH.SHORT[old] if old else "not chosen", WL.CONFIG))
        return 0
    if a.cmd not in ("plan", "apply") or extra or a.value or (a.select and a.cmd != "apply") or \
            (a.motion_hw is not None and not MH.valid(a.motion_hw)):
        print(__doc__)
        return 1
    if a.cmd == "plan":
        resolve(a)
        return show_plan()
    return do_apply(a)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
