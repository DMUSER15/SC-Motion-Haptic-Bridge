#!/usr/bin/env python3
"""scm_addons.py - optional add-ons for the SC Motion Haptic Bridge (add-on contract v1, section 4).

An add-on is a separate program in its own repository that uses the bridge's telemetry. The
bridge never needs one; nothing here blocks the bridge
installer, and no add-on file is ever edited.

  python3 setup/common/scm_addons.py list                  add-ons known (catalog) and installed
  python3 setup/common/scm_addons.py install SOURCE        SOURCE: a .tar.gz / .zip of a release, an
                                                           unpacked folder, or a name from the catalog
                                                           (downloads its newest GitHub release)
  python3 setup/common/scm_addons.py update NAME [SOURCE]  the same, for an add-on that is installed
  python3 setup/common/scm_addons.py plan NAME             the add-on's own read-only check
  python3 setup/common/scm_addons.py rollback NAME         back to the version installed before
  python3 setup/common/scm_addons.py remove NAME           run the add-on's remove step, delete its files
  (the installers accept the same after the word "addon": sc_motion_install.py addon list)
  --yes   do not ask before running the add-on's installer

What install does
  1. Unpacks into a staging folder. Archives are read member by member: only plain files and
     folders, no links, nothing outside the folder; size and count limits.
  2. Checks the package: addon.json (name, version, contract range includes this bridge's
     contract, this platform, commands that start with {python} and name a script inside the
     package, tap ports inside the add-on ranges and not taken) and release.json (every file
     listed with its sha256, every sha256 correct, no file that is not listed).
  3. Shows what it found and asks. An add-on's installer is a program from that package and runs
     as you: install only add-ons you trust.
  4. Moves it to <add-ons folder>/<name>/<version>/ and runs the add-on's "install" command
     there (the older version's "remove" command first). If that fails, the older version is
     put back. The version before stays on disk for "rollback"; older ones are deleted.

Add-ons folder: ~/.local/share/sc-motion/addons (Windows %LOCALAPPDATA%\\sc-motion\\addons); env
SC_ADDONS_DIR. Catalog: addons/catalog.json in the bridge repository (optional).
Exit codes: 0 done, 1 usage / not confirmed / nothing to do, 2 refused (package or source not
usable), 3 the add-on's own command failed.
"""
import datetime
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)
import scm_ports as P  # noqa: E402
import scm_update as U  # noqa: E402

VERSION = "1.0"
CONTRACT = 1                       # the add-on contract this bridge implements
REPO = os.path.dirname(os.path.dirname(HERE))
CATALOG = os.path.join(REPO, "addons", "catalog.json")
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
MAX_FILES = 5000
MAX_BYTES = 300 * 1024 * 1024
MAX_ONE = 64 * 1024 * 1024
SKIP_DIRS = (".git", "__pycache__")
HOOK_TIMEOUT = 900
PLATFORM = "windows" if os.name == "nt" else "linux"


class Refused(Exception):
    """The package or source cannot be used; nothing was installed."""


def addons_dir():
    env = os.environ.get("SC_ADDONS_DIR")
    if env:
        return env
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "sc-motion", "addons")


def _stamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def _read_json(path, limit=4 << 20):
    if os.path.getsize(path) > limit:
        raise ValueError("file too large")
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


# ---------------------------------------------------------------- catalog and state
def catalog():
    """[{name, title, repo, summary}] from addons/catalog.json; [] when there is none."""
    try:
        data = _read_json(CATALOG)
    except (OSError, ValueError):
        return []
    out = []
    for e in data.get("addons", []) if isinstance(data, dict) else []:
        if isinstance(e, dict) and NAME_RE.match(str(e.get("name", ""))) and SLUG_RE.match(str(e.get("repo", ""))):
            out.append({"name": e["name"], "title": str(e.get("title") or e["name"])[:80], "repo": e["repo"],
                        "summary": str(e.get("summary") or "")[:200]})
    return out


def state_path(name):
    return os.path.join(addons_dir(), name, "state.json")


