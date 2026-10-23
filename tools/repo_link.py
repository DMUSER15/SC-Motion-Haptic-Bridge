#!/usr/bin/env python3
"""repo_link.py - make the files in your home folder symlinks to this repository, so the
setup runs from the repository and `git status` / `git diff` show every change you make.

  python3 tools/repo_link.py status            read-only: state of every mapped file
  python3 tools/repo_link.py link              replace identical home files with symlinks,
                                               create missing ones (fresh machine)
  python3 tools/repo_link.py link --adopt-home a home file that DIFFERS from the repo is copied
                                               into the repo first (then shows in git diff)
  python3 tools/repo_link.py link --use-repo   a home file that DIFFERS is backed up and replaced
                                               by a link to the repo version
  python3 tools/repo_link.py unlink            turn the symlinks back into ordinary files
                                               (same content), e.g. before deleting the repo

The map (repo path -> home path) is tools/linkmap.json, written by repo_collect.py.
Every file that is replaced is first copied to ~/sc_motion_link_backup/<time>/ (+ manifest.json).
States: LINKED, SAME (identical file, link pending), DRIFT (differs), MISSING (no home file),
ELSEWHERE (symlink to somewhere else), NO-REPO-FILE (map entry without repo file).
Some editors save by writing a new file, which replaces the symlink: `status` then shows
SAME or DRIFT for that file; run `link` (or `link --adopt-home`) again.
Exit codes: 0 ok / all linked, 1 usage, 5 something needs attention (status), 6 refused.
"""
import datetime, json, os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scm_manifest as M  # noqa: E402

REPO = os.path.dirname(HERE)


def states(home, only=None):
    """only: optional predicate on the repo path (e.g. M.is_install_item)."""
    out = []
    for rp, hp in sorted(M.load_linkmap(REPO).items()):
        if only is not None and not only(rp):
            continue
        r = os.path.join(REPO, rp)
        h = M.untilde(hp, home)
        if not os.path.isfile(r):
            s = "NO-REPO-FILE"
        elif os.path.islink(h):
            s = "LINKED" if os.path.realpath(h) == os.path.realpath(r) else "ELSEWHERE"
        elif not os.path.exists(h):
            s = "MISSING"
        elif os.path.isfile(h) and M.sha256(h) == M.sha256(r):
            s = "SAME"
        else:
            s = "DRIFT"
        out.append((rp, h, r, s))
    return out


def cmd_status(home, only=None):
    st = states(home, only)
    if not st:
        print("No link map yet (tools/linkmap.json). Run: python3 tools/repo_collect.py apply")
        return 5
    print("repo_link status (read-only)  repo %s" % REPO)
    for rp, h, r, s in st:
        print("  %-12s %-42s <- %s" % (s, rp, M.tilde(h, home) if h.startswith(home) else h))
    bad = [x for x in st if x[3] != "LINKED"]
    print("RESULT: %d of %d linked%s" % (len(st) - len(bad), len(st),
                                         "" if not bad else "; run: python3 tools/repo_link.py link"))
    return 0 if not bad else 5


def backup(home, files):
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bdir = os.path.join(home, "sc_motion_link_backup", stamp)
    n = 1
    while os.path.exists(bdir):
        n += 1
        bdir = os.path.join(home, "sc_motion_link_backup", "%s_%d" % (stamp, n))
    man = []
    for h in files:
        rel = os.path.relpath(h, home)
        dst = os.path.join(bdir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(h, dst, follow_symlinks=True)
        man.append({"path": h, "sha256": M.sha256(h)})
    os.makedirs(bdir, exist_ok=True)
    with open(os.path.join(bdir, "manifest.json"), "w") as f:
        json.dump({"tool": "repo_link", "files": man}, f, indent=1)
    return bdir


def make_link(h, r):
    tmp = os.path.join(os.path.dirname(h), "." + os.path.basename(h) + ".scm_link")
    if os.path.lexists(tmp):
        os.remove(tmp)
    os.symlink(r, tmp)
    os.replace(tmp, h)


def cmd_link(home, mode, only=None):
    st = states(home, only)
    if not st:
        print("No link map yet. Run: python3 tools/repo_collect.py apply")
        return 6
    drift = [x for x in st if x[3] in ("DRIFT", "ELSEWHERE")]
    if drift and mode is None:
        print("REFUSED: these home files differ from the repository copy:")
        for rp, h, r, s in drift:
            print("  %-10s %s  (repo: %s)" % (s, h, rp))
        print("Keep YOUR version (it goes into the repo, see git diff):  link --adopt-home")
        print("Use the REPO version (yours is backed up):               link --use-repo")
        return 6
    missing_repo = [x for x in st if x[3] == "NO-REPO-FILE"]
    for rp, h, r, s in missing_repo:
        print("  skipped (no repo file): %s" % rp)
    todo = [x for x in st if x[3] in ("SAME", "DRIFT", "ELSEWHERE", "MISSING")]
    existing = [h for rp, h, r, s in todo if os.path.exists(h)]
    bdir = backup(home, existing) if existing else None
    if bdir:
        print("backup: %s (%d files)" % (bdir, len(existing)))
    for rp, h, r, s in todo:
        if s in ("DRIFT", "ELSEWHERE") and mode == "adopt-home":
            shutil.copyfile(h, r)
            print("  adopted   %s (your version is now in the repo)" % rp)
        os.makedirs(os.path.dirname(h), exist_ok=True)
        make_link(h, r)
        ok = os.path.islink(h) and os.path.realpath(h) == os.path.realpath(r)
        print("  %-9s %s -> %s" % ("linked" if ok else "FAILED", h, rp))
    already = len([x for x in st if x[3] == "LINKED"])
    if already:
        print("  (%d already linked)" % already)
    return cmd_status(home, only) and 6


def cmd_unlink(home):
    st = [x for x in states(home) if x[3] == "LINKED"]
    if not st:
        print("nothing is linked")
        return 0
    for rp, h, r, s in st:
        tmp = h + ".scm_unlink"
        shutil.copy2(r, tmp)
        os.replace(tmp, h)
        print("  copied    %s (no longer linked)" % h)
    return 0


def main(argv):
    home = os.path.expanduser("~")
    args = argv[1:]
    if args == ["status"] or not args:
        return cmd_status(home)
    if args == ["link"]:
        return cmd_link(home, None)
    if args == ["link", "--adopt-home"]:
        return cmd_link(home, "adopt-home")
    if args == ["link", "--use-repo"]:
        return cmd_link(home, "use-repo")
    if args == ["unlink"]:
        return cmd_unlink(home)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
