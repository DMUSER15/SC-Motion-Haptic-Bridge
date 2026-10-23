#!/usr/bin/env python3
"""sc_motion_install.py - set up (or check) the Star Citizen -> SimHub motion pipeline on
Arch / CachyOS, from this repository.

  python3 setup/linux/sc_motion_install.py plan  [--game-prefix PATH] [--motion-hw HW]   read-only
  python3 setup/linux/sc_motion_install.py apply [--game-prefix PATH] [--select] [--motion-hw HW]
  python3 setup/linux/sc_motion_install.py motion-hw none|thanos|other   save that choice only

In a terminal, apply shows a numbered menu when several Star Citizen prefixes are found and none
is chosen yet; the likely one (named by LUG-Helper's config, with the D-BOX connector) is
pre-selected, Enter accepts it. --select shows the menu even when a prefix is already saved.

First the DEPENDENCIES are checked and the install locations assigned. apply changes nothing
at all until every dependency is DONE:
  D1 prereqs       python-grpcio, python-protobuf, openssl (prints the pacman line; installs nothing)
  D2 star-citizen  the LUG-Helper prefix to use. All prefixes found are listed. An explicit choice
                   wins (--game-prefix, then env SC_GAME_PREFIX, then the one saved by an earlier
                   apply in ~/.config/sc-motion/config.json); without one, a single prefix found is
                   used, and with several apply asks you to pick one from a menu
  D3 dbox          D-BOX HaptiSync Center's connector in that prefix (setup/linux/sc_motion_guide.py dbox)
  D4 simhub        SimHub in ~/linux-simracing-utils/pfx (the one sc_start.py starts). Other SimHub
                   copies found elsewhere are listed but never used (sc_motion_guide.py simhub)
  D5 descriptor    scripts/motion_desc.bin in this repository (the capture server's message
                   schema, written by hand in tools/motion_schema.py; nothing to copy)
apply saves the assigned locations in ~/.config/sc-motion/config.json, then runs the steps:
   1 dbox-folder    ProgramData\\D-BOX\\Gaming\\LiveMotion\\StarCitizen = copy of LiveMotionConnector
                    (copies only missing files, never overwrites)
   2 dbox-config    connector config points at 127.0.0.1:61666 / LiveMotionComm (check only)
   3 dbox-original  ~/livemotion.crt.orig = D-BOX's original certificate (copied from the prefix)
   4 cert-pair      ~/lm.crt + ~/lm.key for the capture server (created only if BOTH are absent)
   5 scripts        bridge, capture server, launcher, certificate tools, plugin source and
                    effects.json linked from this repository into your home folder (only these)
   6 footprint      every livemotion.crt copy in the prefix = ~/lm.crt (sc_lmcert.py footprint all;
                    the connector reads the LiveMotionConnector copy since 2026-10-21)
   7 simdef         SimHub's game definition "Star Citizen" (24 fields, data conversion revision 3):
                    config/simhub/Star Citizen.simdef copied to SimHub\\PluginsData\\Common\\
                    ExternalSims and registered in the prefix's AppData\\Local\\SimHub\\ExternalSims\\
                    Registrations. A registered definition the bridge can use is kept as it is;
                    an old file or registration is renamed to *.bak-<time> first. SimHub must be
                    closed. Afterwards select "Star Citizen" in SimHub's game list once.
   8 plugin         SC Haptics plugin built and installed (dotnet build + sc_haptics_install.py)
   9 menu           app-menu entries "Start StarCitizen - Motion Enabled" and
                    "Star Citizen - Remove Motion Setup" (sc_motion_clean.py)
  10 motion-hw      OPTIONAL motion hardware choice (never a dependency; any platform SimHub
                    supports works, set up in SimHub as usual):
                      none    no setup help (no rig, or you set it up yourself) - the default
                      thanos  example: checks the THANOS COM port with the separate tool
                              simhub-thanos-linux (its read-only `plan`); installing it and
                              changing the port are left to you (docs/THANOS-LINUX.md)
                      other   generic notes only (docs/INSTALL-LINUX.md 5)
                    apply asks once in a terminal (Enter = none) and saves the choice with the
                    locations; --motion-hw HW chooses directly; `motion-hw HW` changes only it.
Then run: python3 setup/linux/sc_motion_verify.py

apply refuses while StarCitizen.exe runs. Steps that change the game prefix (1, 6) use the
same backups and safety rules as sc_lmcert.py / sc_certswap.py. Nothing runs as root.
Exit codes: 0 all DONE, 1 usage, 2 game running, 5 steps left (TODO/BLOCKED),
6 dependencies not met (apply changed nothing).
"""
import argparse, os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(REPO, "setup", "common"), os.path.join(REPO, "tools")]
import scm_common as C  # noqa: E402
import scm_linux as L  # noqa: E402
import scm_motion_hw as MH  # noqa: E402
import scm_thanos_ext as TX  # noqa: E402
import scm_manifest as M  # noqa: E402
import scm_simdef as SD  # noqa: E402
import repo_link  # noqa: E402

