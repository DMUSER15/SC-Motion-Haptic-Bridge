"""scm_appupdate.py - update check for every installed app, for the control window.

Apps
  bridge   this repository. A release folder or public git clone: the public releases on GitHub
           (setup/common/scm_update.py, the same files, checks and backups as the launcher). Any
           other git checkout (the private working repository): its own git remote.
  thanos   simhub-thanos-linux (Linux only, if installed): its git remote.
  mfd      the MFD add-on (if installed): a git clone of its repository (setting mfd_source,
           default ~/SC-Motion-MFD) checked against its remote and against the installed
           version; without a clone, the newest GitHub release of the catalog's repository.
check_all() never changes anything (it runs `git fetch`, which only updates the clone's record
of the remote). apply() changes only the app it is given, and refuses when that would lose or
mix work: local changes, local commits not on the remote, the game running, or SimHub running
when the game definition or the plugin would change.

Every check gives: current -> available version, a summary of the changes (the new CHANGELOG
sections, else the commit subjects), and whether it changes platform movement values (files
with role motion / simdef). A motion change must be confirmed by typing YES, like the launcher.
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
COMMON = os.path.join(REPO, "setup", "common")
for _d in (COMMON, os.path.join(REPO, "setup", "linux"), os.path.join(REPO, "tools")):
    if _d not in sys.path:
        sys.path.append(_d)
import scm_update as U  # noqa: E402

HOME = os.path.expanduser("~")
GIT_TIMEOUT = 30
SECTIONS = 5
BULLETS = 3
MOTION_FILES = {"scripts/lm_simhub3.py", "scripts/lm_simhub3t.py", "scripts/lm_simhub4.py", "scripts/lm_simhub4t.py",
                "scripts/SC_Motion_Server.py", "scripts/motion_desc.bin", "scripts/sc_fx.py",
                "config/simhub/Star Citizen.simdef"}


def role_of(path):
    try:
        import scm_export
        return scm_export.role(path)
    except Exception:  # noqa: BLE001  the export tool is private; same rule as a fallback
        if path == "config/simhub/Star Citizen.simdef":
            return "simdef"
        return "motion" if path in MOTION_FILES else ""


# ------------------------------------------------------------------ git
def git(root, *args, timeout=GIT_TIMEOUT):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    try:
        r = subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True, timeout=timeout,
                           env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return r.returncode, (r.stdout if r.returncode == 0 else (r.stderr or r.stdout)).strip()


def changelog_sections(text):
    """-> OrderedDict-like list [(heading, [bullet lines])] of '## ' sections."""
    out = []
    cur = None
    for line in (text or "").splitlines():
        if line.startswith("## "):
            cur = (line[3:].strip(), [])
            out.append(cur)
        elif cur is not None and line.lstrip().startswith(("- ", "* ")) and not line.startswith("  "):
            cur[1].append(line.strip()[2:].strip())
    return out


def new_sections(old, new):
    seen = {h for h, _ in changelog_sections(old)}
    return [(h, b) for h, b in changelog_sections(new) if h not in seen]


def summarize(sections, subjects):
    lines = []
    for h, bullets in sections[:SECTIONS]:
        lines.append(h)
        for b in bullets[:BULLETS]:
            lines.append("  - " + (b if len(b) <= 160 else b[:157] + "..."))
        if len(bullets) > BULLETS:
            lines.append("  - (+%d more)" % (len(bullets) - BULLETS))
    if len(sections) > SECTIONS:
        lines.append("(+%d more sections)" % (len(sections) - SECTIONS))
    if not lines:
        lines = ["- " + s for s in subjects[:10]] or ["(no change notes)"]
    return lines


def changelog_blocks(text):
    """-> [(heading, full text of the section)] for every '## ' section."""
    out, cur = [], None
    for line in (text or "").splitlines():
        if line.startswith("## "):
            cur = [line]
            out.append(cur)
        elif cur is not None:
            cur.append(line)
    return [(b[0][3:].strip(), "\n".join(b).rstrip()) for b in out]


def new_sections_raw(old, new):
    """The '## ' sections of new that old does not have, as their full text (wrapped lines and
    sub-headings kept): what the Show changes button shows."""
    seen = {h for h, _ in changelog_blocks(old)}
    return "\n\n".join(txt for h, txt in changelog_blocks(new) if h not in seen)


def web_url(root):
    """The clone's GitHub page (https) from its origin, or "" when it is not on GitHub."""
    rc, url = git(root, "remote", "get-url", "origin", timeout=10)
    m = re.search(r"github\.com[:/]+([A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$", url.strip()) if rc == 0 else None
    return "https://github.com/%s" % m.group(1) if m else ""


