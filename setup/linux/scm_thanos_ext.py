"""scm_thanos_ext.py - the THANOS example option, checked with the separate simhub-thanos-linux tool.

Since kit 1.3 this repository has no THANOS tool of its own. The THANOS AMC (or any USB serial
controller) is set up in SimHub's Wine prefix with simhub-thanos-linux:
  git clone https://github.com/DMUSER15/simhub-thanos-linux ~/simhub-thanos-linux
  python3 ~/simhub-thanos-linux/simhub_thanos.py plan      (read-only)
  python3 ~/simhub-thanos-linux/simhub_thanos.py apply     (SimHub closed, cabinet OFF)
The installer (step 10) and the verifier only run its read-only `plan`; they never change ports.

Where the tool is looked for: env SC_THANOS_TOOL (path to simhub_thanos.py), else
~/simhub-thanos-linux/simhub_thanos.py. The stand-in sysfs override SCM_SYS_ROOT is passed on as
SIMHUB_THANOS_SYS_ROOT (tests only).
"""
import os, re, subprocess, sys

REPO_URL = "https://github.com/DMUSER15/simhub-thanos-linux"
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "simhub-thanos-linux")
INSTALL = "git clone %s %s" % (REPO_URL, DEFAULT_DIR)
FIX = "python3 %s apply (SimHub closed, cabinet OFF)" % os.path.join(DEFAULT_DIR, "simhub_thanos.py")


def tool_path():
    return os.environ.get("SC_THANOS_TOOL") or os.path.join(DEFAULT_DIR, "simhub_thanos.py")


def _env(env):
    e = dict(env if env is not None else os.environ)
    if e.get("SCM_SYS_ROOT") and not e.get("SIMHUB_THANOS_SYS_ROOT"):
        e["SIMHUB_THANOS_SYS_ROOT"] = e["SCM_SYS_ROOT"]
    return e


def _last(out):
    lines = [x.strip() for x in (out or "").splitlines() if x.strip()]
    return lines[-1] if lines else ""


def check(env=None, timeout=120):
    """Read-only. Returns {"state": DONE|TODO|MISSING|ERROR, "detail", "coms", "auto", "warnings"}.
    DONE = simhub-thanos-linux `plan` exit 0 (every chosen device READY)."""
    p = tool_path()
    res = {"state": "MISSING", "detail": "", "coms": [], "auto": [], "warnings": 0, "tool": p}
    if not os.path.isfile(p):
        res["detail"] = "simhub-thanos-linux not installed (%s missing). Install: %s" % (p, INSTALL)
        return res
    try:
        r = subprocess.run([sys.executable, p, "plan"], capture_output=True, text=True, env=_env(env),
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        res.update(state="ERROR", detail="simhub-thanos-linux plan failed to run: %s" % e)
        return res
    out = (r.stdout + r.stderr).strip()
    res["coms"] = re.findall(r"^\S.*\s->\s+(COM\d+)\s+\(", out, re.M)
    res["auto"] = re.findall(r"^\s+NOTE\s+(COM\d+) is Wine's automatic link", out, re.M)
    res["warnings"] = len(re.findall(r"^\s+WARNING\s", out, re.M))
    ready = re.findall(r"^\s+READY\s+(COM\d+) reaches (\S+)", out, re.M)
    if r.returncode == 0 and ready:
        note = ""
        if res["auto"]:
            note += "; Wine's automatic %s also reaches it: keep %s disabled in SimHub" % (
                "/".join(res["auto"]), "/".join(res["auto"]))
        if res["warnings"]:
            note += "; %d WARNING(s): python3 %s plan" % (res["warnings"], p)
        res.update(state="DONE", detail="THANOS on %s ready in SimHub's prefix (simhub-thanos-linux)%s"
                   % (", ".join(c for c, _ in ready), note))
        return res
    if r.returncode == 4:
        todo = re.findall(r"^\s+[1-3] (access|registry|link)\s+(TODO|CONFLICT)", out, re.M)
        res.update(state="TODO", detail="THANOS %s: %s not done; %s" % (
            ", ".join(res["coms"]) or "?", ", ".join(w for w, _ in todo) or "?", FIX))
    elif r.returncode in (3, 5):
        why = [x.strip() for x in out.splitlines() if x.startswith(("CHOICE NEEDED", "BLOCKED", "no "))]
        res.update(state="TODO", detail="THANOS: %s; %s" % (why[0] if why else _last(out), FIX))
    else:
        res.update(state="ERROR", detail="simhub-thanos-linux plan exit %d: %s" % (r.returncode, _last(out)))
    return res