H = L.HOME
CRT, KEY = os.path.join(H, "lm.crt"), os.path.join(H, "lm.key")
ORIG = os.path.join(H, "livemotion.crt.orig")
DESC = os.path.join(REPO, "scripts", C.DESC_FILE)
PACMAN = "sudo pacman -S --needed python-grpcio python-protobuf openssl espeak-ng dotnet-sdk"


class Ctx:
    def __init__(self, option=None, motion_hw=None):
        self.motion_hw = motion_hw or MH.saved(L.load_config())  # None = not chosen yet
        self.game, self.game_src, self.cands, self.problem = L.choose_game_prefix(option)
        self.dc = os.path.join(self.game, "drive_c") if self.game else None
        self.simhubs = L.find_simhub_copies()
        self.shdir = L.simhub_dir()
        self.openssl = C.find_openssl()
        self.env = L.full_env(self.game, self.shdir)


def run_tool(script, *args, env=None):
    p = os.path.join(H, script)
    if not os.path.exists(p):
        return None, "%s not found (step 5 links it)" % p
    r = subprocess.run([sys.executable, p] + list(args), capture_output=True, text=True, env=env, timeout=600)
    out = (r.stdout + r.stderr).strip()
    return r.returncode, out


def last(out):
    lines = [x for x in (out or "").splitlines() if x.strip()]
    return lines[-1].strip() if lines else ""


# ----------------------------------------------------------------------------- steps
# each check returns (state, detail); state in DONE, TODO, BLOCKED, INFO

def s_prereqs(c):
    mods = C.python_modules()
    miss = [m for m, v in mods.items() if v is None]
    if not c.openssl:
        miss.append("openssl")
    if miss:
        return "BLOCKED", "missing: %s. Run: %s" % (", ".join(miss), PACMAN)
    return "DONE", "grpc %s, protobuf %s, openssl" % (mods["grpc"], mods["google.protobuf"])


def s_game(c):
    if not c.game:
        return "BLOCKED", c.problem + (". Install Star Citizen with LUG-Helper (docs/INSTALL-LINUX.md 2)"
                                       if not c.cands else "")
    live = os.path.isfile(os.path.join(c.game, L.LIVE_REL, "Bin64", "StarCitizen.exe"))
    return "DONE", "%s (%s)%s" % (c.game, c.game_src, "" if live else "; LIVE not installed in it yet")


def s_dbox(c):
    if not c.dc:
        return "BLOCKED", "needs D2"
    if os.path.isfile(os.path.join(C.connector_dir(c.dc), "dbxLive64.dll")):
        return "DONE", "LiveMotionConnector present"
    return "BLOCKED", ("D-BOX HaptiSync Center is not installed in this prefix. Guided install: "
                       "python3 setup/linux/sc_motion_guide.py dbox")


