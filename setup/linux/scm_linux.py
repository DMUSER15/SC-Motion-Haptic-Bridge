"""scm_linux.py - Linux path detection, processes and settings for the sc-motion-bridge
installer and verifier. Read-only except save_config().

Paths the runtime scripts use (env variable, else the location saved by apply in
~/.config/sc-motion/config.json, else the default):
  sc_start.py       game prefix: env SC_GAME_PREFIX, saved game_prefix, default ~/Games/star-citizen
                    (LUG-Helper's default); SimHub prefix ~/linux-simracing-utils/pfx (fixed),
                    wine .../var/wine/bin/wine
  sc_certswap.py    env SC_CERT_DRIVE_C, saved game_prefix + /drive_c, default ~/Games/star-citizen/drive_c
  sc_lmcert.py      ~/lm.crt, ~/lm.key, ~/lm_certs, imports sc_certswap.py
  sc_haptics_install.py  env SC_SIMHUB_DIR, saved simhub_dir, default ~/linux-simracing-utils/.../SimHub
  SC_Motion_Server.py  motion_desc.bin next to it (repository scripts/), ~/lm.crt, ~/lm.key
On a machine where those defaults are wrong, the installer writes the menu entry with the
env variables set, and prints `set -Ux` lines for fish so manual commands work too.
"""
import glob, json, os, shutil

HOME = os.path.expanduser("~")
USER = os.path.basename(HOME)
SCRIPT_DEFAULT_GAME = os.path.join(HOME, "Games", "star-citizen")
SCRIPT_DEFAULT_SIMHUB_DIR = os.path.join(HOME, "linux-simracing-utils", "pfx", "drive_c", "Program Files (x86)", "SimHub")
SIMHUB_PREFIX = os.path.join(HOME, "linux-simracing-utils", "pfx")
SIMHUB_WINE = os.path.join(HOME, "linux-simracing-utils", "var", "wine", "bin", "wine")
SIMHUB_DIR_DEFAULT = os.path.join(SIMHUB_PREFIX, "drive_c", "Program Files (x86)", "SimHub")
CONFIG = os.path.join(HOME, ".config", "sc-motion", "config.json")
# LUG-Helper keeps the chosen prefix in this file (Probable; used only as a hint)
LUG_WINEDIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.join(HOME, ".config")),
                           "starcitizen-lug", "winedir.conf")
DESKTOP = os.path.join(HOME, ".local", "share", "applications", "sc-start-motion.desktop")
DESKTOP_CLEAN = os.path.join(HOME, ".local", "share", "applications", "sc-motion-clean.desktop")
LIVE_REL = os.path.join("drive_c", "Program Files", "Roberts Space Industries", "StarCitizen", "LIVE")

GAME_EXE = "starcitizen.exe"
LAUNCHER_EXE = "rsi launcher.exe"
SIMHUB_EXE = "simhubwpf.exe"
PIPELINE = ("sc_motion_server.py", "lm_server2.py", "lm_simhub.py", "lm_simhub2.py", "lm_simhub3.py", "lm_simhub3t.py",
            "lm_simhub4.py", "lm_simhub4t.py")
BLOCKERS = ("lmrec.py", "lm_bridge.py", "lmcapture.py", "lm_replay.py", "lm_replay2.py", "sc_feedtest.py")


def load_config():
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, "w") as f:
        json.dump(cfg, f, indent=1)
        f.write("\n")


def is_game_prefix(p):
    return bool(p) and os.path.isdir(os.path.join(p, "drive_c")) and os.path.isfile(os.path.join(p, "sc-launch.sh"))


def game_prefix_candidates():
    """Places a LUG prefix may be, with where the idea came from (not yet validated)."""
    out = []
    try:
        with open(LUG_WINEDIR) as f:
            line = f.readline().strip()
        if line:
            out.append((line, "LUG-Helper winedir.conf"))
    except OSError:
        pass
    out.append((os.path.join(HOME, "Games", "star-citizen"), "LUG-Helper default"))
    for base in sorted(glob.glob("/run/media/%s/*" % USER)) + sorted(glob.glob("/mnt/*")):
        for depth in ("*", "*/*", "*/*/*"):
            for p in sorted(glob.glob(os.path.join(base, depth, "sc-launch.sh"))):
                out.append((os.path.dirname(p), "found on %s" % base))
    out.append((SCRIPT_DEFAULT_GAME, "sc_start.py default"))
    return out


def find_game_prefixes():
    """Every valid LUG prefix (drive_c + sc-launch.sh), de-duplicated by real path:
    [{"path", "sources", "live", "dbox"}]. Read-only."""
    seen = {}
    for p, src in game_prefix_candidates():
        if not is_game_prefix(p):
            continue
        rp = os.path.realpath(p)
        if rp not in seen:
            seen[rp] = {"path": os.path.abspath(p), "sources": [],
                        "live": os.path.isfile(os.path.join(p, LIVE_REL, "Bin64", "StarCitizen.exe")),
                        "dbox": os.path.isfile(os.path.join(p, "drive_c", "ProgramData", "D-BOX", "Gaming",
                                                            "LiveMotion", "LiveMotionConnector", "dbxLive64.dll"))}
        if src not in seen[rp]["sources"]:
            seen[rp]["sources"].append(src)
    return list(seen.values())


