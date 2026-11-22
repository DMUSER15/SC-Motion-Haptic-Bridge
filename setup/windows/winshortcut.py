"""winshortcut.py - the Start menu entry of the control window on Windows.

  ensure_control_entry(install_dir, repo, python_exe)   -> (changed, text)

The entry "Star Citizen Motion - Control window" starts pythonw.exe (no console window) with
<repository folder>\\gui\\sc_control.py. The window runs from the repository folder the installer
saved, because that is the folder the updates keep current; the runtime files it drives are the
copies in %USERPROFILE%\\sc-motion (installer step 6).

Used by the installer (sc_install_win.py step 13) and by the terminal launcher at every start
(sc_start_win.py), so an install updated from 0.1.1 (which brings gui\\ but never ran step 13)
gets the entry too. A record next to the launcher (control_window.json) remembers what the entry
points at; the entry is written again only when that changes (moved repository folder, other
Python) or the .lnk file is gone. Nothing else is changed.
Test override: SC_SCM_LNK_STANDIN=1 (not on Windows) writes the entry's settings as JSON in place
of a .lnk, so the launcher and installer paths can be exercised on Linux.
"""
import json
import os
import subprocess

IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0
TITLE = "Star Citizen Motion - Control window"
RECORD = "control_window.json"


def start_menu():
    return os.path.join(os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming"),
                        "Microsoft", "Windows", "Start Menu", "Programs")


def lnk_path():
    return os.path.join(start_menu(), TITLE + ".lnk")


def gui_script(repo):
    return os.path.join(repo, "gui", "sc_control.py") if repo else None


def windowed_python(python_exe):
    """pythonw.exe next to python.exe (no console window), else python.exe itself."""
    d = os.path.dirname(python_exe or "")
    for name in ("pythonw.exe", "pythonw"):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return python_exe


def icon_for(repo):
    for rel in ("config/icons/sc-motion.ico", "docs/logo.ico"):
        p = os.path.join(repo, *rel.split("/"))
        if os.path.isfile(p):
            return p
    return None


def wanted(repo, python_exe):
    script = gui_script(repo)
    return {"target": windowed_python(python_exe), "args": '"%s"' % script, "workdir": repo,
            "icon": icon_for(repo), "lnk": lnk_path()}


def _ps_quote(s):
    return "'" + (s or "").replace("'", "''") + "'"


def make_lnk(spec):
    """Write the .lnk with WScript.Shell (PowerShell). -> (ok, text)."""
    os.makedirs(os.path.dirname(spec["lnk"]), exist_ok=True)
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut(%s); $s.TargetPath=%s; $s.Arguments=%s; "
          "$s.WorkingDirectory=%s; $s.Description=%s; %s$s.Save()" % (
              _ps_quote(spec["lnk"]), _ps_quote(spec["target"]), _ps_quote(spec["args"]), _ps_quote(spec["workdir"]),
              _ps_quote("Star Citizen motion control window: bridge, SimHub and the RSI Launcher"),
              ("$s.IconLocation=%s; " % _ps_quote(spec["icon"])) if spec.get("icon") else ""))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                           text=True, timeout=60, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return os.path.isfile(spec["lnk"]), (r.stdout + r.stderr).strip() or "created"


def _standin(spec):
    os.makedirs(os.path.dirname(spec["lnk"]), exist_ok=True)
    with open(spec["lnk"], "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=1)
    return True, "stand-in written"


def state(install_dir, repo, python_exe):
    """-> ("DONE" | "TODO" | "BLOCKED", text) without changing anything."""
    script = gui_script(repo)
    if not script or not os.path.isfile(script):
        return "BLOCKED", "no gui\\sc_control.py in the repository folder (%s)" % (repo or "not saved")
    spec = wanted(repo, python_exe)
    try:
        with open(os.path.join(install_dir, RECORD), encoding="utf-8") as f:
            have = json.load(f)
    except (OSError, ValueError):
        have = None
    if have == spec and os.path.isfile(spec["lnk"]):
        return "DONE", spec["lnk"]
    return "TODO", "create %s" % spec["lnk"]


def ensure_control_entry(install_dir, repo, python_exe, make=None):
    """Create or correct the Start menu entry. -> (changed, text). make: test stand-in for make_lnk."""
    st, text = state(install_dir, repo, python_exe)
    if st != "TODO":
        return False, text
    if make is None and not IS_WIN:
        if os.environ.get("SC_SCM_LNK_STANDIN") != "1":
            return False, "Windows only"
        make = _standin

    spec = wanted(repo, python_exe)
    ok, out = (make or make_lnk)(spec)
    if not ok:
        return False, "control window entry not created: %s" % out
    os.makedirs(install_dir, exist_ok=True)
    with open(os.path.join(install_dir, RECORD), "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=1)
    return True, "Start menu: \"%s\" (opens the control window)" % TITLE