def git_check(root, version_at, changelog="CHANGELOG.md"):
    """-> dict(ok, current, available, behind, ahead, dirty, files, summary, problem)."""
    res = {"ok": False, "current": None, "available": None, "behind": 0, "ahead": 0, "dirty": False,
           "files": [], "summary": [], "problem": None, "upstream": None}
    if not os.path.isdir(os.path.join(root, ".git")):
        res["problem"] = "%s is not a git clone" % root
        return res
    rc, out = git(root, "fetch", "--quiet", "--tags", "origin")
    if rc:
        lines = [x.strip() for x in out.splitlines() if x.strip()]
        why = next((x for x in lines if x.lower().startswith(("fatal:", "error:"))), lines[0] if lines else "git fetch failed")
        res["problem"] = "could not reach the remote (%s); showing what was known before" % why[:200]
    rc, up = git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if rc:
        res["problem"] = res["problem"] or "this branch follows no remote branch"
        res["current"] = version_at(root, "HEAD")
        return res
    res["upstream"] = up
    res["current"] = version_at(root, "HEAD")
    res["available"] = version_at(root, "@{u}")
    rc, n = git(root, "rev-list", "--count", "HEAD..@{u}")
    res["behind"] = int(n) if rc == 0 and n.isdigit() else 0
    rc, n = git(root, "rev-list", "--count", "@{u}..HEAD")
    res["ahead"] = int(n) if rc == 0 and n.isdigit() else 0
    rc, st = git(root, "status", "--porcelain", "--untracked-files=no")
    res["dirty"] = rc == 0 and bool(st.strip())
    if res["behind"]:
        rc, files = git(root, "diff", "--name-only", "HEAD...@{u}")
        res["files"] = [f for f in files.splitlines() if f.strip()] if rc == 0 else []
        _, old = git(root, "show", "HEAD:" + changelog)
        rc2, new = git(root, "show", "@{u}:" + changelog)
        rc3, subj = git(root, "log", "--format=%s", "-n", "12", "HEAD..@{u}")
        secs = new_sections(old, new) if rc2 == 0 else []
        res["summary"] = summarize(secs, subj.splitlines() if rc3 == 0 else [])
        res["changes"] = (new_sections_raw(old, new) if rc2 == 0 else "") or "\n".join(
            "- " + s for s in (subj.splitlines() if rc3 == 0 else []))
    res["ok"] = True
    return res


def describe(root, ref):
    rc, out = git(root, "describe", "--tags", "--always", ref)
    return out if rc == 0 else None


def file_at(root, ref, path):
    rc, out = git(root, "show", "%s:%s" % (ref, path))
    return out if rc == 0 else None


def version_from_py(text):
    m = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', text or "", re.M)
    return m.group(1) if m else None


def version_newer(a, b):
    """a is a newer version than b. An unreadable version on either side is never newer (and
    never raises: one app's odd version string must not hide the others)."""
    va, vb = U.parse_version(a), U.parse_version(b)
    if va is None:
        return False
    if vb is None:
        return True
    try:
        return va > vb
    except TypeError:
        return False


def recheck_git(root):
    """Local changes or local commits made since the check. -> reason or None."""
    rc, st = git(root, "status", "--porcelain", "--untracked-files=no")
    if rc:
        return "git status failed (%s)" % st[-200:]
    rc, n = git(root, "rev-list", "--count", "@{u}..HEAD")
    return blocked_git({"dirty": bool(st.strip()), "ahead": int(n) if rc == 0 and n.isdigit() else 0})


def blocked_git(r):
    if r["dirty"]:
        return "the clone has local changes that are not committed (commit or stash them, then update)"
    if r["ahead"]:
        return "the clone has %d local commit(s) that are not on the remote (push them or update by hand)" % r["ahead"]
    return None


def ff_merge(root):
    rc, out = git(root, "merge", "--ff-only", "@{u}", timeout=120)
    return rc == 0, out


# ------------------------------------------------------------------ the apps
def app_record(app_id, title, **kw):
    d = {"id": app_id, "title": title, "installed": True, "kind": "", "where": "", "current": None,
         "url": "", "release_url": "", "changes": "",
         "available": None, "update": False, "summary": [], "motion": False, "motion_files": [],
         "needs_simhub_closed": False, "blocked": None, "problem": None, "notes": []}
    d.update(kw)
    return d