def s_folder(c):
    if not c.dc or C.game_folder_missing(c.dc) is None:
        return "BLOCKED", "needs D3"
    miss = C.game_folder_missing(c.dc)
    if not miss:
        return "DONE", "StarCitizen connector folder complete"
    return "TODO", "copy %d files LiveMotionConnector -> StarCitizen (%s)" % (len(miss), ", ".join(miss[:4]))


def a_folder(c):
    return C.ensure_game_folder(c.dc)


def s_config(c):
    if not c.dc or not os.path.isdir(C.game_dir(c.dc)):
        return "BLOCKED", "needs step 1"
    ok, det = C.connector_config_check(C.game_dir(c.dc))
    return ("DONE" if ok else "INFO"), det


def s_orig(c):
    if os.path.isfile(ORIG):
        if C.sha256(ORIG) == C.DBOX_ORIG_SHA:
            return "DONE", "~/livemotion.crt.orig is the known D-BOX original"
        return "BLOCKED", ("~/livemotion.crt.orig is not the known D-BOX original (another D-BOX "
                           "version?). Check it, then set SC_CERT_ORIG_SHA to its sha256")
    if not c.dc:
        return "BLOCKED", "needs D2"
    if C.original_available(c.dc, ORIG):
        return "TODO", "copy D-BOX's original livemotion.crt from the prefix to ~/livemotion.crt.orig"
    return "BLOCKED", ("no livemotion.crt in the prefix matches the known D-BOX original sha256 "
                       "(different HaptiSync version, or all copies already replaced)")


def a_orig(c):
    return C.backup_dbox_original(c.dc, ORIG)


def s_pair(c):
    have = [os.path.exists(CRT), os.path.exists(KEY)]
    if all(have):
        if not c.openssl:
            return "BLOCKED", "needs openssl (D1)"
        ok, det = C.pair_ok(c.openssl, CRT, KEY)
        return ("DONE" if ok else "BLOCKED"), det + ("" if ok else "; see sc_lmcert.py status")
    if any(have):
        return "BLOCKED", "only one of ~/lm.crt / ~/lm.key exists; never overwritten. Restore the pair (~/lm_certs)"
    if not c.openssl:
        return "BLOCKED", "needs openssl (D1)"
    return "TODO", "create a new pair (RSA 4096, CN/SAN LiveMotionComm, CA:TRUE, 10 years)"


def a_pair(c):
    return C.bootstrap_pair(c.openssl, CRT, KEY)


def s_desc(c):
    ok, det = C.desc_check(DESC)
    if ok:
        return "DONE", det
    return "BLOCKED", ("scripts/%s %s. Rebuild it: python3 tools/motion_schema.py build "
                       "(needs python-protobuf, D1)" % (C.DESC_FILE, det))


def s_scripts(c):
    st = repo_link.states(H, M.is_install_item)
    if not st:
        return "BLOCKED", "no tools/linkmap.json in the repository (run tools/repo_collect.py apply first)"
    bad = [x for x in st if x[3] in ("DRIFT", "ELSEWHERE")]
    if bad:
        return "BLOCKED", ("%d home files differ from the repo (%s): python3 tools/repo_link.py link "
                           "--adopt-home (keep yours) or --use-repo" % (len(bad), ", ".join(os.path.basename(x[1]) for x in bad)))
    miss = [x[0] for x in st if x[3] == "NO-REPO-FILE"]
    if miss:
        return "BLOCKED", "missing in the repository: " + ", ".join(miss)
    todo = [x for x in st if x[3] in ("SAME", "MISSING")]
    if todo:
        return "TODO", "link %d of %d bridge/plugin files into your home folder" % (len(todo), len(st))
    return "DONE", "%d bridge/plugin files linked to the repository" % len(st)


def a_scripts(c):
    rc = repo_link.cmd_link(H, None, M.is_install_item)
    return rc == 0, "repo_link link exit %d" % rc