def read_state(name):
    try:
        s = _read_json(state_path(name))
    except (OSError, ValueError):
        return None
    if not isinstance(s, dict) or s.get("name") != name or U.parse_version(s.get("current")) is None:
        return None
    if s.get("previous") is not None and U.parse_version(s.get("previous")) is None:
        s["previous"] = None
    if not isinstance(s.get("history"), list):
        s["history"] = []
    return s


def write_state(name, s):
    path = state_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def installed():
    """{name: state} of every installed add-on."""
    out = {}
    try:
        names = os.listdir(addons_dir())
    except OSError:
        return out
    for n in sorted(names):
        if NAME_RE.match(n):
            s = read_state(n)
            if s and os.path.isdir(version_dir(n, s["current"])):
                out[n] = s
    return out


def version_dir(name, version):
    return os.path.join(addons_dir(), name, version)


# ---------------------------------------------------------------- unpacking (untrusted input)
def _member_ok(name):
    name = name.replace("\\", "/")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return None
    parts = [x for x in name.split("/") if x not in ("", ".")]
    if not parts or ".." in parts:
        return None
    return parts


def _budget():
    return {"files": 0, "bytes": 0}


def _count(b, size):
    b["files"] += 1
    b["bytes"] += size
    if size > MAX_ONE:
        raise Refused("a file in the package is larger than %d MB" % (MAX_ONE >> 20))
    if b["files"] > MAX_FILES or b["bytes"] > MAX_BYTES:
        raise Refused("the package is too large (more than %d files or %d MB)" % (MAX_FILES, MAX_BYTES >> 20))


def _copy_stream(src, dst_path, size):
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    left = size
    with open(dst_path, "wb") as out:
        while left > 0:
            chunk = src.read(min(1 << 20, left))
            if not chunk:
                raise Refused("the archive is cut off")
            out.write(chunk)
            left -= len(chunk)


def unpack_tar(path, dest):
    b = _budget()
    try:
        with tarfile.open(path, "r:*") as tf:
            for m in tf:
                if m.isdir() or m.type in (tarfile.XGLTYPE, tarfile.XHDTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK):
                    continue
                if not m.isreg():
                    raise Refused("the archive holds something that is not a plain file (%s): links and devices are not accepted" % m.name)
                parts = _member_ok(m.name)
                if parts is None:
                    raise Refused("the archive holds a path outside its folder: %r" % m.name)
                _count(b, m.size)
                src = tf.extractfile(m)
                _copy_stream(src, os.path.join(dest, *parts), m.size)
    except (tarfile.TarError, EOFError, OSError) as e:
        raise Refused("cannot read the archive: %s" % e)


def unpack_zip(path, dest):
    b = _budget()
    try:
        with zipfile.ZipFile(path) as zf:
            for zi in zf.infolist():
                if zi.is_dir():
                    continue
                if stat.S_ISLNK(zi.external_attr >> 16):
                    raise Refused("the archive holds a link (%s): links are not accepted" % zi.filename)
                parts = _member_ok(zi.filename)
                if parts is None:
                    raise Refused("the archive holds a path outside its folder: %r" % zi.filename)
                _count(b, zi.file_size)
                with zf.open(zi) as src:
                    _copy_stream(src, os.path.join(dest, *parts), zi.file_size)
    except (zipfile.BadZipFile, OSError, RuntimeError) as e:
        raise Refused("cannot read the archive: %s" % e)


def copy_folder(src, dest):
    b = _budget()
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not os.path.islink(os.path.join(root, d))]
        for n in files:
            sp = os.path.join(root, n)
            if os.path.islink(sp) or not os.path.isfile(sp):
                raise Refused("the folder holds something that is not a plain file: %s" % sp)
            if n.endswith((".pyc", ".pyo")):
                continue
            _count(b, os.path.getsize(sp))
            dp = os.path.join(dest, os.path.relpath(sp, src))
            os.makedirs(os.path.dirname(dp), exist_ok=True)
            shutil.copyfile(sp, dp)


