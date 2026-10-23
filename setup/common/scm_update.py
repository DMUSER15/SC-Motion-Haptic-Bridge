"""scm_update.py - check for, and apply, updates from the public SC-Motion-Haptic-Bridge
releases. Called by the launchers (scripts/sc_start.py on Linux, setup/windows/sc_start_win.py
on Windows) before anything starts.

What the launcher does with it
  - Up to date, no network, no release published yet, or a developer checkout: one line, no
    pause, the launcher carries on.
  - A newer release exists: the launcher pauses, lists exactly which files would change, and
    offers [u]pdate / [s]kip / [q]uit. Without a terminal it only prints the notice.

Where the information comes from
  - the newest release (pre-releases included) of SC_UPDATE_REPO on GitHub;
  - that release's release.json (version + sha256 and role of every file), read at the
    release tag. Every downloaded file must match its sha256 before anything is replaced.

What an update changes
  - only files whose content differs from the release (compared by sha256), plus files that
    are new in it. Nothing is deleted. Each replaced file is kept in
    ~/sc_motion_update_backup/<old>_to_<new>_<time>/ first (with a manifest.json).
  - never: your effects.json (config/effects.json, ~/sc_haptics/effects.json), SimHub's own
    settings and profiles (Motion tab, Motion haptics, ShakeIt, controllers), your
    certificates and keys, your saved locations (config.json).
  - SimHub's game definition "Star Citizen" only when the release changed it: the registered
    file is renamed to *.bak-<time> and the new one installed (SimHub must be closed).
  - the SC Haptics plugin only when its source changed: rebuilt with dotnet and reinstalled
    (SimHub must be closed; the old DLL is kept in ~/sc_haptics/backup).
  - Files whose role is "motion" or "simdef" shape what reaches the motion platform. When any
    of them changes (or the release says so), the launcher shows a warning that platform
    movement values HAVE changed and asks you to type YES before updating; the warning is
    repeated at the rig pause of the restarted launcher.

Kinds of install
  release      a downloaded release folder (release.json, no .git): files are replaced in place
  git-public   a git clone of the public repository: `git fetch` + fast-forward to the tag
  developer    any other git checkout without release.json (for example the private working
               repository): reported, never updated by the launcher
  Windows also copies the runtime files into %USERPROFILE%\\sc-motion afterwards (the same
  files the installer's step 6 copies). Without a saved repository folder it can only tell
  you to download the release and run Install-SCMotion.ps1 -Apply.

Environment
  SC_UPDATE_CHECK=0        no check at all
  SC_UPDATE_REPO           owner/name on GitHub (default DMUSER15/SC-Motion-Haptic-Bridge)
  SC_UPDATE_TIMEOUT        seconds per request (default 5)
  SC_UPDATE_API, SC_UPDATE_RAW   other base URLs (tests)
Only the Python standard library is used.
"""
import datetime, hashlib, json, os, re, shutil, subprocess, sys, time, urllib.error, urllib.parse, urllib.request

REPO_SLUG = os.environ.get("SC_UPDATE_REPO", "DMUSER15/SC-Motion-Haptic-Bridge")
API = os.environ.get("SC_UPDATE_API", "https://api.github.com").rstrip("/")
RAW = os.environ.get("SC_UPDATE_RAW", "https://raw.githubusercontent.com").rstrip("/")
TIMEOUT = float(os.environ.get("SC_UPDATE_TIMEOUT", "5") or 5)
MANIFEST = "release.json"
MAX_FILE = 20 * 1024 * 1024
# never written by an update (only created when missing), whatever a release says
PROTECTED = ("config/effects.json",)
MOTION_ROLES = ("motion", "simdef")
DONE_ENV = "SC_UPDATE_DONE"
UA = "SC-Motion-Haptic-Bridge-updater"


# ----------------------------------------------------------------------------- versions
def parse_version(text):
    """'v0.1.0-alpha.2' -> comparable key, or None. Semantic-versioning order: a release is
    newer than its pre-releases; pre-release parts compare numerically when numeric."""
    m = re.match(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+.*)?$", (text or "").strip())
    if not m:
        return None
    core = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
    pre = m.group(4)
    if not pre:
        return core + ((1,),)
    ids = tuple((0, int(x), "") if x.isdigit() else (1, 0, x) for x in pre.split("."))
    return core + ((0,) + ids,)