def check_bridge(repo=REPO):
    loc = U.find_local(repo)
    if loc["kind"] == "developer":
        r = git_check(loc["root"], describe)
        rec = app_record("bridge", "Motion bridge (SC Motion Haptic Bridge)", kind="git", where=loc["root"],
                         current=r["current"], available=r["available"], problem=r["problem"], url=web_url(loc["root"]))
        if r["behind"]:
            mfiles = [p for p in r["files"] if role_of(p) in U.MOTION_ROLES]
            rec.update(update=True, summary=r["summary"], changes=r.get("changes", ""), motion=bool(mfiles), motion_files=mfiles,
                       needs_simhub_closed=any(role_of(p) == "simdef" or p.startswith("plugin/src/") for p in r["files"]),
                       blocked=blocked_git(r), notes=["%d commit(s) on %s" % (r["behind"], r["upstream"])],
                       _files=r["files"])
        return rec
    if loc["kind"] in ("release", "git-public"):
        rec = app_record("bridge", "Motion bridge (SC Motion Haptic Bridge)", kind=loc["kind"], where=loc["root"],
                         current=loc["version"], url="https://github.com/%s" % U.REPO_SLUG)
        try:
            rel = U.latest_release()
        except Exception as e:  # noqa: BLE001
            rec["problem"] = "GitHub not reachable (%s)" % getattr(e, "reason", e)
            return rec
        if rel is None or not version_newer(rel.get("tag_name"), loc["version"]):
            rec["available"] = rel.get("tag_name") if rel else None
            return rec
        tag = rel["tag_name"]
        try:
            remote = json.loads(U._get(U.raw_url(tag, U.MANIFEST), limit=4 << 20))
            problem = U.check_manifest(remote)
        except Exception as e:  # noqa: BLE001
            remote, problem = None, "could not read %s (%s)" % (U.MANIFEST, e)
        notes = [x.rstrip() for x in (rel.get("body") or "").splitlines() if x.strip()]
        rec.update(available=tag, update=True, summary=summarize(changelog_sections("\n".join(notes)), notes),
                   changes="\n".join(notes), release_url="https://github.com/%s/releases/tag/%s" % (U.REPO_SLUG, tag))
        if problem:
            rec["blocked"] = "the release's %s is not usable (%s)" % (U.MANIFEST, problem)
            return rec
        changes = U.plan_changes(loc["root"], remote)
        mfiles = [p for p, r, _ in changes if r in U.MOTION_ROLES]
        rec.update(motion=bool(mfiles) or bool(remote.get("motion_values_changed")), motion_files=mfiles,
                   needs_simhub_closed=any(r == "simdef" or p.startswith("plugin/src/") for p, r, _ in changes),
                   notes=["%d file(s) change" % len(changes)], _loc=loc, _tag=tag, _remote=remote, _changes=changes)
        return rec
    return app_record("bridge", "Motion bridge (SC Motion Haptic Bridge)", kind=loc["kind"], where=loc.get("root") or "",
                      problem=loc["detail"])


def check_thanos():
    try:
        import scm_thanos_ext as T
        tool = T.tool_path()
    except Exception:  # noqa: BLE001
        return None
    root = os.path.dirname(tool)
    if not os.path.isfile(tool):
        return None
    r = git_check(root, lambda rt, ref: version_from_py(file_at(rt, ref, "simhub_thanos.py")) or describe(rt, ref))
    rec = app_record("thanos", "THANOS connection tool (simhub-thanos-linux)", kind="git", where=root,
                     current=r["current"], available=r["available"], problem=r["problem"], url=web_url(root))
    if r["behind"]:
        rec.update(update=True, summary=r["summary"], changes=r.get("changes", ""), blocked=blocked_git(r),
                   notes=["Updating only replaces the tool. If you then run its apply (it changes COM ports): "
                          "SimHub closed, cabinet OFF."])
    return rec


def addon_module():
    try:
        import scm_addons
        return scm_addons
    except Exception:  # noqa: BLE001
        return None


def mfd_source(settings):
    """The MFD clone to install / update from; "" = none ("release" in the setting: always the newest release)."""
    v = (settings or {}).get("mfd_source") or os.environ.get("SC_MFD_SOURCE") or "~/SC-Motion-MFD"
    return "" if v.strip().lower() == "release" else os.path.expanduser(v)


