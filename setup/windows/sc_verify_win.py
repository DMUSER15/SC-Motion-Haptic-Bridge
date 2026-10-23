#!/usr/bin/env python3
"""sc_verify_win.py - check that the Star Citizen -> SimHub motion setup is complete and
working on Windows. Prints PASS / WARN / FAIL per check and saves a report
(%USERPROFILE%\\lm_capture\\verify\\verify_<time>.txt).

  py -3 %USERPROFILE%\\sc-motion\\sc_verify_win.py                 read-only checks
  py -3 %USERPROFILE%\\sc-motion\\sc_verify_win.py --live          + pipeline test without the game
        (capture server, TLS self-test, tee + bridge, zero frames -> SimHub packets checked against
        the registered game definition (size and signature),
        tee copy for the plugin; then both stopped). Game, SimHub and the pipeline must be closed.
  py -3 %USERPROFILE%\\sc-motion\\sc_verify_win.py --last-session  + logs of the last start

Runs from the install folder (next to sc_start_win.py). Read-only except --live.
Exit codes: 0 PASS, 9 WARN, 10 FAIL, 1 usage, 2 --live refused.
Test overrides: same as sc_install_win.py, plus SC_VERIFY_CAPTURE_SCRIPT.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scm_common as C  # noqa: E402
import scm_checks as K  # noqa: E402
import scm_manifest as M  # noqa: E402
import winproc  # noqa: E402
import subprocess  # noqa: E402

H = os.path.expanduser("~")
DRIVE = os.environ.get("SC_CERT_DRIVE_C", os.environ.get("SystemDrive", "C:") + "\\")
PF = os.environ.get("ProgramFiles", os.path.join(DRIVE, "Program Files"))
PF86 = os.environ.get("ProgramFiles(x86)", os.path.join(DRIVE, "Program Files (x86)"))
import winlocate as WL  # noqa: E402
_CFG = WL.load()
SIMHUB_DIR = os.environ.get("SC_SIMHUB_DIR") or _CFG.get("simhub_dir") or os.path.join(PF86, "SimHub")
RSI = os.environ.get("SC_RSI_LAUNCHER") or _CFG.get("rsi_launcher") or os.path.join(
    PF, "Roberts Space Industries", "RSI Launcher", "RSI Launcher.exe")
LIVE = os.environ.get("SC_LIVE_DIR") or _CFG.get("live_dir") or os.path.join(PF, "Roberts Space Industries", "StarCitizen", "LIVE")
CRT, KEY = os.path.join(H, "lm.crt"), os.path.join(H, "lm.key")
ORIG = os.path.join(H, "livemotion.crt.orig")
VERIFY_DIR = os.path.join(H, "lm_capture", "verify")
LAUNCH_DIR = os.path.join(H, "lm_capture", "launch")
NEEDED = ["SC_Motion_Server.py", "motion_desc.bin", "lm_simhub3.py", "lm_simhub3t.py", "lm_simhub4.py", "lm_simhub4t.py",
          "sc_fx.py", "sc_certswap.py", "sc_lmcert.py", "sc_start.py", "sc_start_win.py", "sc_cert_win.py", "winproc.py",
          "winlocate.py", "scm_common.py", "scm_checks.py", "scm_motion_hw.py", "scm_simdef.py"]
PIPELINE = ("sc_motion_server.py", "lm_server2.py", "lm_simhub.py", "lm_simhub2.py", "lm_simhub3.py", "lm_simhub3t.py",
            "lm_simhub4.py", "lm_simhub4t.py")


def cert(*args):
    r = subprocess.run([sys.executable, os.path.join(HERE, "sc_cert_win.py")] + list(args),
                       capture_output=True, text=True, timeout=120)
    return r.returncode, (r.stdout + r.stderr).strip()


def last(out):
    lines = [x for x in (out or "").splitlines() if x.strip()]
    return lines[-1].strip() if lines else ""


def main(argv):
    args = set(argv[1:])
    if not args <= {"--live", "--last-session", "--no-save"}:
        print(__doc__)
        return 1
    R = C.Report("sc_verify_win (Windows)")
    print("sc_verify_win  install %s  user folder %s" % (HERE, H))
    # system
    v = sys.version_info
    R.add("PASS" if v >= (3, 9) else "FAIL", "system", "python", "%d.%d.%d %s" % (v[0], v[1], v[2], sys.executable))
    for mod, ver in C.python_modules().items():
        R.add("PASS" if ver else "FAIL", "system", "module " + mod, ver or "missing",
              "py -3 -m pip install --user grpcio protobuf")
    o = C.find_openssl()
    R.add("PASS" if o else "FAIL", "system", "openssl", o or "missing", "winget install -e --id ShiningLight.OpenSSL.Light")
    miss = [n for n in NEEDED if not os.path.isfile(os.path.join(HERE, n))]
    R.add("FAIL" if miss else "PASS", "install", "script files", "missing: " + ", ".join(miss) if miss else
          "%d present" % len(NEEDED), "Install-SCMotion.ps1 -Apply (step 6)")
    errs = [n for n in os.listdir(HERE) if n.endswith(".py") and not C.compile_ok(os.path.join(HERE, n))[0]]
    R.add("FAIL" if errs else "PASS", "install", "scripts compile", ", ".join(errs) or "all compile")
    # game
    for kind in ("rsi_launcher", "live_dir", "simhub_dir"):
        c = WL.candidates(kind)
        if len(c) > 1:
            R.add("INFO", "locate", "%s copies" % WL.KINDS[kind]["title"], "; ".join(x["path"] for x in c))
    R.add("PASS" if os.path.isfile(RSI) else "FAIL", "game", "RSI Launcher", RSI,
          "Install-SCMotion.ps1 -Apply (assigns it), or set SC_RSI_LAUNCHER")
    exe = os.path.join(LIVE, "Bin64", "StarCitizen.exe")
    R.add("PASS" if os.path.isfile(exe) else "WARN", "game", "Star Citizen LIVE", exe,
          "install LIVE, or set SC_LIVE_DIR to your library's LIVE folder")
    # dbox
    con = os.path.join(C.connector_dir(DRIVE), "dbxLive64.dll")
    R.add("PASS" if os.path.isfile(con) else "FAIL", "dbox", "HaptiSync connector installed",
          "present" if os.path.isfile(con) else "missing", "install D-BOX HaptiSync Center")
    miss = C.game_folder_missing(DRIVE)
    ok = miss == [] and os.path.isfile(os.path.join(C.game_dir(DRIVE), "dbxLive64.dll"))
    R.add("PASS" if ok else "FAIL", "dbox", "StarCitizen connector folder", "complete" if ok else "missing %s" % (miss or "folder"),
          "Install-SCMotion.ps1 -Apply (step 1, elevated)")
    if os.path.isdir(C.game_dir(DRIVE)):
        ok, det = C.connector_config_check(C.game_dir(DRIVE))
        R.add("PASS" if ok else "WARN", "dbox", "connector config", det)
    pipe = winproc.scan(*PIPELINE)
    held = winproc.listening("tcp", C.PORT_CAPTURE_TCP)
    who = winproc.owner("tcp", C.PORT_CAPTURE_TCP) if held else ""
    if held and not who.lower().startswith(tuple(n.lower() for n in C.SERVER_NAMES)):
        R.add("FAIL", "dbox", "port 61666", "held by %s" % (who or "another program"),
              "set D-BOX's Motion Engine service to Manual and stop it (docs/INSTALL-WINDOWS.md)")
    else:
        R.add("PASS", "dbox", "port 61666", "free" if not held else "our capture server")
    # cert
    ok = os.path.isfile(ORIG) and C.sha256(ORIG) == C.DBOX_ORIG_SHA
    R.add("PASS" if ok else "FAIL", "cert", "livemotion.crt.orig", "known D-BOX original" if ok else "missing or different",
          "Install-SCMotion.ps1 -Apply (step 4)")
    rc, out = cert("lmcert", "check")
    R.add({0: "PASS", 9: "WARN"}.get(rc, "FAIL"), "cert", "lmcert check", last(out),
          "py -3 sc_cert_win.py lmcert status; after a game/D-BOX update: lmcert repair (elevated)")
    ok, det = C.desc_check(C.server_desc_path(os.path.join(HERE, C.SERVER_SCRIPT), H))
    R.add("PASS" if ok else "FAIL", "cert", C.DESC_FILE, det, "Install-SCMotion.ps1 -Apply (installs it with the scripts)")
    # simhub
    R.add("PASS" if os.path.isfile(os.path.join(SIMHUB_DIR, "SimHubWPF.exe")) else "FAIL", "simhub", "SimHub",
          SIMHUB_DIR, "install SimHub or set SC_SIMHUB_DIR")
    try:
        import scm_simdef as SD
        sp = SD.Place(None, SIMHUB_DIR, os.environ.get("LOCALAPPDATA") or os.path.join(H, "AppData", "Local"),
                      scripts_dir=HERE)
        ok, det = SD.check_registered(sp)
        R.add("PASS" if ok else "FAIL", "simhub", "game definition 'Star Citizen'", det,
              "Install-SCMotion.ps1 -Apply (step 8), with SimHub closed")
        sev, det = SD.active_game_text(SIMHUB_DIR)
        R.add(sev, "simhub", "active game", det, "SimHub: game list (top left) -> Star Citizen")
    except ImportError:
        R.add("FAIL", "simhub", "game definition 'Star Citizen'", "scm_simdef.py not installed",
              "Install-SCMotion.ps1 -Apply (step 6)")
    dll = os.path.join(SIMHUB_DIR, "SCHaptics.dll")
    R.add("PASS" if os.path.isfile(dll) else "WARN", "simhub", "SC Haptics plugin", "installed" if os.path.isfile(dll)
          else "SCHaptics.dll missing (motion works, shakers do not)", "Install-SCMotion.ps1 -Apply (step 9)")
    cfg = os.path.join(H, "sc_haptics", "effects.json")
    if os.path.isfile(cfg):
        ok, det = C.json_ok(cfg)
        R.add("PASS" if ok else "FAIL", "simhub", "effects.json", det)
    else:
        R.add("WARN", "simhub", "effects.json", "missing", "Install-SCMotion.ps1 -Apply (step 10)")
    # motion hardware (optional; installer step 12): information only, never WARN or FAIL
    try:
        import scm_motion_hw as MH
        hw = MH.saved(_CFG)
        R.add("INFO", "motion", "motion hardware", "%s (optional; set up in SimHub as usual)" % (
            MH.SHORT[hw] if hw else "not chosen"), "" if hw else "Install-SCMotion.ps1 -Apply -Select")
    except ImportError:
        R.add("INFO", "motion", "motion hardware", "scm_motion_hw.py not installed (Install-SCMotion.ps1 -Apply)")
    lnk = os.path.join(os.environ.get("APPDATA", H), "Microsoft", "Windows", "Start Menu", "Programs",
                       "Start StarCitizen - Motion Enabled.lnk")
    if os.name == "nt":
        R.add("PASS" if os.path.isfile(lnk) else "WARN", "launch", "Start menu entry", lnk, "Install-SCMotion.ps1 -Apply (step 11)")
    # runtime
    game = winproc.scan("starcitizen.exe")
    simhub = winproc.scan("simhubwpf.exe")
    R.add("INFO", "runtime", "running now", "game %s, SimHub %s, pipeline %s" % (
        "yes" if game else "no", "yes" if simhub else "no", "yes" if pipe else "no"))
    rc = 0
    if "--live" in args:
        if game or simhub or pipe:
            R.add("FAIL", "live", "preconditions", "close the game, SimHub and the pipeline first (sc_start_win.py stop)")
            rc = 2
        else:
            import sc_start_win as W
            W.patch()
            cap = os.environ.get("SC_VERIFY_CAPTURE_SCRIPT", W.CAPTURE_SCRIPT)
            K.live_pipeline(R, W.S, cap, None, VERIFY_DIR, lambda: cert("lmcert", "selftest"))
    if "--last-session" in args:
        K.last_session(R, LAUNCH_DIR)
    verdict = R.summary()
    if "--no-save" not in args:
        print("report: %s" % R.save(VERIFY_DIR))
    if rc == 2:
        return 2
    return {"PASS": 0, "WARN": 9, "FAIL": 10}[verdict]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
