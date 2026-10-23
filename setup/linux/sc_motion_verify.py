#!/usr/bin/env python3
"""sc_motion_verify.py - check that the Star Citizen -> SimHub motion setup is complete and
working on Linux. Prints PASS / WARN / FAIL per check and saves a report you can attach to an
issue (~/lm_capture/verify/verify_<time>.txt).

  python3 setup/linux/sc_motion_verify.py                 read-only checks (any time)
  python3 setup/linux/sc_motion_verify.py --live          + pipeline test without the game:
        starts the capture server, TLS self-test against the certificate the game trusts,
        starts the tee + bridge, sends all-zero frames and checks the SimHub packets against the registered
        game definition (size and signature),
        checks the tee copy for the SC Haptics plugin, then stops both.
        Needs: game, SimHub and the pipeline NOT running (so nothing can reach the rig).
  python3 setup/linux/sc_motion_verify.py --last-session  + reads the logs of the last
        "Start StarCitizen - Motion Enabled" run and the D-BOX connector log: did the game
        connect, register events and stream frames, and did the bridge see you seated?

Read-only except --live, which only starts and stops the capture server and the bridge (the
same programs sc_start.py starts) and writes logs to ~/lm_capture/verify/.
Exit codes: 0 PASS, 9 WARN, 10 FAIL, 1 usage, 2 --live refused (something running).
Test override (stand-ins only): SC_VERIFY_CAPTURE_SCRIPT replaces ~/SC_Motion_Server.py for --live.
"""
import glob, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(REPO, "setup", "common"), os.path.join(REPO, "tools")]
import scm_common as C  # noqa: E402
import scm_checks as K  # noqa: E402
import scm_linux as L  # noqa: E402
import scm_motion_hw as MH  # noqa: E402
import scm_thanos_ext as TX  # noqa: E402
import scm_manifest as M  # noqa: E402
import scm_simdef as SD  # noqa: E402
import repo_link  # noqa: E402

H = L.HOME
CRT, KEY = os.path.join(H, "lm.crt"), os.path.join(H, "lm.key")
ORIG = os.path.join(H, "livemotion.crt.orig")
SERVER = os.path.join(H, C.SERVER_SCRIPT)
REPO_DESC = os.path.join(REPO, "scripts", C.DESC_FILE)
VERIFY_DIR = os.path.join(H, "lm_capture", "verify")
LAUNCH_DIR = os.path.join(H, "lm_capture", "launch")