def s_footprint(c):
    for need, fn in ((3, s_orig), (4, s_pair), (5, s_scripts)):
        if fn(c)[0] != "DONE":
            return "BLOCKED", "needs step %d" % need
    rc, out = run_tool("sc_lmcert.py", "check", env=c.env)
    if rc in (0, 9):
        return "DONE", last(out)
    copies = C.dbox_cert_copies(c.dc)
    known = (C.DBOX_ORIG_SHA, C.sha256(CRT))
    if copies and all(C.sha256(p) in known for p in copies):
        return "TODO", "some copies are D-BOX's original: sc_lmcert.py footprint all"
    return "BLOCKED", last(out) + "; run: python3 ~/sc_lmcert.py status"


def a_footprint(c):
    rc, out = run_tool("sc_lmcert.py", "footprint", "all", env=c.env)
    print("\n".join("      " + x for x in out.splitlines()))
    return rc == 0, last(out)


def s_simhub(c):
    exe = os.path.join(c.shdir, "SimHubWPF.exe")
    miss = [p for p in (L.SIMHUB_PREFIX, L.SIMHUB_WINE, exe) if not os.path.exists(p)]
    others = [x["prefix"] for x in c.simhubs if not x["used"]]
    note = ("; %d other SimHub cop%s found, not used" % (len(others), "y" if len(others) == 1 else "ies")) if others else ""
    if miss:
        return "BLOCKED", ("not found: %s%s. sc_start.py starts SimHub from ~/linux-simracing-utils only. "
                           "Guided install: python3 setup/linux/sc_motion_guide.py simhub" % (", ".join(miss), note))
    return "DONE", c.shdir + note


def s_simdef(c):
    if not os.path.isdir(c.shdir):
        return "BLOCKED", "needs D4"
    st, det = SD.state(L.simdef_place(REPO, c.shdir))
    if st == "TODO" and L.scan(L.SIMHUB_EXE):
        return "BLOCKED", "SimHub is running; close it, then apply again (%s)" % det
    return st, det


def a_simdef(c):
    return SD.apply(L.simdef_place(REPO, c.shdir))


def s_plugin(c):
    rc, out = run_tool("sc_haptics_install.py", "check", env=c.env)
    if rc == 0:
        return "DONE", last(out)
    src = os.path.join(H, "sc_haptics", "src", "SCHaptics.csproj")
    if not os.path.exists(src):
        return "BLOCKED", "plugin source not in ~/sc_haptics/src (step 5 links it)"
    if not L.which("dotnet"):
        return "BLOCKED", "dotnet SDK missing: sudo pacman -S --needed dotnet-sdk"
    if L.scan(L.SIMHUB_EXE):
        return "BLOCKED", "SimHub is running; close it, then apply again"
    return "TODO", "dotnet build + sc_haptics_install.py install"


def a_plugin(c):
    csproj = os.path.join(H, "sc_haptics", "src", "SCHaptics.csproj")
    env = dict(c.env, DOTNET_CLI_TELEMETRY_OPTOUT="1")
    r = subprocess.run(["dotnet", "build", csproj, "-c", "Release"], capture_output=True, text=True, env=env)
    if r.returncode:
        print("\n".join("      " + x for x in (r.stdout + r.stderr).strip().splitlines()[-15:]))
        return False, "dotnet build failed"
    rc, out = run_tool("sc_haptics_install.py", "install", env=c.env)
    print("\n".join("      " + x for x in out.splitlines()))
    return rc == 0, last(out)


MENUS = ((L.DESKTOP, "sc_start.py", L.desktop_entry), (L.DESKTOP_CLEAN, "sc_motion_clean.py", L.clean_desktop_entry))


def s_menu(c):
    todo = []
    for path, script, _ in MENUS:
        if not os.path.isfile(path):
            todo.append("write " + path)
        elif script not in open(path).read():
            todo.append("%s does not start %s (backed up, then rewritten)" % (path, script))
    if todo:
        return "TODO", "; ".join(todo)
    return "DONE", "%s, %s" % (L.DESKTOP, os.path.basename(L.DESKTOP_CLEAN))