def package_root(stage):
    """The folder that holds addon.json: the staging folder itself, or its only sub-folder."""
    if os.path.isfile(os.path.join(stage, "addon.json")):
        return stage
    subs = [n for n in os.listdir(stage)]
    if len(subs) == 1 and os.path.isfile(os.path.join(stage, subs[0], "addon.json")):
        return os.path.join(stage, subs[0])
    raise Refused("no addon.json at the top of the package: this is not an add-on")


# ---------------------------------------------------------------- download (catalog name)
def download(entry, dest, say):
    """Newest GitHub release of a catalog add-on into dest. Every file is checked against the
    release's release.json before it is kept."""
    slug = entry["repo"]
    try:
        data = json.loads(U._get("%s/repos/%s/releases?per_page=30" % (U.API, slug), limit=4 << 20))
    except Exception as e:  # noqa: BLE001
        code = getattr(e, "code", None)
        if code == 404:
            raise Refused("no public releases found for %s (a private repository cannot be downloaded: "
                          "install from an archive or folder instead)" % slug)
        raise Refused("GitHub not reachable for %s (%s)" % (slug, getattr(e, "reason", e)))
    best = None
    for r in data if isinstance(data, list) else []:
        if isinstance(r, dict) and not r.get("draft"):
            v = U.parse_version(r.get("tag_name"))
            if v is not None and (best is None or v > best[0]):
                best = (v, r["tag_name"])
    if best is None:
        raise Refused("%s has no release yet" % slug)
    tag = best[1]

    def raw(p):
        import urllib.parse
        return "%s/%s/%s/%s" % (U.RAW, slug, urllib.parse.quote(tag, safe=""), urllib.parse.quote(p))
    try:
        man = json.loads(U._get(raw(U.MANIFEST), limit=4 << 20))
    except Exception as e:  # noqa: BLE001
        raise Refused("could not read release.json of %s %s (%s)" % (slug, tag, e))
    problem = U.check_manifest(man)
    if problem:
        raise Refused("release.json of %s %s is not usable (%s)" % (slug, tag, problem))
    if len(man["files"]) > MAX_FILES:
        raise Refused("the release lists too many files")
    say("downloading %s %s (%d files) ..." % (slug, tag, len(man["files"])))
    import hashlib
    b = _budget()
    for p, meta in sorted(man["files"].items()):
        try:
            blob = U._get(raw(p), timeout=max(U.TIMEOUT, 30), limit=MAX_ONE)
        except Exception as e:  # noqa: BLE001
            raise Refused("%s: download failed (%s)" % (p, e))
        if hashlib.sha256(blob).hexdigest() != meta["sha256"]:
            raise Refused("%s: sha256 does not match the release" % p)
        _count(b, len(blob))
        dp = U.local_path(dest, p)
        os.makedirs(os.path.dirname(dp), exist_ok=True)
        with open(dp, "wb") as f:
            f.write(blob)
    with open(os.path.join(dest, U.MANIFEST), "w", encoding="utf-8") as f:
        json.dump(man, f, indent=1, sort_keys=True)
        f.write("\n")
    return "%s release %s" % (slug, tag)


# ---------------------------------------------------------------- checking a package
def _command(meta, key, root):
    cmd = meta.get(key)
    if not (isinstance(cmd, list) and len(cmd) >= 2 and all(isinstance(x, str) and x and "\x00" not in x for x in cmd)):
        raise Refused("addon.json: '%s' must be a command list" % key)
    if cmd[0] != "{python}":
        raise Refused("addon.json: '%s' must start with {python} (only Python scripts of the package are run)" % key)
    if not U.safe_path(cmd[1]) or not cmd[1].endswith(".py") or not os.path.isfile(U.local_path(root, cmd[1])):
        raise Refused("addon.json: '%s' must name a .py script inside the package (%r)" % (key, cmd[1]))
    if len(cmd) > 12 or any(len(x) > 200 for x in cmd):
        raise Refused("addon.json: '%s' is too long" % key)
    return cmd