def tool(script, *args, env=None, timeout=120):
    p = os.path.join(H, script)
    if not os.path.exists(p):
        return None, "%s not found" % p
    try:
        r = subprocess.run([sys.executable, p] + list(args), capture_output=True, text=True,
                           env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 99, "timeout"
    return r.returncode, (r.stdout + r.stderr).strip()


def last(out):
    lines = [x for x in (out or "").splitlines() if x.strip()]
    return lines[-1].strip() if lines else ""


def port_owner(port, proto="t"):
    if not L.which("ss"):
        return ""
    r = subprocess.run(["ss", "-H", "-%slnp" % proto, "sport = :%d" % port], capture_output=True, text=True)
    m = re.search(r'users:\(\("([^"]+)",pid=(\d+)', r.stdout)
    return "%s pid %s" % (m.group(1), m.group(2)) if m else (r.stdout.strip()[:80] if r.stdout.strip() else "")


# ----------------------------------------------------------------------------- sections
def sec_system(R):
    v = sys.version_info
    R.add("PASS" if v >= (3, 9) else "FAIL", "system", "python", "%d.%d.%d" % v[:3], "python 3.9 or newer")
    for mod, ver in C.python_modules().items():
        R.add("PASS" if ver else "FAIL", "system", "module " + mod, ver or "missing",
              "sudo pacman -S --needed python-grpcio python-protobuf")
    o = C.find_openssl()
    R.add("PASS" if o else "FAIL", "system", "openssl", o or "missing", "sudo pacman -S --needed openssl")
    R.add("INFO", "system", "espeak-ng (spoken prompts)", L.which("espeak-ng") or "not installed (optional)")
    R.add("INFO", "system", "dotnet (plugin build)", L.which("dotnet") or "not installed (needed to rebuild the plugin)")


def sec_repo(R):
    st = repo_link.states(H, M.is_install_item)
    if not st:
        R.add("WARN", "repo", "link map", "no tools/linkmap.json", "python3 tools/repo_collect.py apply")
        return
    c = {}
    for x in st:
        c[x[3]] = c.get(x[3], 0) + 1
    bad = {k: v for k, v in c.items() if k != "LINKED"}
    if not bad:
        R.add("PASS", "repo", "bridge/plugin files linked", "%d of %d" % (len(st), len(st)))
    else:
        sev = "FAIL" if ("MISSING" in bad or "NO-REPO-FILE" in bad) else "WARN"
        R.add(sev, "repo", "bridge/plugin files linked",
              ", ".join("%s %d" % kv for kv in sorted(c.items())),
              "python3 setup/linux/sc_motion_install.py apply (step 9), or tools/repo_link.py link --adopt-home")
    other = [x for x in repo_link.states(H) if not M.is_install_item(x[0])]
    if other:
        R.add("INFO", "repo", "other scripts in the repo", "%d (not needed to run; %d linked in ~)" % (
            len(other), len([x for x in other if x[3] == "LINKED"])))
    errs = []
    for rp, h, r, s in st:
        if rp.endswith(".py") and os.path.exists(h):
            ok, det = C.compile_ok(h)
            if not ok:
                errs.append("%s %s" % (os.path.basename(h), det))
    R.add("FAIL" if errs else "PASS", "repo", "scripts compile", "; ".join(errs) or "all .py files compile")
    if os.path.isdir(os.path.join(REPO, ".git")):
        r = subprocess.run(["git", "-C", REPO, "status", "--short"], capture_output=True, text=True)
        n = len([x for x in r.stdout.splitlines() if x.strip()])
        R.add("INFO", "repo", "uncommitted changes", "%d (git -C %s status)" % (n, REPO) if n else "none")


def sec_game(R):
    gp, how, cands, problem = L.choose_game_prefix()
    if len(cands) > 1:
        R.add("INFO", "game", "LUG prefixes found", "; ".join(x["path"] for x in cands))
    if not gp:
        R.add("FAIL", "game", "LUG prefix", problem,
              "python3 setup/linux/sc_motion_install.py apply --game-prefix PATH (assigns it)")
        return None
    R.add("PASS", "game", "LUG prefix", "%s (%s)" % (gp, how))
    launch = os.path.join(gp, "sc-launch.sh")
    wp = None
    try:
        for line in open(launch):
            m = re.match(r'\s*export\s+wine_path="([^"]+)"', line)
            if m:
                wp = m.group(1)
    except OSError:
        pass
    ok = wp and os.path.exists(os.path.join(wp, "wineserver"))
    R.add("PASS" if ok else "WARN", "game", "sc-launch.sh runner", wp or "wine_path not found",
          "LUG-Helper: re-select the runner (sc_start.py needs wineserver for auto-close)")
    exe = os.path.join(gp, L.LIVE_REL, "Bin64", "StarCitizen.exe")
    R.add("PASS" if os.path.isfile(exe) else "WARN", "game", "Star Citizen LIVE installed",
          "yes" if os.path.isfile(exe) else "StarCitizen.exe not found", "install LIVE in the RSI Launcher")
    env = L.script_env(gp, L.simhub_dir())
    if env:
        miss = [k for k, v in env.items() if os.environ.get(k) != v]
        R.add("WARN" if miss else "PASS", "game", "env for non-default paths",
              "not set in this shell: " + ", ".join(miss) if miss else "set",
              "fish: set -Ux " + "; set -Ux ".join("%s '%s'" % (k, env[k]) for k in miss) if miss else "")
    return gp


def sec_dbox(R, gp):
    if not gp:
        R.add("SKIP", "dbox", "D-BOX connector", "no game prefix")
        return
    dc = os.path.join(gp, "drive_c")
    con = os.path.join(C.connector_dir(dc), "dbxLive64.dll")
    R.add("PASS" if os.path.isfile(con) else "FAIL", "dbox", "HaptiSync connector installed",
          "LiveMotionConnector present" if os.path.isfile(con) else "missing",
          "install D-BOX HaptiSync Center in the game prefix (docs/INSTALL-LINUX.md)")
    gdl = os.path.join(C.game_dir(dc), "dbxLive64.dll")
    miss = C.game_folder_missing(dc)
    R.add("PASS" if os.path.isfile(gdl) and not miss else "FAIL", "dbox", "StarCitizen connector folder",
          "complete" if os.path.isfile(gdl) and not miss else "missing %s" % (miss or "folder"),
          "python3 setup/linux/sc_motion_install.py apply (step 4)")
    if os.path.isdir(C.game_dir(dc)):
        ok, det = C.connector_config_check(C.game_dir(dc))
        R.add("PASS" if ok else "WARN", "dbox", "connector config", det,
              "compare with LiveMotionConnector/LiveMotionConnector.config")
    gcopy = os.path.join(C.game_dir(dc), "livemotion.crt")
    copies = C.dbox_cert_copies(dc)
    csha = C.sha256(CRT) if os.path.isfile(CRT) else None
    kinds = {"original": 0, "custom": 0, "other": 0}
    for p in copies:
        h = C.sha256(p)
        kinds["original" if h == C.DBOX_ORIG_SHA else "custom" if h == csha else "other"] += 1
    R.add("INFO", "dbox", "livemotion.crt copies", "%d: %d D-BOX original, %d ~/lm.crt, %d other" % (
        len(copies), kinds["original"], kinds["custom"], kinds["other"]))
    if kinds["other"]:
        R.add("WARN", "dbox", "unknown certificate copies", "%d copies are neither" % kinds["other"],
              "python3 ~/sc_lmcert.py status")
    logp = os.path.join(gp, L.LIVE_REL, "LiveMotionConnector.log")
    if os.path.isfile(logp):
        t = open(logp, errors="replace").read().splitlines()[-400:]
        bad = [x for x in t if re.search(r"certificate verify failed|Handshake failed|UNAVAILABLE|error", x, re.I)]
        R.add("WARN" if bad else "INFO", "dbox", "connector log (game side)",
              (bad[-1].strip()[:110] if bad else "no errors in the last 400 lines"),
              "if the game ran with the pipeline up: python3 ~/sc_lmcert.py status")
    else:
        R.add("INFO", "dbox", "connector log (game side)", "none yet (written when the game runs)")


def sec_cert(R, gp):
    env = L.full_env(gp, L.simhub_dir())
    ok = os.path.isfile(ORIG) and C.sha256(ORIG) == C.DBOX_ORIG_SHA
    R.add("PASS" if ok else "FAIL", "cert", "~/livemotion.crt.orig", "known D-BOX original" if ok else
          "missing or different", "python3 setup/linux/sc_motion_install.py apply (step 6)")
    rc, out = tool("sc_lmcert.py", "check", env=env)
    st = {0: "PASS", 9: "WARN"}.get(rc, "FAIL")
    R.add(st, "cert", "sc_lmcert.py check", last(out) if rc is not None else out,
          "python3 ~/sc_lmcert.py status; after a game/D-BOX update: python3 ~/sc_lmcert.py repair (game closed)")
    desc = C.server_desc_path(SERVER, H)
    ok, det = C.desc_check(desc)
    if ok and os.path.isfile(REPO_DESC) and C.sha256(desc) != C.sha256(REPO_DESC):
        ok, det = False, "%s differs from the repository's scripts/%s" % (desc, C.DESC_FILE)
    R.add("PASS" if ok else "FAIL", "cert", C.DESC_FILE, det,
          "python3 setup/linux/sc_motion_install.py apply (step 5); check: python3 tools/motion_schema.py check")


def sec_simhub(R):
    shd = L.simhub_dir()
    others = [x["prefix"] for x in L.find_simhub_copies() if not x["used"]]
    if others:
        R.add("INFO", "simhub", "other SimHub copies (not used)", "; ".join(others))
    for p, what in ((L.SIMHUB_PREFIX, "prefix"), (L.SIMHUB_WINE, "wine"), (os.path.join(shd, "SimHubWPF.exe"), "SimHubWPF.exe")):
        R.add("PASS" if os.path.exists(p) else "FAIL", "simhub", what, p,
              "install SimHub with linux-simracing-utils into ~/linux-simracing-utils")
    st, det = SD.state(L.simdef_place(REPO, shd))
    R.add({"DONE": "PASS", "TODO": "FAIL"}.get(st, "FAIL"), "simhub", "game definition 'Star Citizen'", det,
          "python3 setup/linux/sc_motion_install.py apply (step 7), with SimHub closed")
    sev, det = SD.active_game_text(shd)
    R.add(sev, "simhub", "active game", det, "SimHub: game list (top left) -> Star Citizen")
    rc, out = tool("sc_haptics_install.py", "check", env=L.full_env(None, shd))
    R.add("PASS" if rc == 0 else "WARN", "simhub", "SC Haptics plugin",
          (last(out) if rc is not None else out), "python3 setup/linux/sc_motion_install.py apply (step 8)")
    cfg = os.path.join(H, "sc_haptics", "effects.json")
    if os.path.exists(cfg):
        ok, det = C.json_ok(cfg)
        R.add("PASS" if ok else "FAIL", "simhub", "effects.json", det, "fix the JSON (the plugin keeps the last good one)")
    else:
        R.add("WARN", "simhub", "effects.json", "missing", "python3 ~/sc_haptics_install.py install")


def usb_com_ports():
    """[(COMnn, target, present)] for the USB serial COM links in SimHub's prefix."""
    dd = os.path.join(L.SIMHUB_PREFIX, "dosdevices")
    ports = []
    for p in sorted(glob.glob(os.path.join(dd, "com*"))):
        try:
            tgt = os.readlink(p)
        except OSError:
            continue
        if "/dev/ttyS" in tgt:
            continue
        ports.append((os.path.basename(p).upper(), tgt, os.path.exists(tgt)))
    return ports


def sec_motion(R):
    """Optional motion hardware (installer step 10). Hardware checks only for the choice made;
    connecting the platform in SimHub is up to you and is not checked here."""
    hw = MH.saved(L.load_config())
    R.add("INFO", "motion", "motion hardware", "%s%s" % (
        MH.SHORT[hw] if hw else "not chosen", "" if hw == "thanos" else
        " (optional; set up in SimHub as usual)"),
        "" if hw else "python3 setup/linux/sc_motion_install.py motion-hw none|thanos|other")
    if hw == "none":
        return
    ports = usb_com_ports()
    show = "; ".join("%s->%s%s" % (n, t.replace("/dev/serial/by-id/", "by-id/"), "" if e else " (NOT PRESENT)")
                     for n, t, e in ports)
    if hw != "thanos":  # other / not chosen: information only, never WARN or FAIL
        if ports:
            R.add("INFO", "motion", "USB serial COM ports in SimHub's prefix", show)
        return
    # example option: THANOS AMC, checked with simhub-thanos-linux's read-only `plan`
    r = TX.check()
    if r["state"] == "DONE":
        R.add("PASS", "motion", "THANOS COM port", r["detail"])
    elif r["state"] == "MISSING":
        R.add("INFO", "motion", "THANOS COM port", "not checked: simhub-thanos-linux is not installed"
              + ("; COM links: " + show if ports else ""), TX.INSTALL)
    else:
        R.add("WARN", "motion", "THANOS COM port", r["detail"],
              "python3 %s plan (docs/THANOS-LINUX.md)" % r["tool"])


def sec_launch(R):
    if os.path.isfile(L.DESKTOP):
        t = open(L.DESKTOP).read()
        m = re.search(r"^Exec=(.*)$", t, re.M)
        ex = m.group(1) if m else ""
        path = re.findall(r"(\S*sc_start\.py)", ex)
        ok = bool(path) and os.path.exists(path[-1].strip('"'))
        R.add("PASS" if ok else "FAIL", "launch", "menu entry", ex[:100],
              "python3 setup/linux/sc_motion_install.py apply (step 9)")
    else:
        R.add("FAIL", "launch", "menu entry", "missing " + L.DESKTOP, "python3 setup/linux/sc_motion_install.py apply")
    ok = os.path.isfile(L.DESKTOP_CLEAN) and "sc_motion_clean.py" in open(L.DESKTOP_CLEAN).read()
    R.add("PASS" if ok else "WARN", "launch", "remove-setup menu entry",
          L.DESKTOP_CLEAN if ok else "missing " + L.DESKTOP_CLEAN, "python3 setup/linux/sc_motion_install.py apply (step 9)")


def sec_runtime(R):
    game = L.scan(L.GAME_EXE)
    simhub = L.scan(L.SIMHUB_EXE)
    pipe = L.scan(*L.PIPELINE)
    blk = L.scan(*L.BLOCKERS)
    names = sorted({L._program(c.split(" ")) for _, c in pipe})
    R.add("INFO", "runtime", "running now", "game %s, SimHub %s, pipeline %s" % (
        "yes" if game else "no", "yes" if simhub else "no", ", ".join(names) or "no"))
    if blk:
        R.add("WARN", "runtime", "test/replay tools running", "; ".join(c[:60] for _, c in blk),
              "sc_start.py refuses to start while these run")
    if not pipe:
        busy = []
        if C.tcp_listening_local(C.PORT_CAPTURE_TCP):
            busy.append("tcp %d (%s)" % (C.PORT_CAPTURE_TCP, port_owner(C.PORT_CAPTURE_TCP) or "?"))
        for p in (C.PORT_TEE_UDP, C.PORT_PLUGIN_UDP, C.PORT_BRIDGE_UDP):
            if not C.udp_free(p):
                if p == C.PORT_PLUGIN_UDP and simhub:
                    continue
                busy.append("udp %d (%s)" % (p, port_owner(p, "u") or "?"))
        R.add("FAIL" if busy else "PASS", "runtime", "pipeline ports free", ", ".join(busy) or "61666, 61700-61702",
              "another program holds the port; stop it (D-BOX Motion Engine must not use 61666)")
    return bool(game), bool(simhub), bool(pipe)


# ----------------------------------------------------------------------------- live test
def live(R, running):
    game, simhub, pipe = running
    if game or simhub or pipe:
        R.add("FAIL", "live", "preconditions", "close the game, SimHub and the pipeline first "
              "(python3 ~/sc_start.py stop)")
        return 2
    sys.path.insert(0, H)
    try:
        import sc_start as S
    except Exception as e:  # noqa: BLE001
        R.add("FAIL", "live", "import ~/sc_start.py", str(e))
        return 0
    cap = os.environ.get("SC_VERIFY_CAPTURE_SCRIPT", S.CAPTURE_SCRIPT)
    env = L.full_env(L.detect_game_prefix()[0], L.simhub_dir())
    K.live_pipeline(R, S, cap, env, VERIFY_DIR, lambda: tool("sc_lmcert.py", "selftest", env=env))
    return 0


def main(argv):
    args = set(argv[1:])
    if not args <= {"--live", "--last-session", "--no-save"}:
        print(__doc__)
        return 1
    R = C.Report("sc_motion_verify (Linux)")
    print("sc_motion_verify  repo %s  home %s" % (REPO, H))
    sec_system(R)
    sec_repo(R)
    gp = sec_game(R)
    sec_dbox(R, gp)
    sec_cert(R, gp)
    sec_simhub(R)
    sec_motion(R)
    sec_launch(R)
    running = sec_runtime(R)
    rc = 0
    if "--live" in args:
        rc = live(R, running)
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