def a_menu(c):
    n = 0
    for path, script, text in MENUS:
        if os.path.isfile(path) and script in open(path).read():
            continue
        if os.path.isfile(path):
            os.replace(path, path + ".bak")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text(c.game, c.shdir))
        n += 1
    return True, "%d written" % n


def s_motion(c):
    hw = c.motion_hw
    if hw is None:
        return "INFO", ("optional, not chosen: apply asks once; or: python3 %s motion-hw none|thanos|other"
                        % os.path.relpath(os.path.abspath(__file__), REPO))
    if hw == "none":
        return "DONE", "none: no setup help needed (set up any motion hardware in SimHub as usual)"
    if hw == "other":
        return "DONE", "other: set your motion hardware up in SimHub as usual (docs/INSTALL-LINUX.md 5)"
    return thanos_state(c)


def thanos_state(c):
    """Example option: read-only `simhub_thanos.py plan` of simhub-thanos-linux (scm_thanos_ext)."""
    r = TX.check(env=c.env)
    if r["state"] == "DONE":
        return "DONE", r["detail"]
    if r["state"] == "MISSING":
        return "TODO", "THANOS option: " + r["detail"]
    if r["state"] == "ERROR":
        return "BLOCKED", r["detail"]
    return "TODO", r["detail"]


DEPS = [
    ("D1", "prereqs", s_prereqs),
    ("D2", "star-citizen", s_game),
    ("D3", "dbox", s_dbox),
    ("D4", "simhub", s_simhub),
    ("D5", "descriptor", s_desc),
]
STEPS = [
    (1, "dbox-folder", s_folder, a_folder),
    (2, "dbox-config", s_config, None),
    (3, "dbox-original", s_orig, a_orig),
    (4, "cert-pair", s_pair, a_pair),
    (5, "scripts", s_scripts, a_scripts),
    (6, "footprint", s_footprint, a_footprint),
    (7, "simdef", s_simdef, a_simdef),
    (8, "plugin", s_plugin, a_plugin),
    (9, "menu", s_menu, a_menu),
    (10, "motion-hw", s_motion, None),
]


def check(fn, c):
    try:
        return fn(c)
    except Exception as e:  # noqa: BLE001
        return "BLOCKED", "check error: %s" % e


def show_locations(c):
    print("Star Citizen (LUG-Helper) prefixes found:")
    if not c.cands:
        print("  none")
    for x in c.cands:
        mark = "->" if c.game and os.path.realpath(x["path"]) == os.path.realpath(c.game) else "  "
        print("  %s %s  [%s]  LIVE %s, D-BOX connector %s" % (mark, x["path"], ", ".join(x["sources"]),
              "yes" if x["live"] else "no", "yes" if x["dbox"] else "no"))
    if c.game and not any(os.path.realpath(x["path"]) == os.path.realpath(c.game) for x in c.cands):
        print("  -> %s  [%s]" % (c.game, c.game_src))
    print("SimHub copies found:")
    if not c.simhubs:
        print("  none")
    for x in c.simhubs:
        print("  %s %s  %s" % ("->" if x["used"] else "  ", x["prefix"],
                              "(used: sc_start.py starts this one)" if x["used"] else "(not used)"))
    print()


def show_plan(c):
    print("sc_motion_install plan (read-only)  repo %s" % REPO)
    print()
    show_locations(c)
    print("Dependencies (must all be DONE before apply changes anything):")
    dstates = []
    for n, name, chk in DEPS:
        st, det = check(chk, c)
        dstates.append(st)
        print("  %-3s %-14s %-8s %s" % (n, name, st, det))
    print("Install steps:")
    states = []
    for n, name, chk, _ in STEPS:
        st, det = check(chk, c)
        states.append(st)
        print("  %-3s %-14s %-8s %s" % (n, name, st, det))
    env = L.script_env(c.game, c.shdir)
    if env:
        print()
        print("Your paths differ from the scripts' defaults. The menu entry sets them; for manual")
        print("commands in fish, run once:")
        for k, v in sorted(env.items()):
            print("  set -Ux %s '%s'" % (k, v))
    print()
    if any(x != "DONE" for x in dstates):
        print("RESULT: dependencies not met (%d of %d DONE); apply will change nothing until they are"
              % (dstates.count("DONE"), len(dstates)))
        return 6
    left = [x for x in states if x in ("TODO", "BLOCKED")]
    if not left:
        print("RESULT: all steps DONE; next: python3 %s" % os.path.join(REPO, "setup/linux/sc_motion_verify.py"))
        return 0
    print("RESULT: dependencies DONE; %d TODO, %d BLOCKED" % (states.count("TODO"), states.count("BLOCKED")))
    return 5