def check_package(root):
    """-> (meta, problems are raised as Refused). meta = the checked addon.json."""
    try:
        meta = _read_json(os.path.join(root, "addon.json"), limit=1 << 20)
    except (OSError, ValueError) as e:
        raise Refused("addon.json cannot be read (%s)" % e)
    if not isinstance(meta, dict) or meta.get("schema") != "sc-motion-addon":
        raise Refused("addon.json: schema must be sc-motion-addon")
    name = meta.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise Refused("addon.json: 'name' must be 2-32 lower-case letters, digits, - or _")
    if not isinstance(meta.get("version"), str) or U.parse_version(meta["version"]) is None \
            or not re.match(r"^[0-9A-Za-z.+-]{1,40}$", meta["version"]):
        raise Refused("addon.json: no valid 'version'")
    c = meta.get("contract")
    if not (isinstance(c, dict) and all(isinstance(c.get(k), int) and not isinstance(c.get(k), bool) for k in ("min", "max"))):
        raise Refused("addon.json: 'contract' must be {min, max}")
    if not c["min"] <= CONTRACT <= c["max"]:
        raise Refused("this add-on is made for bridge contract %d-%d; this bridge implements contract %d. "
                      "Update the bridge or the add-on." % (c["min"], c["max"], CONTRACT))
    plats = meta.get("platforms")
    if isinstance(plats, list) and PLATFORM not in plats:
        raise Refused("this add-on does not support %s (it lists: %s)" % (PLATFORM, ", ".join(map(str, plats))))
    for key in ("install", "remove"):
        _command(meta, key, root)
    if "plan" in meta:
        _command(meta, "plan", root)
    taps = meta.get("taps", [])
    if not isinstance(taps, list):
        raise Refused("addon.json: 'taps' must be a list")
    others = {}
    for n, s in installed().items():
        if n != name:
            for t in s.get("taps", []):
                others[t] = n
    seen = []
    for t in taps:
        port = t.get("port") if isinstance(t, dict) else None
        why = P.tap_port_problem(port, taken=seen)
        if why:
            raise Refused("addon.json: tap %r: %s" % (t.get("name") if isinstance(t, dict) else t, why))
        if port in others:
            raise Refused("tap port %d is already used by the installed add-on '%s'" % (port, others[port]))
        seen.append(port)
    if len(seen) > P.MAX_TAPS:
        raise Refused("addon.json: more than %d taps" % P.MAX_TAPS)
    # release.json: every file listed, every sha256 right, nothing extra
    man = U.read_manifest(os.path.join(root, U.MANIFEST))
    if man is None:
        raise Refused("release.json is missing or not usable: only released packages can be installed "
                      "(it lists every file with its sha256)")
    if man.get("name") not in (None, name) or U.parse_version(man["version"]) != U.parse_version(meta["version"]):
        raise Refused("release.json (%s %s) does not belong to addon.json (%s %s)" % (
            man.get("name"), man.get("version"), name, meta["version"]))
    listed = set(man["files"])
    if "addon.json" not in listed:
        raise Refused("release.json does not list addon.json")
    present = set()
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for n in files:
            present.add(os.path.relpath(os.path.join(d, n), root).replace(os.sep, "/"))
    present.discard(U.MANIFEST)
    extra = sorted(present - listed)
    if extra:
        raise Refused("the package holds files its release.json does not list: %s" % ", ".join(extra[:5]))
    for p in sorted(listed):
        lp = U.local_path(root, p)
        if not os.path.isfile(lp):
            raise Refused("release.json lists %s but the package does not have it" % p)
        if U.sha256_file(lp) != man["files"][p]["sha256"]:
            raise Refused("%s does not match the sha256 in release.json (damaged or changed package)" % p)
    meta["_taps"] = seen
    meta["_files"] = len(listed)
    return meta


