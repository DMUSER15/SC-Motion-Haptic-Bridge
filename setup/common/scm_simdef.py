"""scm_simdef.py - SimHub's game definition for Star Citizen ("Star Citizen.simdef"): check,
install and register it. Shared by the Linux and Windows installers and verifiers.

SimHub finds an External Sim through a registration file
    <LocalAppData>\\SimHub\\ExternalSims\\Registrations\\<DefinitionUniqueId>.simlink
holding one line: the full Windows path of the .simdef (SimHub's own demo code writes it the
same way). On Linux, <LocalAppData> is inside SimHub's Wine prefix
(drive_c/users/<user>/AppData/Local) and the path in the file is a Windows path (C:\\...).

Policy:
  - a registered definition that the bridge (scripts/lm_simhub4.py) can use is DONE and is
    never overwritten, even if it differs from the repository copy (you may have changed it in
    SimHub's Game Definition Editor);
  - otherwise the repository copy (config/simhub/Star Citizen.simdef) is copied into SimHub's
    PluginsData\\Common\\ExternalSims folder and registered; an existing file or registration is
    first renamed to <name>.bak-YYYYmmdd-HHMMSS (never deleted).
SimHub must be closed while this is applied (it watches these files).
"""
import json, os, shutil, sys, time

SIMDEF_UUID = "2f10c0be-479b-4b35-8671-a1b16ce4b874"
SIMDEF_NAME = "Star Citizen.simdef"
REPO_REL = os.path.join("config", "simhub", SIMDEF_NAME)
EXTERNAL_REL = os.path.join("PluginsData", "Common", "ExternalSims")
REG_REL = os.path.join("SimHub", "ExternalSims", "Registrations", SIMDEF_UUID + ".simlink")


def bridge_module(scripts_dir):
    """lm_simhub4 from scripts_dir (the repository's scripts/ or the Windows install folder)."""
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import lm_simhub4  # noqa: E402
    return lm_simhub4


def inspect(path, scripts_dir):
    """-> (ok, text): can the bridge use this .simdef? Read-only."""
    if not path or not os.path.isfile(path):
        return False, "no file %s" % path
    try:
        v4 = bridge_module(scripts_dir)
        sd = v4.load_simdef(path)
    except SystemExit as e:
        return False, str(e).splitlines()[0].replace(" Set LM_SIMDEF to its path.", "")
    except Exception as e:  # noqa: BLE001
        return False, "cannot check it (%s)" % e
    try:
        with open(path) as fh:
            rev = json.load(fh).get("ManagerVersion")
    except (OSError, ValueError):
        rev = None
    return True, "%d fields, signature 0x%08X%s" % (len(sd["fields"]), sd["tele_sig"],
                                                    (", data conversion revision %s" % rev) if rev else "")


def win_to_local(win_path, drive_c=None):
    """Windows path from a .simlink -> local path. On Linux drive_c is the Wine prefix's
    drive_c (C: there, Z: = /); on Windows (drive_c None) the path is returned unchanged."""
    if drive_c is None or not win_path:
        return win_path
    if len(win_path) > 2 and win_path[1] == ":":
        rest = win_path[2:].replace("\\", "/").lstrip("/")
        if win_path[0].upper() == "C":
            return os.path.join(drive_c, rest)
        if win_path[0].upper() == "Z":
            return "/" + rest
    return None


def local_to_win(path, drive_c=None):
    """Local path -> the Windows path written into the .simlink."""
    if drive_c is None:
        return os.path.abspath(path)
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(drive_c))
    if rel.startswith(".."):
        return "Z:" + os.path.abspath(path).replace("/", "\\")
    return "C:\\" + rel.replace("/", "\\")


def wine_local_appdata(prefix, user):
    """drive_c/users/<user>/AppData/Local in a Wine prefix: an existing one that already has a
    SimHub folder wins (Wine's user name may differ from the login name), else <user>."""
    users = os.path.join(prefix, "drive_c", "users")
    try:
        names = sorted(os.listdir(users))
    except OSError:
        names = []
    for n in names:
        la = os.path.join(users, n, "AppData", "Local")
        if os.path.isdir(os.path.join(la, "SimHub")):
            return la
    return os.path.join(users, user, "AppData", "Local")


class Place:
    """Where things are on this machine."""

    def __init__(self, repo, simhub_dir, local_appdata, drive_c=None, scripts_dir=None):
        """repo may be None for check_registered() (scripts_dir is then required)."""
        self.repo = repo
        self.simhub_dir = simhub_dir
        self.local_appdata = local_appdata
        self.drive_c = drive_c                     # None on Windows
        self.scripts_dir = scripts_dir or os.path.join(repo, "scripts")
        self.repo_simdef = os.path.join(repo, REPO_REL) if repo else None
        self.target = os.path.join(simhub_dir, EXTERNAL_REL, SIMDEF_NAME)
        self.link = os.path.join(local_appdata, REG_REL)

    def registered(self):
        """-> (windows path in the .simlink or None, local path or None)"""
        try:
            with open(self.link, encoding="utf-8-sig") as fh:
                w = fh.read().strip()
        except OSError:
            return None, None
        return (w or None), win_to_local(w, self.drive_c) if w else None