def install_from_clone(A, root, say):
    """Install the add-on from a clone's committed HEAD (git archive), not its folder: ignored files in a
    working clone (download caches, logs, dist/) are not in release.json and the package check would
    refuse the folder, and uncommitted edits must never be installed. -> exit code of cmd_install."""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="sc-mfd-clone-")
    tar = os.path.join(tmp, "head.tar")
    try:
        rc, out = git(root, "archive", "--format=tar", "-o", tar, "HEAD", timeout=120)
        if rc:
            say("could not pack the clone's HEAD (%s)" % out[-200:])
            return 2
        return A.cmd_install(tar, ["--yes"], say=say, want_name="mfd")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def _addon_version(root, ref):
    text = file_at(root, ref, "addon.json")
    try:
        return json.loads(text).get("version") if text else None
    except ValueError:
        return None


def check_mfd(settings=None):
    A = addon_module()
    st = A.installed().get("mfd") if A else None
    if not st:
        return None
    installed = st["current"]
    src = mfd_source(settings)
    rec = app_record("mfd", "MFD add-on (SC Motion MFD)", current=installed)
    if src and os.path.isdir(os.path.join(src, ".git")):
        r = git_check(src, _addon_version)
        head_v = r["current"]
        up_v = r["available"] or head_v
        rec.update(kind="git", where=src, problem=r["problem"], available=up_v, url=web_url(src))
        newer = version_newer(up_v, installed)
        if newer or r["behind"]:
            summary, changes = r["summary"], r.get("changes", "")
            if not r["behind"]:
                _, old = git(src, "show", "v%s:CHANGELOG.md" % installed)
                head_cl = file_at(src, "HEAD", "CHANGELOG.md")
                summary, changes = summarize(new_sections(old, head_cl), []), new_sections_raw(old, head_cl)
            # The installer copies the clone's working tree: uncommitted changes or commits that are
            # not on the remote would be installed under a version number that does not describe them.
            rec.update(update=bool(newer), summary=summary, changes=changes, blocked=blocked_git(r),
                       notes=["installed %s, clone %s, remote %s" % (installed, head_v, r["available"] or "?"),
                              "Updating runs the add-on's own installer from %s (it runs as you)." % src],
                       _behind=r["behind"])
            if r["behind"] and not newer:
                rec["notes"].append("the clone is behind its remote, but the version did not change")
        return rec
    entry = next((c for c in A.catalog() if c.get("name") == "mfd"), None)
    if not entry:
        rec["problem"] = "no clone at %s and no catalog entry" % (src or "(mfd_source = release)")
        return rec
    rec.update(kind="release", where=entry["repo"], url="https://github.com/%s" % entry["repo"])
    try:
        info = release_info(entry["repo"])
    except Exception as e:  # noqa: BLE001
        rec["problem"] = "releases of %s not reachable (%s)" % (entry["repo"], getattr(e, "reason", e))
        return rec
    if not info:
        rec["problem"] = "%s has no release yet" % entry["repo"]
        return rec
    rec.update(available=info["version"], release_url=info["release_url"])
    if version_newer(info["tag"], installed):
        old = ""
        try:
            with open(os.path.join(A.version_dir("mfd", installed), "CHANGELOG.md"), encoding="utf-8") as f:
                old = f.read()
        except OSError:
            pass
        secs = new_sections(old, info["changelog"]) if info["changelog"] else []
        rec.update(update=True, summary=summarize(secs, ["release %s of %s" % (info["tag"], entry["repo"])]),
                   changes=new_sections_raw(old, info["changelog"]) or info["notes"] or "(the release has no change notes)")
    return rec


def release_info(slug):
    """Newest GitHub release of slug -> {tag, version, release_url, notes, changelog} or None. changelog =
    the release's CHANGELOG.md ("" when it cannot be read). Raises when GitHub cannot be reached."""
    import urllib.parse
    data = json.loads(U._get("%s/repos/%s/releases?per_page=10" % (U.API, slug), limit=2 << 20))
    rels = [r for r in data if isinstance(r, dict) and not r.get("draft") and U.parse_version(r.get("tag_name")) is not None]
    if not rels:
        return None
    best = max(rels, key=lambda r: U.parse_version(r["tag_name"]))
    tag = best["tag_name"]
    try:
        cl = U._get("%s/%s/%s/CHANGELOG.md" % (U.RAW, slug, urllib.parse.quote(tag, safe="")), limit=2 << 20)
        cl = cl.decode("utf-8", "replace") if isinstance(cl, bytes) else str(cl)
    except Exception:  # noqa: BLE001  the notes still work without it
        cl = ""
    return {"tag": tag, "version": tag[1:] if tag[:1] in "vV" and tag[1:2].isdigit() else tag,
            "release_url": str(best.get("html_url") or "https://github.com/%s/releases/tag/%s" % (slug, tag)),
            "notes": str(best.get("body") or "").strip(), "changelog": cl}