# ---------------------------------------------------------------- running an add-on's command
def run_hook(meta, key, folder, say):
    """-> (exit code, last RESULT line or '')."""
    cmd = [sys.executable if x == "{python}" else x for x in meta[key]]
    env = dict(os.environ, SC_BRIDGE_DIR=REPO, PYTHONDONTWRITEBYTECODE="1")
    say("  running the add-on's %s step: %s" % (key, " ".join(meta[key])))
    try:
        r = subprocess.run(cmd, cwd=folder, env=env, capture_output=True, text=True, errors="replace",
                           timeout=HOOK_TIMEOUT, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        say("  the %s step did not finish within %d s" % (key, HOOK_TIMEOUT))
        return 124, ""
    except OSError as e:
        say("  the %s step could not be started (%s)" % (key, e))
        return 126, ""
    lines = (r.stdout + r.stderr).splitlines()
    for ln in lines[-60:]:
        print("      " + ln[:300])
    result = next((ln.strip() for ln in reversed(lines) if ln.strip().startswith("RESULT ")), "")
    return r.returncode, result


def load_meta(name, version):
    folder = version_dir(name, version)
    meta = _read_json(os.path.join(folder, "addon.json"), limit=1 << 20)
    for key in ("install", "remove"):
        _command(meta, key, folder)
    if "plan" in meta:
        _command(meta, "plan", folder)
    return meta, folder


def _rmtree(path):
    base = os.path.realpath(addons_dir())
    if os.path.realpath(path).startswith(base + os.sep):
        shutil.rmtree(path, ignore_errors=True)


def _confirm(args, question):
    if "--yes" in args:
        return True
    if not sys.stdin.isatty():
        print("  no terminal to ask: nothing was changed (add --yes to run without the question)")
        return False
    try:
        return input(question).strip().lower() in ("y", "yes")
    except EOFError:
        return False


# ---------------------------------------------------------------- commands
def cmd_list(say=print):
    inst = installed()
    cat = catalog()
    say("Add-ons (folder: %s)" % addons_dir())
    names = sorted(set(inst) | {c["name"] for c in cat})
    if not names:
        say("  none installed, no catalog. Install one: scm_addons.py install ARCHIVE-OR-FOLDER")
    for n in names:
        c = next((x for x in cat if x["name"] == n), None)
        s = inst.get(n)
        if s:
            say("  %-10s installed %s%s%s" % (n, s["current"], ("  (before: %s; rollback possible)" % s["previous"])
                                              if s.get("previous") and os.path.isdir(version_dir(n, s["previous"])) else "",
                                              ("  taps: %s" % ", ".join(map(str, s.get("taps", [])))) if s.get("taps") else ""))
        else:
            say("  %-10s not installed" % n)
        if c:
            say("  %-10s   %s  [%s]%s" % ("", c["title"], c["repo"], (": " + c["summary"]) if c["summary"] else ""))
    return 0


def summary():
    """One line for the verifiers: installed add-ons and their versions ('' when none)."""
    return ", ".join("%s %s" % (n, s["current"]) for n, s in installed().items())


def _stage_source(source, stage, say):
    """Fill stage from source. -> text describing where it came from."""
    if os.path.isdir(source):
        copy_folder(source, stage)
        return "folder"
    if os.path.isfile(source):
        low = source.lower()
        if low.endswith((".tar.gz", ".tgz", ".tar")):
            unpack_tar(source, stage)
        elif low.endswith(".zip"):
            unpack_zip(source, stage)
        else:
            raise Refused("%s is not a .tar.gz, .tgz, .tar or .zip archive" % source)
        return "archive %s (sha256 %s)" % (os.path.basename(source), U.sha256_file(source))
    entry = next((c for c in catalog() if c["name"] == source), None)
    if entry is None:
        raise Refused("%r is not a file, a folder or a name in the catalog" % source)
    return download(entry, stage, say)


def cmd_install(source, args, say=print, want_name=None):
    base = addons_dir()
    os.makedirs(base, exist_ok=True)
    stage = os.path.join(base, ".stage-%d-%s" % (os.getpid(), _stamp()))
    os.makedirs(stage)
    try:
        origin = _stage_source(source, stage, say)
        root = package_root(stage)
        meta = check_package(root)
        name, version = meta["name"], meta["version"]
        if want_name and name != want_name:
            raise Refused("that package is the add-on '%s', not '%s'" % (name, want_name))
        state = read_state(name)
        old = state["current"] if state and os.path.isdir(version_dir(name, state["current"])) else None
        say("Add-on: %s (%s) version %s, %d files, from %s" % (meta.get("title") or name, name, version, meta["_files"], origin))
        say("  package check: every file matches release.json; contract %d ok" % CONTRACT)
        if meta["_taps"]:
            say("  telemetry tap port(s): %s" % ", ".join(map(str, meta["_taps"])))
        mods = meta.get("python_modules") if isinstance(meta.get("python_modules"), dict) else {}
        if mods.get("required") or mods.get("optional"):
            say("  Python modules it uses: required %s; optional %s (not installed by this tool)" % (
                ", ".join(map(str, mods.get("required") or [])) or "none", ", ".join(map(str, mods.get("optional") or [])) or "none"))
        if old:
            say("  installed now: %s -> this replaces it (kept for 'rollback')" % old if old != version else
                "  installed now: the same version %s -> it is installed again" % old)
        say("  It will run the add-on's own installer: %s" % " ".join(meta["install"]))
        say("  That is a program from the package and runs as you. Install only add-ons you trust.")
        if not _confirm(args, "  Install %s %s? [y]es / [n]o: " % (name, version)):
            say("not installed")
            return 1
        final = version_dir(name, version)
        aside = None
        if os.path.exists(final):
            aside = final + ".replaced-" + _stamp()
            os.rename(final, aside)
        os.makedirs(os.path.dirname(final), exist_ok=True)
        os.rename(root, final)
        old_meta = None
        if old and old != version:
            try:
                old_meta, old_folder = load_meta(name, old)
                run_hook(old_meta, "remove", old_folder, say)
            except (OSError, ValueError, Refused) as e:
                say("  (the old version's remove step was skipped: %s)" % e)
        elif aside:
            try:
                am = _read_json(os.path.join(aside, "addon.json"), limit=1 << 20)
                _command(am, "remove", aside)
                run_hook(am, "remove", aside, say)
            except (OSError, ValueError, Refused):
                pass
        rc, result = run_hook(meta, "install", final, say)
        if rc != 0:
            say("The add-on's installer failed (exit code %d). Putting things back." % rc)
            try:
                run_hook(meta, "remove", final, say)
            except OSError:
                pass
            _rmtree(final)
            if aside:
                os.rename(aside, final)
                try:
                    am, af = load_meta(name, version)
                    run_hook(am, "install", af, say)
                except (OSError, ValueError, Refused):
                    pass
            elif old_meta is not None:
                run_hook(old_meta, "install", version_dir(name, old), say)
            say("RESULT addon install: FAILED, %s" % ("%s %s is active again" % (name, old) if old else "nothing is installed"))
            return 3
        if aside:
            _rmtree(aside)
        new_state = {"name": name, "current": version, "previous": old if old and old != version else (state or {}).get("previous"),
                     "title": str(meta.get("title") or name)[:80], "taps": meta["_taps"],
                     "history": ((state or {}).get("history", []) + [{"time": _stamp(), "action": "install", "version": version,
                                                                    "from": old, "source": origin[:200]}])[-30:]}
        write_state(name, new_state)
        keep = {version, new_state["previous"]}
        for n in os.listdir(os.path.join(base, name)):
            p = os.path.join(base, name, n)
            if os.path.isdir(p) and n not in keep:
                _rmtree(p)
        say("RESULT addon install: %s %s installed in %s%s" % (name, version, final, ("; " + result) if result else ""))
        return 0
    except Refused as e:
        say("REFUSED: %s" % e)
        say("RESULT addon install: nothing was installed")
        return 2
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def cmd_plan(name, say=print):
    s = installed().get(name)
    if not s:
        say("add-on '%s' is not installed" % name)
        return 1
    try:
        meta, folder = load_meta(name, s["current"])
    except (OSError, ValueError, Refused) as e:
        say("REFUSED: the installed add-on cannot be read (%s)" % e)
        return 2
    if "plan" not in meta:
        say("add-on '%s' has no plan step" % name)
        return 1
    rc, _ = run_hook(meta, "plan", folder, say)
    return 0 if rc == 0 else 3


def cmd_rollback(name, args, say=print):
    s = installed().get(name)
    if not s:
        say("add-on '%s' is not installed" % name)
        return 1
    prev = s.get("previous")
    if not prev or not os.path.isdir(version_dir(name, prev)):
        say("add-on '%s': no earlier version is kept (only %s); nothing to go back to" % (name, s["current"]))
        return 1
    try:
        cur_meta, cur_folder = load_meta(name, s["current"])
        prev_meta, prev_folder = load_meta(name, prev)
    except (OSError, ValueError, Refused) as e:
        say("REFUSED: a kept version cannot be read (%s)" % e)
        return 2
    say("Add-on %s: %s -> %s (the version installed before)" % (name, s["current"], prev))
    if not _confirm(args, "  Go back to %s? [y]es / [n]o: " % prev):
        say("not changed")
        return 1
    run_hook(cur_meta, "remove", cur_folder, say)
    rc, result = run_hook(prev_meta, "install", prev_folder, say)
    if rc != 0:
        say("The older version's installer failed (exit code %d); putting %s back." % (rc, s["current"]))
        run_hook(cur_meta, "install", cur_folder, say)
        say("RESULT addon rollback: FAILED, %s %s is active again" % (name, s["current"]))
        return 3
    s["history"] = (s["history"] + [{"time": _stamp(), "action": "rollback", "version": prev, "from": s["current"]}])[-30:]
    s["current"], s["previous"] = prev, s["current"]
    write_state(name, s)
    say("RESULT addon rollback: %s is back at %s ('rollback' again returns to %s)" % (name, prev, s["previous"]))
    return 0


def cmd_remove(name, args, say=print):
    if not NAME_RE.match(name) or not os.path.isdir(os.path.join(addons_dir(), name)):
        say("add-on '%s' is not installed" % name)
        return 1
    s = read_state(name)
    say("Add-on %s%s: its remove step runs, then its program files are deleted. Your settings for it are kept." % (
        name, (" " + s["current"]) if s else ""))
    if not _confirm(args, "  Remove %s? [y]es / [n]o: " % name):
        say("not removed")
        return 1
    rc = 0
    if s and os.path.isdir(version_dir(name, s["current"])):
        try:
            meta, folder = load_meta(name, s["current"])
            rc, _ = run_hook(meta, "remove", folder, say)
        except (OSError, ValueError, Refused) as e:
            say("  (its remove step was skipped: %s)" % e)
    _rmtree(os.path.join(addons_dir(), name))
    say("RESULT addon remove: %s removed%s" % (name, "" if rc == 0 else " (its own remove step reported exit code %d)" % rc))
    return 0


def main(argv):
    args = [a for a in argv[1:] if a != "addon"]
    flags = [a for a in args if a.startswith("--")]
    pos = [a for a in args if not a.startswith("--")]
    if any(f != "--yes" for f in flags) or not pos:
        print(__doc__)
        return 1
    cmd, rest = pos[0], pos[1:]
    if cmd == "list" and not rest:
        return cmd_list()
    if cmd == "install" and len(rest) == 1:
        return cmd_install(rest[0], flags)
    if cmd == "update" and len(rest) in (1, 2) and NAME_RE.match(rest[0]):
        if rest[0] not in installed():
            print("add-on '%s' is not installed (use install)" % rest[0])
            return 1
        return cmd_install(rest[1] if len(rest) == 2 else rest[0], flags, want_name=rest[0])
    if cmd == "plan" and len(rest) == 1:
        return cmd_plan(rest[0])
    if cmd == "rollback" and len(rest) == 1:
        return cmd_rollback(rest[0], flags)
    if cmd == "remove" and len(rest) == 1:
        return cmd_remove(rest[0], flags)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