def do_apply(option, select, motion_hw=None):
    if L.scan(L.GAME_EXE):
        print("REFUSED: StarCitizen.exe is running. Quit the game first.")
        return 2
    picked, how, _, problem = L.pick_game_prefix(option, force_menu=select)
    if picked and how == "chosen in the menu":
        option = picked
    c = Ctx(option)
    if how == "chosen in the menu" and c.game:
        c.game_src = how
    bad = [(n, name, check(chk, c)) for n, name, chk in DEPS]
    bad = [x for x in bad if x[2][0] != "DONE"]
    if bad:
        show_locations(c)
        print("Dependencies not met, nothing was changed:")
        for n, name, (st, det) in bad:
            print("  %-3s %-14s %-8s %s" % (n, name, st, det))
        return 6
    cfg = L.load_config()
    hw, hw_how = MH.choose(motion_hw, cfg, menu=True)
    c.motion_hw = hw
    cfg.update({"game_prefix": c.game, "simhub_dir": c.shdir, "repo": REPO, MH.KEY: hw})
    L.save_config(cfg)
    print("Locations assigned (saved in %s):" % L.CONFIG)
    print("  Star Citizen prefix: %s" % c.game)
    print("  SimHub:              %s" % c.shdir)
    print("  Motion hardware:     %s (%s; optional)" % (MH.SHORT[hw], hw_how))
    for n, name, chk, act in STEPS:
        st, det = check(chk, c)
        if st == "TODO" and act:
            print("  %-3s %-14s applying: %s" % (n, name, det))
            ok, res = act(c)
            print("  %-3s %-14s %s %s" % (n, name, "done:" if ok else "FAILED:", res))
            if not ok:
                print("Stopped at step %s. Nothing after it was changed." % n)
                break
    print()
    return show_plan(Ctx(option))


def save_motion_hw(hw):
    """`motion-hw HW`: saves only the motion hardware choice; nothing else is changed."""
    cfg = L.load_config()
    old = MH.saved(cfg)
    cfg[MH.KEY] = hw
    L.save_config(cfg)
    print("Motion hardware: %s (was %s), saved in %s" % (MH.SHORT[hw], MH.SHORT[old] if old else "not chosen", L.CONFIG))
    st, det = check(s_motion, Ctx(motion_hw=hw))
    print("  10  motion-hw      %-8s %s" % (st, det))
    return 0


def main(argv):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("cmd", nargs="?")
    ap.add_argument("--game-prefix")
    ap.add_argument("--select", action="store_true")
    ap.add_argument("--motion-hw", dest="motion_hw")
    ap.add_argument("value", nargs="?")
    a, extra = ap.parse_known_args(argv[1:])
    if a.cmd == "motion-hw":
        if not MH.valid(a.value) or extra or a.select or a.motion_hw or a.game_prefix:
            print(__doc__)
            return 1
        return save_motion_hw(a.value)
    if a.cmd not in ("plan", "apply") or extra or a.value or (a.select and a.cmd != "apply") or \
            (a.motion_hw is not None and not MH.valid(a.motion_hw)):
        print(__doc__)
        return 1
    if a.cmd == "plan":
        return show_plan(Ctx(a.game_prefix, a.motion_hw))
    return do_apply(a.game_prefix, a.select, a.motion_hw)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