# ----------------------------------------------------------------------------- network
def _get(url, timeout=None, limit=MAX_FILE):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("response larger than %d bytes: %s" % (limit, url))
    return data


def latest_release():
    """The newest non-draft release of REPO_SLUG (pre-releases included), or None."""
    data = json.loads(_get("%s/repos/%s/releases?per_page=30" % (API, REPO_SLUG), limit=4 << 20))
    best = None
    for r in data if isinstance(data, list) else []:
        if not isinstance(r, dict) or r.get("draft"):
            continue
        v = parse_version(r.get("tag_name"))
        if v is not None and (best is None or v > best[0]):
            best = (v, r)
    return best[1] if best else None


def raw_url(tag, path):
    return "%s/%s/%s/%s" % (RAW, REPO_SLUG, urllib.parse.quote(tag, safe=""), urllib.parse.quote(path))


# ----------------------------------------------------------------------------- manifests
_SAFE = re.compile(r"^[A-Za-z0-9 ._()+-]+$")


def safe_path(p):
    """A release path we accept: relative, '/'-separated, no '..', no .git, plain characters."""
    if not isinstance(p, str) or not p or p.startswith("/") or "\\" in p or ":" in p:
        return False
    parts = p.split("/")
    return all(x not in ("", ".", "..", ".git") and _SAFE.match(x) for x in parts)


def check_manifest(m):
    """-> problem text, or None if the manifest is usable."""
    if not isinstance(m, dict):
        return "not a JSON object"
    if parse_version(m.get("version")) is None:
        return "no valid version"
    files = m.get("files")
    if not isinstance(files, dict) or not files:
        return "no file list"
    for p, meta in files.items():
        if not safe_path(p):
            return "unsafe path %r" % p
        if not isinstance(meta, dict) or not re.match(r"^[0-9a-f]{64}$", str(meta.get("sha256", ""))):
            return "bad sha256 for %s" % p
    return None