def choose_game_prefix(option=None):
    """Which prefix to use. Returns (path or None, how, candidates, problem).
    An explicit choice wins: --game-prefix, then env SC_GAME_PREFIX, then the saved config.
    Without one: exactly one valid prefix is used; none or several -> problem."""
    cands = find_game_prefixes()
    for value, how in ((option, "--game-prefix"), (os.environ.get("SC_GAME_PREFIX"), "env SC_GAME_PREFIX"),
                       (load_config().get("game_prefix"), "saved in ~/.config/sc-motion/config.json")):
        if value:
            if is_game_prefix(value):
                return os.path.abspath(value), how, cands, None
            return None, how, cands, "%s (%s) is not a LUG prefix (needs drive_c and sc-launch.sh)" % (value, how)
    if len(cands) == 1:
        return cands[0]["path"], "only prefix found (%s)" % ", ".join(cands[0]["sources"]), cands, None
    if not cands:
        return None, "", cands, "no LUG-Helper prefix found (needs drive_c and sc-launch.sh)"
    return None, "", cands, "%d Star Citizen prefixes found; apply shows a menu to choose one (or use --game-prefix)" % len(cands)


def suggested_prefix(cands):
    """Index of the prefix most likely to be the one in use: named by LUG-Helper's own config,
    has the D-BOX connector, has LIVE installed."""
    def score(x):
        return (4 if "LUG-Helper winedir.conf" in x["sources"] else 0) + (2 if x["dbox"] else 0) + \
               (1 if x["live"] else 0) + (1 if "sc_start.py default" in x["sources"] else 0)
    return max(range(len(cands)), key=lambda i: score(cands[i])) if cands else 0


def describe_prefix(x):
    return "%s\n       [%s]  LIVE %s, D-BOX connector %s" % (
        x["path"], ", ".join(x["sources"]), "yes" if x["live"] else "no", "yes" if x["dbox"] else "no")


def pick_game_prefix(option=None, force_menu=False):
    """Like choose_game_prefix, but in a terminal shows a numbered menu when several prefixes
    are found and none is chosen yet (or always with force_menu). Returns the same tuple."""
    import scm_ui
    p, how, cands, problem = choose_game_prefix(option)
    if option or not cands or not scm_ui.interactive():
        return p, how, cands, problem
    if p is None and len(cands) > 1 or force_menu:
        cur = [i for i, x in enumerate(cands) if p and os.path.realpath(x["path"]) == os.path.realpath(p)]
        i = scm_ui.choose_one("Which Star Citizen prefix should be used?",
                              [describe_prefix(x) for x in cands], cur[0] if cur else suggested_prefix(cands))
        if i is None:
            return None, "", cands, "no prefix chosen"
        return cands[i]["path"], "chosen in the menu", cands, None
    return p, how, cands, problem


def detect_game_prefix():
    """(path, how) of the prefix in use, or (None, problem). Never guesses between several."""
    p, how, _, problem = choose_game_prefix()
    return (p, how) if p else (None, problem)


SIMHUB_REL = (("Program Files (x86)", "SimHub", "SimHubWPF.exe"), ("Program Files", "SimHub", "SimHubWPF.exe"))


def find_simhub_copies(extra_prefixes=()):
    """Every SimHubWPF.exe in Wine/Proton prefixes we know how to find:
    [{"exe", "prefix", "used"}]; "used" = the linux-simracing-utils prefix sc_start.py starts."""
    roots = [SIMHUB_PREFIX, os.path.join(HOME, ".wine")]
    roots += glob.glob(os.path.join(HOME, "Games", "*"))
    for steam in (os.path.join(HOME, ".local", "share", "Steam"), os.path.join(HOME, ".steam", "steam"),
                  os.path.join(HOME, ".var", "app", "com.valvesoftware.Steam", ".local", "share", "Steam")):
        roots += glob.glob(os.path.join(steam, "steamapps", "compatdata", "*", "pfx"))
    for base in glob.glob("/run/media/%s/*" % USER) + glob.glob("/mnt/*"):
        roots += glob.glob(os.path.join(base, "steamapps", "compatdata", "*", "pfx"))
        roots += glob.glob(os.path.join(base, "*", "steamapps", "compatdata", "*", "pfx"))
    roots += glob.glob(os.path.join(HOME, ".local", "share", "bottles", "bottles", "*"))
    roots += glob.glob(os.path.join(HOME, ".var", "app", "com.usebottles.bottles", "data", "bottles", "bottles", "*"))
    roots += list(extra_prefixes)
    seen, out = set(), []
    used = os.path.realpath(SIMHUB_PREFIX)
    for r in roots:
        for rel in SIMHUB_REL:
            exe = os.path.join(r, "drive_c", *rel)
            if os.path.isfile(exe):
                rp = os.path.realpath(exe)
                if rp in seen:
                    continue
                seen.add(rp)
                out.append({"exe": exe, "prefix": r, "used": os.path.realpath(r) == used})
    return out


