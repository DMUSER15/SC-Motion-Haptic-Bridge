"""winlocate.py - find the RSI Launcher, Star Citizen LIVE and SimHub on Windows, and remember
which ones the installer assigned (%APPDATA%\\sc-motion\\config.json). Read-only except save().

Where it looks, for each:
  1. the environment variable (SC_RSI_LAUNCHER, SC_LIVE_DIR, SC_SIMHUB_DIR) - an explicit choice
  2. the choice saved by an earlier install (config.json)
  3. Windows' installed-programs list (registry Uninstall keys; RSI Launcher and SimHub)
  4. the usual folders on every fixed drive: \\Program Files\\..., \\Program Files (x86)\\...,
     \\Roberts Space Industries\\..., \\Games\\Roberts Space Industries\\..., \\SimHub
Used by sc_install_win.py (menus when several are found), sc_verify_win.py and sc_start_win.py.
Test override (stand-ins on Linux): SC_CERT_DRIVE_C (the C: root) and SCM_EXTRA_DRIVES
(more drive roots, separated by os.pathsep).
"""
import json, os

IS_WIN = os.name == "nt"
H = os.path.expanduser("~")
APPDATA = os.environ.get("APPDATA", os.path.join(H, "AppData", "Roaming"))
CONFIG = os.environ.get("SC_SCM_CONFIG", os.path.join(APPDATA, "sc-motion", "config.json"))
SYSDRIVE = os.environ.get("SC_CERT_DRIVE_C", os.environ.get("SystemDrive", "C:") + "\\")

KINDS = {
    "rsi_launcher": {"env": "SC_RSI_LAUNCHER", "title": "RSI Launcher", "reg": "rsi launcher",
                     "rel": [("Program Files", "Roberts Space Industries", "RSI Launcher", "RSI Launcher.exe"),
                             ("Roberts Space Industries", "RSI Launcher", "RSI Launcher.exe")],
                     "valid": lambda p: os.path.isfile(p), "regfile": "RSI Launcher.exe"},
    "live_dir": {"env": "SC_LIVE_DIR", "title": "Star Citizen LIVE",
                 "rel": [("Program Files", "Roberts Space Industries", "StarCitizen", "LIVE"),
                         ("Roberts Space Industries", "StarCitizen", "LIVE"),
                         ("Games", "Roberts Space Industries", "StarCitizen", "LIVE"),
                         ("StarCitizen", "LIVE")],
                 "valid": lambda p: os.path.isfile(os.path.join(p, "Bin64", "StarCitizen.exe"))},
    "simhub_dir": {"env": "SC_SIMHUB_DIR", "title": "SimHub", "reg": "simhub",
                   "rel": [("Program Files (x86)", "SimHub"), ("Program Files", "SimHub"), ("SimHub",)],
                   "valid": lambda p: os.path.isfile(os.path.join(p, "SimHubWPF.exe")), "regfile": None},
}


def load():
    try:
        with open(CONFIG, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(values):
    cfg = load()
    cfg.update(values)
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, "w") as f:
        json.dump(cfg, f, indent=1)
        f.write("\n")


def drives():
    """Fixed drive roots (C:\\, D:\\, ...)."""
    if not IS_WIN:
        extra = [d for d in os.environ.get("SCM_EXTRA_DRIVES", "").split(os.pathsep) if d]
        return [SYSDRIVE] + extra
    out = []
    try:
        import ctypes
        k = ctypes.windll.kernel32
        mask = k.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                root = "%s:\\" % chr(65 + i)
                if k.GetDriveTypeW(root) == 3:  # DRIVE_FIXED
                    out.append(root)
    except Exception:  # noqa: BLE001
        out = [SYSDRIVE]
    return out or [SYSDRIVE]


def registry_locations(name):
    """InstallLocation (or the folder of DisplayIcon) of installed programs whose DisplayName
    contains name (lower-case)."""
    if not IS_WIN or not name:
        return []
    import winreg
    out = []
    keys = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall")]
    for root, path in keys:
        try:
            k = winreg.OpenKey(root, path)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(k)[0]):
            try:
                sk = winreg.OpenKey(k, winreg.EnumKey(k, i))
                dn = str(winreg.QueryValueEx(sk, "DisplayName")[0])
            except OSError:
                continue
            if name not in dn.lower():
                continue
            loc = ""
            for v in ("InstallLocation", "DisplayIcon"):
                try:
                    loc = str(winreg.QueryValueEx(sk, v)[0]).strip().strip('"').split(",")[0]
                except OSError:
                    continue
                if loc:
                    break
            if loc:
                out.append(loc if os.path.isdir(loc) else os.path.dirname(loc))
    return out


def candidates(kind):
    """[{"path", "sources"}] of valid locations for kind, de-duplicated."""
    k = KINDS[kind]
    found = []

    def add(p, src):
        if not p:
            return
        p = os.path.normpath(p)
        if not k["valid"](p):
            return
        for x in found:
            if os.path.normcase(os.path.realpath(x["path"])) == os.path.normcase(os.path.realpath(p)):
                if src not in x["sources"]:
                    x["sources"].append(src)
                return
        found.append({"path": p, "sources": [src]})

    add(os.environ.get(k["env"]), "env " + k["env"])
    add(load().get(kind), "saved choice")
    for loc in registry_locations(k.get("reg")):
        add(os.path.join(loc, k["regfile"]) if k.get("regfile") else loc, "installed programs")
    for d in drives():
        for rel in k["rel"]:
            add(os.path.join(d, *rel), "found on %s" % d)
    return found


def choose(kind, option=None, menu=False, force_menu=False):
    """(path or None, how, candidates, problem). Explicit choice wins (option, env, saved);
    one candidate is used; several -> a menu when menu=True and a terminal is there, else a
    problem. force_menu shows the menu even when a choice exists."""
    k = KINDS[kind]
    cands = candidates(kind)
    explicit = [(option, "--" + kind.replace("_", "-")), (os.environ.get(k["env"]), "env " + k["env"]),
                (load().get(kind), "saved choice")]
    chosen = None
    for value, how in explicit:
        if value:
            if not k["valid"](value):
                return None, how, cands, "%s (%s) is not a valid %s location" % (value, how, k["title"])
            chosen = (os.path.normpath(value), how)
            break
    if chosen and not force_menu:
        return chosen[0], chosen[1], cands, None
    if not chosen and len(cands) == 1 and not force_menu:
        return cands[0]["path"], "only one found (%s)" % ", ".join(cands[0]["sources"]), cands, None
    if not cands:
        return None, "", cands, "%s not found" % k["title"]
    if menu:
        try:
            import scm_ui
        except ImportError:
            scm_ui = None
        if scm_ui and scm_ui.interactive():
            cur = [i for i, x in enumerate(cands) if chosen and os.path.normcase(x["path"]) == os.path.normcase(chosen[0])]
            i = scm_ui.choose_one("Which %s should be used?" % k["title"],
                                  ["%s   [%s]" % (x["path"], ", ".join(x["sources"])) for x in cands],
                                  cur[0] if cur else 0)
            if i is None:
                return None, "", cands, "no %s chosen" % k["title"]
            return cands[i]["path"], "chosen in the menu", cands, None
    if chosen:
        return chosen[0], chosen[1], cands, None
    return None, "", cands, "%d %s locations found; apply shows a menu (or use --%s)" % (
        len(cands), k["title"], kind.replace("_", "-"))