def state(p):
    """-> (DONE|TODO|BLOCKED, detail). Read-only."""
    ok, det = inspect(p.repo_simdef, p.scripts_dir)
    if not ok:
        return "BLOCKED", "repository copy %s: %s" % (REPO_REL, det)
    w, local = p.registered()
    if w:
        rok, rdet = inspect(local, p.scripts_dir)
        if rok:
            same = os.path.isfile(local) and open(local, "rb").read() == open(p.repo_simdef, "rb").read()
            return "DONE", "registered %s (%s)%s" % (w, rdet, "" if same else "; differs from the repository copy, kept")
        return "TODO", "registered %s is not usable (%s): install the repository copy and register it" % (w, rdet)
    if not os.path.isdir(p.simhub_dir):
        return "BLOCKED", "SimHub folder not found: %s" % p.simhub_dir
    return "TODO", "install %s (%s) into SimHub's ExternalSims folder and register it" % (REPO_REL, det)


def check_registered(p):
    """-> (ok, detail) for the registered definition only (no repository needed; the Windows
    verifier runs from the install folder). Read-only."""
    w, local = p.registered()
    if not w:
        return False, "not registered (no %s)" % p.link
    ok, det = inspect(local, p.scripts_dir)
    return ok, "registered %s (%s)" % (w, det)


def _set_aside(path, stamp):
    if os.path.lexists(path):
        bak = "%s.bak-%s" % (path, stamp)
        os.replace(path, bak)
        return bak
    return None


def apply(p):
    """Install + register. -> (ok, text). SimHub must be closed (the caller checks)."""
    st, det = state(p)
    if st == "DONE":
        return True, det
    if st == "BLOCKED":
        return False, det
    stamp = time.strftime("%Y%m%d-%H%M%S")
    notes = []
    os.makedirs(os.path.dirname(p.target), exist_ok=True)
    if os.path.isfile(p.target) and open(p.target, "rb").read() != open(p.repo_simdef, "rb").read():
        notes.append("old definition kept as " + os.path.basename(_set_aside(p.target, stamp)))
    shutil.copyfile(p.repo_simdef, p.target)
    os.makedirs(os.path.dirname(p.link), exist_ok=True)
    win = local_to_win(p.target, p.drive_c)
    if p.registered()[0] != win:
        if os.path.lexists(p.link):
            notes.append("old registration kept as " + os.path.basename(_set_aside(p.link, stamp)))
        with open(p.link, "w", encoding="utf-8", newline="") as fh:
            fh.write(win)
    st2, det2 = state(p)
    return st2 == "DONE", det2 + ("; " + "; ".join(notes) if notes else "")


def replace(p):
    """Install + register the repository copy even when a usable definition is registered.
    Used only by an update whose release changed config/simhub/Star Citizen.simdef (the
    launcher shows the "platform movement values have changed" warning before it). The file
    at SimHub's ExternalSims location and the registration are renamed to *.bak-<time> first
    when they differ; a definition registered elsewhere is left where it is. -> (ok, text).
    SimHub must be closed (the caller checks)."""
    ok, det = inspect(p.repo_simdef, p.scripts_dir)
    if not ok:
        return False, "repository copy %s: %s" % (REPO_REL, det)
    if not os.path.isdir(p.simhub_dir):
        return False, "SimHub folder not found: %s" % p.simhub_dir
    new = open(p.repo_simdef, "rb").read()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    notes = []
    w_old, local_old = p.registered()
    if local_old and os.path.abspath(local_old) != os.path.abspath(p.target) and os.path.isfile(local_old):
        notes.append("the definition registered before (%s) stays where it is, no longer registered" % w_old)
    os.makedirs(os.path.dirname(p.target), exist_ok=True)
    if os.path.isfile(p.target) and open(p.target, "rb").read() != new:
        notes.append("old definition kept as " + os.path.basename(_set_aside(p.target, stamp)))
    if not os.path.isfile(p.target) or open(p.target, "rb").read() != new:
        shutil.copyfile(p.repo_simdef, p.target)
    os.makedirs(os.path.dirname(p.link), exist_ok=True)
    win = local_to_win(p.target, p.drive_c)
    if w_old != win:
        if os.path.lexists(p.link):
            notes.append("old registration kept as " + os.path.basename(_set_aside(p.link, stamp)))
        with open(p.link, "w", encoding="utf-8", newline="") as fh:
            fh.write(win)
    w, local = p.registered()
    rok, rdet = inspect(local, p.scripts_dir)
    same = bool(local) and os.path.isfile(local) and open(local, "rb").read() == new
    return (rok and same), "game definition: registered %s (%s)%s" % (w, rdet, "; " + "; ".join(notes) if notes else "")


def active_game(simhub_dir):
    """The game SimHub's log last started a game manager for (None = unknown). Read-only."""
    log = os.path.join(simhub_dir, "Logs", "SimHub.txt")
    try:
        with open(log, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 512 * 1024))
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    import re
    hits = re.findall(r"\(Re\)Starting (\S+) Game Manager", text)
    return hits[-1] if hits else None


def active_game_text(simhub_dir):
    """-> (PASS|WARN|INFO, text) about SimHub's active game."""
    a = active_game(simhub_dir)
    if a is None:
        return "INFO", "unknown (no SimHub log yet); select Star Citizen in SimHub's game list"
    if SIMDEF_UUID in a:
        return "PASS", "Star Citizen"
    return "WARN", "%s - select Star Citizen in SimHub's game list, or SimHub waits for that game's data" % a