def simhub_dir():
    return os.environ.get("SC_SIMHUB_DIR") or load_config().get("simhub_dir") or SIMHUB_DIR_DEFAULT


def drive_c_of(shdir):
    """The Wine drive_c that holds this SimHub folder (SimHub's prefix by default)."""
    parts = os.path.abspath(shdir).split(os.sep)
    if "drive_c" in parts:
        i = len(parts) - 1 - parts[::-1].index("drive_c")
        return os.sep.join(parts[:i + 1]) or os.sep
    return os.path.join(SIMHUB_PREFIX, "drive_c")


def simdef_place(repo, shdir):
    """scm_simdef.Place for this machine: the definition goes into <SimHub>/PluginsData/Common/
    ExternalSims, the registration into the Wine user's AppData/Local in the same prefix."""
    import scm_simdef
    dc = drive_c_of(shdir)
    la = scm_simdef.wine_local_appdata(os.path.dirname(dc), USER)
    return scm_simdef.Place(repo, shdir, la, drive_c=dc)


def script_env(game_prefix, shdir):
    """Env variables the runtime scripts need for these paths. None when a path is the scripts'
    default, or is the one saved in ~/.config/sc-motion/config.json (since kit 1.6 the scripts
    read that file themselves)."""
    env = {}
    cfg = load_config()

    def same(a, b):
        return bool(a) and bool(b) and os.path.abspath(a) == os.path.abspath(b)
    if game_prefix and not same(game_prefix, SCRIPT_DEFAULT_GAME) and not same(game_prefix, cfg.get("game_prefix")):
        env["SC_GAME_PREFIX"] = game_prefix
        env["SC_CERT_DRIVE_C"] = os.path.join(game_prefix, "drive_c")
    if shdir and not same(shdir, SCRIPT_DEFAULT_SIMHUB_DIR) and not same(shdir, cfg.get("simhub_dir")):
        env["SC_SIMHUB_DIR"] = shdir
    return env


def full_env(game_prefix, shdir):
    e = dict(os.environ)
    e.update(script_env(game_prefix, shdir))
    if game_prefix:
        e.setdefault("SC_CERT_DRIVE_C", os.path.join(game_prefix, "drive_c"))
        e.setdefault("SC_GAME_PREFIX", game_prefix)
    return e


def _base(arg):
    return arg.replace("\\", "/").rsplit("/", 1)[-1].lower()


def _program(argv):
    first = _base(argv[0])
    if first.startswith("python"):
        for a in argv[1:]:
            if not a.startswith("-"):
                return _base(a)
    return first


def scan(*needles):
    """[(pid, cmdline)] of processes whose program is one of needles (same rule as sc_start.py)."""
    me = os.getpid()
    hits = []
    for d in os.listdir("/proc"):
        if not d.isdigit() or int(d) == me:
            continue
        try:
            with open("/proc/%s/cmdline" % d, "rb") as f:
                raw = f.read()
        except OSError:
            continue
        argv = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
        if argv and _program(argv) in needles:
            hits.append((int(d), " ".join(argv)))
    return hits


def which(name):
    return shutil.which(name)


def _exec_line(game_prefix, shdir, script, *args):
    env = script_env(game_prefix, shdir)

    def q(s):
        s = s.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
        return '"%s"' % s.replace("%", "%%") if (" " in s or "%" in s) else s
    parts = ["env", "SC_START_HOLD=1"] + [q("%s=%s" % kv) for kv in sorted(env.items())]
    parts += ["python3", q(os.path.join(HOME, script))] + list(args)
    return " ".join(parts)


def desktop_entry(game_prefix, shdir):
    """Text of the app-menu entry. Same as the working one when the paths are the defaults."""
    return ("[Desktop Entry]\nType=Application\nVersion=1.0\n"
            "Name=Start StarCitizen - Motion Enabled\n"
            "Comment=Start the capture server, bridge and SimHub, check them, then the RSI Launcher\n"
            "Exec=%s\nPath=%s\nTerminal=true\nIcon=rsi-launcher\nCategories=Game;\n"
            % (_exec_line(game_prefix, shdir, "sc_start.py"), HOME))


def clean_desktop_entry(game_prefix, shdir):
    """Text of the app-menu entry that removes the motion setup's changes (sc_motion_clean.py)."""
    return ("[Desktop Entry]\nType=Application\nVersion=1.0\n"
            "Name=Star Citizen - Remove Motion Setup\n"
            "Comment=Put D-BOX's original certificates back and remove the connector folder, "
            "SimHub plugin and motion menu entry (backups kept)\n"
            "Exec=%s\nPath=%s\nTerminal=true\nIcon=rsi-launcher\nCategories=Game;\n"
            % (_exec_line(game_prefix, shdir, "sc_motion_clean.py", "apply"), HOME))