def read_manifest(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return None
    return m if check_manifest(m) is None else None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def local_path(root, rel):
    return os.path.join(root, *rel.split("/"))


def plan_changes(root, remote):
    """[(path, role, 'new'|'changed')] for the files of remote that differ in root."""
    out = []
    for p, meta in sorted(remote["files"].items()):
        lp = local_path(root, p)
        role = meta.get("role", "")
        if p in PROTECTED or role == "user":
            if not os.path.exists(lp):
                out.append((p, role, "new"))
            continue
        if not os.path.isfile(lp):
            out.append((p, role, "new"))
        elif sha256_file(lp) != meta["sha256"]:
            out.append((p, role, "changed"))
    return out


# ----------------------------------------------------------------------------- local install
def git_origin(root):
    """URL of remote 'origin' from .git/config, or None."""
    try:
        with open(os.path.join(root, ".git", "config"), encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    m = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', text, re.S)
    return m.group(1) if m else None


def find_local(repo, install_dir=None):
    """-> dict(kind, root, manifest, version, detail). kind: release | git-public | developer |
    norepo (Windows install folder only) | unknown."""
    root = repo if repo and os.path.isdir(repo) else None
    if root:
        m = read_manifest(os.path.join(root, MANIFEST))
        has_git = os.path.isdir(os.path.join(root, ".git"))
        if m is None:
            if has_git:
                return dict(kind="developer", root=root, manifest=None, version=None,
                            detail="developer checkout %s (no %s)" % (root, MANIFEST))
        else:
            if has_git:
                origin = git_origin(root) or ""
                if REPO_SLUG.lower() not in origin.lower().replace(":", "/"):
                    return dict(kind="developer", root=root, manifest=m, version=m["version"],
                                detail="git checkout of %s" % (origin or "an unknown remote"))
                return dict(kind="git-public", root=root, manifest=m, version=m["version"], detail="git clone")
            return dict(kind="release", root=root, manifest=m, version=m["version"], detail="release folder")
    if install_dir:
        m = read_manifest(os.path.join(install_dir, MANIFEST))
        if m is not None:
            return dict(kind="norepo", root=None, manifest=m, version=m["version"],
                        detail="install folder only (repository folder not known)")
    return dict(kind="unknown", root=root, manifest=None, version=None, detail="no %s found" % MANIFEST)


# ----------------------------------------------------------------------------- applying
def download_all(tag, changes, remote, stage):
    """Download every changed file into stage/ and check its sha256. -> [problem] (empty = ok)."""
    bad = []
    for p, _, _ in changes:
        want = remote["files"][p]["sha256"]
        try:
            data = _get(raw_url(tag, p), timeout=max(TIMEOUT, 30))
        except Exception as e:  # noqa: BLE001
            bad.append("%s: download failed (%s)" % (p, e))
            continue
        if hashlib.sha256(data).hexdigest() != want:
            bad.append("%s: sha256 does not match the release" % p)
            continue
        dst = local_path(stage, p)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(data)
    return bad


def replace_files(root, stage, changes, backup_dir):
    """Move staged files into root; the old versions go to backup_dir first."""
    done = []
    for p, role, kind in changes:
        src, dst = local_path(stage, p), local_path(root, p)
        if os.path.lexists(dst):
            b = local_path(backup_dir, p)
            os.makedirs(os.path.dirname(b), exist_ok=True)
            shutil.copy2(dst, b)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.replace(src, dst)
        done.append({"path": p, "role": role, "kind": kind})
    return done


def run(cmd, **kw):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, **kw)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return r.returncode, (r.stdout + r.stderr).strip()


def git_update(root, tag):
    """Fast-forward a public git clone to the release tag. -> (ok, text)."""
    if not shutil.which("git"):
        return False, "git not found on PATH"
    rc, out = run(["git", "-C", root, "fetch", "--tags", "origin"])
    if rc:
        return False, "git fetch failed: " + out[-400:]
    rc, out = run(["git", "-C", root, "merge", "--ff-only", "refs/tags/" + tag])
    if rc:
        return False, ("git could not fast-forward to %s (local changes or commits?): %s. Nothing was "
                       "changed; update by hand with git, or download the release." % (tag, out[-400:]))
    return True, "fast-forwarded to %s" % tag


def _sys_path(*dirs):
    for d in dirs:
        if d not in sys.path:
            sys.path.insert(0, d)


def post_linux(root, home, changes, say):
    """Links, game definition and plugin after the files changed. -> [(ok, text)]."""
    _sys_path(os.path.join(root, "tools"), os.path.join(root, "setup", "linux"), os.path.join(root, "setup", "common"))
    out = []
    paths = {p for p, _, _ in changes}
    import scm_linux as L  # noqa: E402
    if any(p.startswith(("scripts/", "plugin/", "config/effects.json")) or p == "tools/linkmap.json" for p in paths):
        import scm_manifest as M  # noqa: E402
        import repo_link  # noqa: E402
        st = [x for x in repo_link.states(home, M.is_install_item) if x[3] in ("SAME", "MISSING")]
        if st:
            rc = repo_link.cmd_link(home, None, M.is_install_item)
            out.append((rc == 0, "linked %d new file(s) into your home folder" % len(st)))
    shdir = L.simhub_dir()
    if any(r == "simdef" for _, r, _ in changes):
        import scm_simdef as SD  # noqa: E402
        out.append(SD.replace(L.simdef_place(root, shdir)))
    if any(p.startswith("plugin/src/") for p in paths):
        csproj = os.path.join(home, "sc_haptics", "src", "SCHaptics.csproj")
        game = L.load_config().get("game_prefix")
        env = dict(L.full_env(game, shdir), DOTNET_CLI_TELEMETRY_OPTOUT="1")
        if not shutil.which("dotnet"):
            out.append((False, "plugin source changed but dotnet is missing: sudo pacman -S --needed dotnet-sdk, "
                               "then (SimHub closed): dotnet build ~/sc_haptics/src/SCHaptics.csproj -c Release; "
                               "python3 ~/sc_haptics_install.py install"))
        elif not os.path.exists(csproj):
            out.append((False, "plugin source not linked in ~/sc_haptics/src: run the installer's apply"))
        else:
            say("rebuilding the SC Haptics plugin (dotnet build) ...")
            rc, txt = run(["dotnet", "build", csproj, "-c", "Release"], env=env)
            if rc:
                out.append((False, "dotnet build failed: " + "\n".join(txt.splitlines()[-8:])))
            else:
                rc, txt = run([sys.executable, os.path.join(home, "sc_haptics_install.py"), "install"], env=env)
                out.append((rc == 0, "plugin: " + (txt.splitlines()[-1] if txt else "installed")))
    return out


def post_windows(root, home, changes, install_dir, simhub_dir, say):
    """Install-folder copies, game definition, plugin and default effects. -> [(ok, text)]."""
    _sys_path(os.path.join(root, "setup", "common"), os.path.join(root, "tools"),
              os.path.join(root, "setup", "windows"), os.path.join(root, "setup", "linux"))
    out = []
    paths = {p for p, _, _ in changes}
    import sc_install_win as W  # noqa: E402
    n = 0
    os.makedirs(install_dir, exist_ok=True)
    for name, src in W.files_to_install().items():
        dst = os.path.join(install_dir, name)
        if os.path.isfile(src) and (not os.path.isfile(dst) or sha256_file(dst) != sha256_file(src)):
            shutil.copyfile(src, dst)
            n += 1
    out.append((True, "%d file(s) copied to %s" % (n, install_dir)))
    if "plugin/effects.default.json" in paths:
        d = os.path.join(home, "sc_haptics")
        os.makedirs(d, exist_ok=True)
        shutil.copyfile(os.path.join(root, "plugin", "effects.default.json"), os.path.join(d, "effects.default.json"))
        out.append((True, "new effects.default.json in %s (your effects.json is unchanged)" % d))
    if any(r == "simdef" for _, r, _ in changes):
        import scm_simdef as SD  # noqa: E402
        la = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        out.append(SD.replace(SD.Place(root, simhub_dir, la)))
    if any(p.startswith("plugin/src/") for p in paths):
        if not shutil.which("dotnet"):
            out.append((False, "plugin source changed but the .NET SDK is missing: winget install Microsoft.DotNet.SDK.8, "
                               "then start the launcher again (or copy a SCHaptics.dll built elsewhere)"))
        else:
            say("rebuilding the SC Haptics plugin (dotnet build) ...")
            proj = os.path.join(root, "plugin", "src", "SCHaptics.csproj")
            env = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1")
            rc, txt = run(["dotnet", "build", proj, "-c", "Release", "-p:SimHubDir=" + simhub_dir.rstrip("\\/") + os.sep], env=env)
            import glob
            built = glob.glob(os.path.join(root, "plugin", "src", "bin", "Release", "*", "SCHaptics.dll"))
            if rc or not built:
                out.append((False, "dotnet build failed: " + "\n".join(txt.splitlines()[-8:])))
            else:
                dst = os.path.join(simhub_dir, "SCHaptics.dll")
                if os.path.isfile(dst):
                    b = os.path.join(home, "sc_haptics", "backup")
                    os.makedirs(b, exist_ok=True)
                    shutil.copy2(dst, os.path.join(b, "SCHaptics.dll." + time.strftime("%Y%m%d_%H%M%S")))
                shutil.copyfile(built[0], dst)
                out.append((sha256_file(dst) == sha256_file(built[0]), "plugin installed in %s" % simhub_dir))
    return out


# ----------------------------------------------------------------------------- the launcher's gate
def motion_banner(lines_out, files=None, old=None, new=None):
    w = lines_out
    w("")
    w("  " + "!" * 74)
    w("  !!  PLATFORM MOVEMENT VALUES HAVE CHANGED%s" % ((" (%s -> %s)" % (old, new)) if old and new else ""))
    w("  " + "!" * 74)
    if files:
        w("  Changed files that shape what reaches your motion platform:")
        for p in files:
            w("    " + p)
    w("  Before anyone rides:")
    w("   - check your SimHub Motion settings for Star Citizen: Motion tab (axes, intensity,")
    w("     smoothing, limits) and Motion haptics (gain, smoothing);")
    w("   - first run: nobody in the rig and motion power OFF, then power ON with nobody in")
    w("     the rig, then a person. E-stop within reach.")
    w("")


def _ask(prompt, choices, default):
    while True:
        try:
            a = input(prompt).strip().lower()
        except EOFError:
            return default
        if not a:
            return default
        if a[:1] in choices:
            return a[:1]


def _restart(env_value):
    os.environ[DONE_ENV] = env_value
    sys.stdout.flush()
    if os.name == "nt":
        rc = subprocess.call([sys.executable] + sys.argv)
        os._exit(rc)
    os.execv(sys.executable, [sys.executable] + sys.argv)


def gate(platform, repo, home, say, game_running, simhub_running, install_dir=None, simhub_dir=None,
         interactive=None):
    """Run the check (and, if you choose, the update). Never raises. -> True when an update
    applied in this start changed motion values (the launcher repeats the warning)."""
    try:
        return _gate(platform, repo, home, say, game_running, simhub_running, install_dir, simhub_dir,
                     sys.stdin.isatty() if interactive is None else interactive)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        raise
    except Exception as e:  # noqa: BLE001
        say("update check skipped (error: %s)" % e)
        return False


def _gate(platform, repo, home, say, game_running, simhub_running, install_dir, simhub_dir, tty):
    done = os.environ.pop(DONE_ENV, None)
    if done:
        old, new, motion = (done.split("|") + ["", "", "0"])[:3]
        say("updated SC-Motion-Haptic-Bridge %s -> %s" % (old, new))
        return motion == "1"
    if os.environ.get("SC_UPDATE_CHECK") == "0":
        say("update check off (SC_UPDATE_CHECK=0)")
        return False
    loc = find_local(repo, install_dir)
    if loc["kind"] in ("developer", "unknown"):
        say("update check: %s; not updated by the launcher" % loc["detail"])
        return False
    mine = loc["version"]
    try:
        rel = latest_release()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            say("update check: no public releases found (%s); continuing with %s" % (REPO_SLUG, mine))
        else:
            say("update check: GitHub answered HTTP %d (rate limit?); continuing with %s" % (e.code, mine))
        return False
    except Exception as e:  # noqa: BLE001
        say("update check: GitHub not reachable (%s); continuing with %s" % (getattr(e, "reason", e), mine))
        return False
    if rel is None:
        say("update check: no release published yet; you have %s" % mine)
        return False
    tag = rel.get("tag_name")
    if parse_version(tag) <= parse_version(mine):
        say("update check: up to date (%s)" % mine)
        return False
    page = rel.get("html_url") or "https://github.com/%s/releases" % REPO_SLUG
    _loud(say, "UPDATE AVAILABLE: SC-Motion-Haptic-Bridge %s -> %s (%s)" % (mine, tag, page))
    notes = [x.rstrip() for x in (rel.get("body") or "").splitlines() if x.strip()][:12]
    if notes:
        print("  release notes:")
        for x in notes:
            print("    " + x[:110])
    try:
        remote = json.loads(_get(raw_url(tag, MANIFEST), limit=4 << 20))
        problem = check_manifest(remote)
    except Exception as e:  # noqa: BLE001
        remote, problem = None, "could not read %s (%s)" % (MANIFEST, e)
    if problem:
        say("the release's %s is not usable (%s); download the release from %s" % (MANIFEST, problem, page))
        return _hold(tty, say)
    if game_running():
        say("Star Citizen is running: update after the game (start the launcher again then)")
        return False
    if loc["kind"] == "norepo":
        say("this install does not know its repository folder. Download %s, then run "
            "Install-SCMotion.ps1 -Apply from it (your settings are kept)" % page)
        return _hold(tty, say)
    root = loc["root"]
    changes = plan_changes(root, remote)
    motion = [p for p, r, _ in changes if r in MOTION_ROLES]
    flagged = bool(remote.get("motion_values_changed"))
    needs_closed = [p for p, r, _ in changes if r == "simdef" or p.startswith("plugin/src/")]
    print("  files that will change (only these; nothing is deleted):")
    for p, r, k in changes:
        extra = ""
        if r == "simdef":
            extra = "  (SimHub's game definition is replaced; the old one is kept as .bak-<time>)"
        elif p.startswith("plugin/src/"):
            extra = "  (the plugin is rebuilt and reinstalled)"
        print("    %-7s %-8s %s%s" % (k, r or "-", p, extra))
    if not changes:
        print("    none (your files already match %s; only the version record changes)" % tag)
    print("  never touched: your effects.json, SimHub's settings and profiles, your certificates, your")
    print("  saved locations. Backups of replaced files: %s" % os.path.join(home, "sc_motion_update_backup"))
    if motion or flagged:
        motion_banner(print, motion, mine, tag)
    if not tty:
        say("no terminal: not updating now. Start the launcher from its menu entry to update.")
        return False
    while True:
        a = _ask("  [u]pdate now, [s]kip this time, or [q]uit? (u/s/q, Enter = skip): ", "usq", "s")
        if a == "s":
            say("update skipped; starting %s" % mine)
            return False
        if a == "q":
            say("stopped before starting anything (update not applied)")
            sys.exit(0)
        if needs_closed and simhub_running():
            say("SimHub is running. The plugin and the game definition can only be replaced while SimHub "
                "is closed. Close SimHub, then press u again.")
            continue
        if motion or flagged:
            try:
                yes = input("  Type YES to confirm you will check your SimHub motion settings before "
                            "riding: ").strip()
            except EOFError:
                yes = ""
            if yes.upper() != "YES":
                say("not confirmed; update skipped")
                return False
        break
    ok = _apply(loc, root, home, tag, remote, changes, platform, install_dir, simhub_dir, say)
    if not ok:
        return _hold(tty, say)
    _restart("%s|%s|%s" % (mine, tag, "1" if (motion or flagged) else "0"))
    return False  # not reached


def _loud(say, text):
    """say() with speech when the launcher's say() supports it."""
    try:
        say(text, speak=True)
    except TypeError:
        say(text)


def _hold(tty, say):
    """Pause so the message can be read; q stops the launcher."""
    if tty and _ask("  Press Enter to continue, or q to quit: ", "q", "") == "q":
        say("stopped before starting anything")
        sys.exit(0)
    return False


def _apply(loc, root, home, tag, remote, changes, platform, install_dir, simhub_dir, say):
    old = loc["version"]
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(home, "sc_motion_update_backup", "%s_to_%s_%s" % (old, tag, stamp))
    if loc["kind"] == "git-public":
        os.makedirs(backup, exist_ok=True)
        for p, _, _ in changes:
            lp = local_path(root, p)
            if os.path.isfile(lp):
                b = local_path(backup, p)
                os.makedirs(os.path.dirname(b), exist_ok=True)
                shutil.copy2(lp, b)
        ok, txt = git_update(root, tag)
        say(txt)
        if not ok:
            return False
        done = [{"path": p, "role": r, "kind": k} for p, r, k in changes]
    else:
        stage = os.path.join(root, ".update-stage-" + stamp)
        os.makedirs(stage, exist_ok=True)
        try:
            say("downloading %d file(s) from %s ..." % (len(changes), tag))
            bad = download_all(tag, changes, remote, stage)
            if bad:
                for x in bad:
                    print("    " + x)
                say("update NOT applied (nothing was changed)")
                return False
            os.makedirs(backup, exist_ok=True)
            done = replace_files(root, stage, changes, backup)
            if os.path.isfile(os.path.join(root, MANIFEST)):
                shutil.copy2(os.path.join(root, MANIFEST), os.path.join(backup, MANIFEST))
            with open(os.path.join(root, MANIFEST), "w", encoding="utf-8") as f:
                json.dump(remote, f, indent=1, sort_keys=True)
                f.write("\n")
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    with open(os.path.join(backup, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"from": old, "to": tag, "time": stamp, "kind": loc["kind"], "files": done}, f, indent=1)
        f.write("\n")
    say("%d file(s) updated; old versions in %s" % (len(done), backup))
    if platform == "windows":
        steps = post_windows(root, home, changes, install_dir, simhub_dir, say)
        if install_dir:
            shutil.copyfile(os.path.join(root, MANIFEST), os.path.join(install_dir, MANIFEST))
    else:
        steps = post_linux(root, home, changes, say)
    failed = False
    for ok, txt in steps:
        say(("  done: " if ok else "  NOT DONE: ") + txt)
        failed = failed or not ok
    if failed:
        say("the files are updated, but the step(s) marked NOT DONE above need you. The pipeline still "
            "starts; fix them when convenient (the verifier shows what is missing).")
        _hold(True, say)
    return True