def newest_sections_text(changelog, n=2):
    """The top n '## ' sections of a CHANGELOG, full text (the install dialog's What's new)."""
    return "\n\n".join(txt for _h, txt in changelog_blocks(changelog)[:n])


def check_all(platform="linux", settings=None, repo=REPO):
    out = []
    for fn in (lambda: check_bridge(repo), (check_thanos if platform == "linux" else (lambda: None)),
               lambda: check_mfd(settings)):
        try:
            rec = fn()
        except Exception as e:  # noqa: BLE001  one app's problem never hides the others
            rec = app_record("?", "update check", problem="%s: %s" % (type(e).__name__, e))
        if rec:
            out.append(rec)
    return out


def public(rec):
    """The record without the private apply data (for display / JSON)."""
    return {k: v for k, v in rec.items() if not k.startswith("_")}


# ------------------------------------------------------------------ applying
def apply(rec, say, game_running, simhub_running, home=HOME, platform="linux", install_dir=None, simhub_dir=None):
    """-> (ok, text, restart_needed). Changes only this app. Windows: install_dir (the installed
    runtime copies, %USERPROFILE%\\sc-motion) and simhub_dir are refreshed after the bridge update,
    like the terminal launcher's update does."""
    if not rec.get("update"):
        return False, "%s: nothing to update" % rec["title"], False
    if rec.get("blocked"):
        return False, "%s: %s" % (rec["title"], rec["blocked"]), False
    if game_running():
        return False, "Star Citizen is running: update after the game", False
    if rec.get("needs_simhub_closed") and simhub_running():
        return False, "%s: close SimHub first (the game definition or the plugin changes)" % rec["title"], False
    if rec.get("kind") == "git":
        why = recheck_git(rec["where"])
        if why:
            return False, "%s: %s; nothing was changed" % (rec["title"], why), False
    if rec["id"] == "bridge" and rec["kind"] == "git":
        ok, out = ff_merge(rec["where"])
        if not ok:
            return False, "git could not fast-forward (%s); nothing was changed" % out[-300:], False
        say("bridge: fast-forwarded to %s" % rec["available"])
        changes = [(p, role_of(p), "changed") for p in rec.get("_files", [])]
        failed = []
        try:
            if platform == "linux":
                steps = U.post_linux(rec["where"], home, changes, say)
            elif platform == "windows" and install_dir and simhub_dir:
                steps = U.post_windows(rec["where"], home, changes, install_dir, simhub_dir, say)
            elif platform == "windows":
                steps = [(False, "install folder or SimHub folder unknown: run Install-SCMotion.ps1 -Apply")]
            else:   # TF-4: another platform (tests: "none") has no follow-up steps; not a Windows message
                steps = []
            for ok2, txt in steps:
                say(("  done: " if ok2 else "  NOT DONE: ") + txt)
                if not ok2:
                    failed.append(txt)
        except Exception as e:  # noqa: BLE001
            failed.append("follow-up steps failed (%s)" % e)
        return (not failed), ("bridge updated to %s" % rec["available"]) + ("; needs you: " + "; ".join(failed) if failed else ""), True
    if rec["id"] == "bridge":
        if platform == "windows" and not (install_dir and simhub_dir):
            return False, "bridge update NOT applied: install folder or SimHub folder unknown (run Install-SCMotion.ps1 -Apply)", False
        ok = U._apply(rec["_loc"], rec["_loc"]["root"], home, rec["_tag"], rec["_remote"], rec["_changes"],
                      platform, install_dir, simhub_dir, say)
        return ok, ("bridge updated to %s" % rec["_tag"]) if ok else "bridge update NOT applied", ok
    if rec["id"] == "thanos":
        ok, out = ff_merge(rec["where"])
        return ok, ("THANOS tool updated to %s" % rec["available"]) if ok else "git could not fast-forward (%s)" % out[-300:], False
    if rec["id"] == "mfd":
        A = addon_module()
        if rec["kind"] == "git":
            if rec.get("_behind"):
                ok, out = ff_merge(rec["where"])
                if not ok:
                    return False, "git could not fast-forward the MFD clone (%s)" % out[-300:], False
            rc = install_from_clone(A, rec["where"], say)
        else:
            rc = A.cmd_install("mfd", ["--yes"], say=say, want_name="mfd")
        return rc == 0, ("MFD add-on updated to %s" % rec["available"]) if rc == 0 else "MFD update failed (exit %d)" % rc, False
    return False, "unknown app", False
