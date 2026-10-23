"""scm_manifest.py - which repository files the installers put in place, and the link map.

Shared by the installers (setup/linux, setup/windows) and tools/repo_link.py (repo -> home
symlinks on Linux). Paths in the link map are stored as "~/..." so the map works for any user
name. The rules for collecting a working setup into a private repository are kept separately
(they are not part of public releases).
"""
import hashlib, json, os

# What the installers put in place on a machine: only what connects Star Citizen to SimHub and
# runs the bridge and the SC Haptics plugin. Everything else in the repo is kept for the record
# and linked only if you run tools/repo_link.py yourself.
# motion_desc.bin is the capture server's schema (tools/motion_schema.py); it sits next to it.
# lm_simhub4(t).py is the bridge the launcher starts (it imports lm_simhub3.py and sc_fx.py);
# lm_simhub3t.py stays for SC_BRIDGE_SCRIPT=~/lm_simhub3t.py (motion fields only).
RUNTIME = ("sc_start.py", "SC_Motion_Server.py", "motion_desc.bin", "lm_simhub3.py", "lm_simhub3t.py",
           "lm_simhub4.py", "lm_simhub4t.py", "sc_fx.py",
           "sc_lmcert.py", "sc_certswap.py", "sc_haptics_install.py", "sc_motion_clean.py")
# Linux only (the Windows installer does not copy these)
LINUX_ONLY = ("sc_motion_clean.py",)
INSTALL_PLUGIN = ("plugin/effects.default.json", "config/effects.json")
SIMDEF_UUID = "2f10c0be-479b-4b35-8671-a1b16ce4b874"
LINKMAP = "tools/linkmap.json"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def tilde(path, home):
    rel = os.path.relpath(path, home)
    if rel.startswith(".."):
        return path
    return "~/" + rel.replace(os.sep, "/")


def untilde(p, home):
    return os.path.join(home, p[2:]) if p.startswith("~/") else p


def is_install_item(repo_path):
    """True for the link-map entries an installer sets up (bridge, capture server, launcher,
    certificate tools, plugin source and its config)."""
    rp = repo_path.replace("\\", "/")
    if rp.startswith("scripts/"):
        return rp[len("scripts/"):] in RUNTIME
    return rp.startswith("plugin/src/") or rp in INSTALL_PLUGIN


def load_linkmap(repo):
    p = os.path.join(repo, LINKMAP)
    if not os.path.isfile(p):
        return {}
    with open(p) as f:
        return json.load(f).get("links", {})


def save_linkmap(repo, links):
    p = os.path.join(repo, LINKMAP)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump({"comment": "repo path -> home path; used by tools/repo_link.py",
                   "links": dict(sorted(links.items()))}, f, indent=1)
        f.write("\n")
