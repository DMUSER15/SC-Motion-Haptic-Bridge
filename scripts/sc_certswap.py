#!/usr/bin/env python3
"""sc_certswap.py - switch the D-BOX LiveMotion trust-certificate copies in the
Star Citizen Wine prefix between the D-BOX original and the user's ~/lm.crt.

  python3 ~/sc_certswap.py status     read-only report (changes nothing)
  python3 ~/sc_certswap.py original   put the D-BOX original into every copy
  python3 ~/sc_certswap.py custom     put ~/lm.crt into every copy (working state)

What it touches: only files named livemotion.crt below
  drive_c/ProgramData/D-BOX  and  drive_c/Program Files/D-BOX
(found by walking those folders, so a new copy is also seen).

Safety rules:
- refuses to write while a StarCitizen.exe process exists (reads /proc cmdline only);
- refuses to write if any copy is neither the D-BOX original nor ~/lm.crt;
- refuses if ~/livemotion.crt.orig is not the known D-BOX original (sha256);
- copies every file to ~/sc_certswap_backup/<time>/ (+ manifest.json) first;
- writes via temp file + rename in the same folder; verifies sha256 after;
- never reads, writes or prints any private key.
Exit codes: 0 ok, 1 usage, 2 game running, 3 source file wrong/missing,
4 no copies found, 5 unknown content found, 6 verify failed.

The game prefix's drive_c: env SC_CERT_DRIVE_C, else the prefix the Linux installer saved in
~/.config/sc-motion/config.json, else LUG-Helper's default ~/Games/star-citizen.

Test overrides (stand-ins): SC_CERT_DRIVE_C, SC_CERT_ORIG, SC_CERT_CUSTOM,
SC_CERT_ORIG_SHA, SC_CERT_BACKUP, SC_CERT_GAME_PATTERN.
"""
import hashlib, json, os, shutil, subprocess, sys, time

VERSION = "1.3"
H = os.path.expanduser("~")


def _saved_game_prefix():
    """The Star Citizen prefix saved by the Linux installer, or None."""
    try:
        with open(os.path.join(H, ".config", "sc-motion", "config.json")) as f:
            return json.load(f).get("game_prefix") or None
    except (OSError, ValueError, AttributeError):
        return None


DRIVE_C = os.environ.get("SC_CERT_DRIVE_C") or os.path.join(
    _saved_game_prefix() or os.path.join(H, "Games", "star-citizen"), "drive_c")
PREFIX = os.path.dirname(DRIVE_C.rstrip("/"))
ORIG = os.environ.get("SC_CERT_ORIG", H + "/livemotion.crt.orig")
CUSTOM = os.environ.get("SC_CERT_CUSTOM", H + "/lm.crt")
ORIG_SHA = os.environ.get("SC_CERT_ORIG_SHA",
    "6f5b4b7ad89b482a298e5947bdf0cb87480930e8ecf3275aa9c27117178d97ad")
BACKUP = os.environ.get("SC_CERT_BACKUP", H + "/sc_certswap_backup")
GAME_PATTERN = os.environ.get("SC_CERT_GAME_PATTERN", "starcitizen.exe").lower()
ROOTS = ["ProgramData/D-BOX", "Program Files/D-BOX"]


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(65536), b""):
            h.update(b)
    return h.hexdigest()


def find_copies():
    out = []
    for r in ROOTS:
        base = os.path.join(DRIVE_C, r)
        if not os.path.isdir(base):
            continue
        for d, _, files in os.walk(base):
            for n in files:
                if n.lower() == "livemotion.crt":
                    out.append(os.path.join(d, n))
    return sorted(out)


def procs_with_arg(name):
    """pids whose argv contains an argument whose file name (after / or \\) is name.
    Wine shows the game as C:\\...\\StarCitizen.exe; a shell line that merely
    mentions the word does not count."""
    name = name.lower()
    pids = []
    for p in os.listdir("/proc"):
        if not p.isdigit() or int(p) == os.getpid():
            continue
        try:
            with open("/proc/%s/cmdline" % p, "rb") as f:
                args = f.read().split(b"\0")
        except OSError:
            continue
        for a in args:
            base = a.decode("utf-8", "replace").replace("\\", "/").rsplit("/", 1)[-1]
            if base.strip().lower() == name:
                pids.append(p)
                break
    return pids


def game_pids():
    return procs_with_arg(GAME_PATTERN)


# the capture server; its old name is still recognised so an old process is never missed
SERVER_NAMES = ("SC_Motion_Server.py", "lm_server2.py")


def server_running():
    return any(procs_with_arg(n) for n in SERVER_NAMES)


def enddate(path):
    try:
        r = subprocess.run(["openssl", "x509", "-noout", "-enddate", "-in", path],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or r.stderr.strip()
    except Exception as e:  # openssl missing etc.
        return "enddate unknown (%s)" % e


def classify(h, custom_sha):
    if h == ORIG_SHA:
        return "D-BOX original"
    if custom_sha and h == custom_sha:
        return "custom (~/lm.crt)"
    return "OTHER"


def rel(p):
    return os.path.relpath(p, DRIVE_C)


def z_drive():
    z = os.path.join(PREFIX, "dosdevices", "z:")
    try:
        return os.readlink(z)
    except OSError:
        return None


def status():
    print("sc_certswap %s status (read-only)" % VERSION)
    custom_sha = sha(CUSTOM) if os.path.isfile(CUSTOM) else None
    orig_ok = os.path.isfile(ORIG) and sha(ORIG) == ORIG_SHA
    print("  D-BOX original source %s: %s" % (ORIG, "OK" if orig_ok else "MISSING or WRONG sha"))
    if custom_sha:
        print("  custom source %s: sha %s..., %s" % (CUSTOM, custom_sha[:12], enddate(CUSTOM)))
    else:
        print("  custom source %s: MISSING" % CUSTOM)
    copies = find_copies()
    print("  copies found: %d" % len(copies))
    states = []
    for p in copies:
        s = classify(sha(p), custom_sha)
        states.append(s)
        print("    [%s] %s" % (s, rel(p)))
    z = z_drive()
    print("  game prefix z: -> %s" % (z if z else "NOT MAPPED"))
    if z and os.path.realpath(z) == "/":
        print("  env value for the test: Z:" + os.path.abspath(CUSTOM).replace("/", "\\"))
    g = game_pids()
    print("  StarCitizen.exe running: %s" % ("YES pids " + ",".join(g) if g else "no"))
    print("  capture server (SC_Motion_Server.py) running: %s" % ("yes" if server_running() else "no"))
    if states and all(s == states[0] for s in states) and states[0] != "OTHER":
        print("RESULT: all %d copies = %s" % (len(states), states[0]))
    else:
        print("RESULT: MIXED or UNKNOWN state")
    return 0


def switch(target):
    g = game_pids()
    if g:
        print("REFUSED: StarCitizen.exe is running (pids %s). Quit the game first." % ",".join(g))
        return 2
    if not os.path.isfile(ORIG) or sha(ORIG) != ORIG_SHA:
        print("REFUSED: %s missing or not the known D-BOX original." % ORIG)
        return 3
    if not os.path.isfile(CUSTOM):
        print("REFUSED: %s missing." % CUSTOM)
        return 3
    custom_sha = sha(CUSTOM)
    src = ORIG if target == "original" else CUSTOM
    want = ORIG_SHA if target == "original" else custom_sha
    copies = find_copies()
    if not copies:
        print("REFUSED: no livemotion.crt copies found below %s" % DRIVE_C)
        return 4
    unknown = [p for p in copies if classify(sha(p), custom_sha) == "OTHER"]
    if unknown:
        print("REFUSED: these copies are neither the D-BOX original nor ~/lm.crt:")
        for p in unknown:
            print("    " + rel(p))
        return 5
    stamp = time.strftime("%Y%m%d_%H%M%S")
    bdir = os.path.join(BACKUP, stamp)
    n = 1
    while os.path.exists(bdir):
        n += 1
        bdir = os.path.join(BACKUP, "%s_%d" % (stamp, n))
    os.makedirs(bdir)
    manifest = []
    for i, p in enumerate(copies):
        dst = os.path.join(bdir, "%d_livemotion.crt" % i)
        shutil.copy2(p, dst)
        manifest.append({"index": i, "path": p, "sha256": sha(p)})
    with open(os.path.join(bdir, "manifest.json"), "w") as f:
        json.dump({"tool": "sc_certswap", "version": VERSION, "target": target,
                   "copies": manifest}, f, indent=1)
    print("backup: %s (%d files + manifest.json)" % (bdir, len(copies)))
    bad = 0
    for p in copies:
        if sha(p) == want:
            print("  unchanged  %s" % rel(p))
            continue
        tmp = os.path.join(os.path.dirname(p), ".livemotion.crt.sc_tmp")
        shutil.copyfile(src, tmp)
        try:
            shutil.copymode(p, tmp)
        except OSError:
            pass
        os.replace(tmp, p)
        ok = sha(p) == want
        bad += 0 if ok else 1
        print("  %s %s" % ("written   " if ok else "VERIFY FAIL", rel(p)))
    if bad:
        print("RESULT: %d copies failed verification; backup in %s" % (bad, bdir))
        return 6
    label = "D-BOX original" if target == "original" else "custom (~/lm.crt)"
    print("RESULT: all %d copies = %s" % (len(copies), label))
    return 0


def main(argv):
    if len(argv) != 2 or argv[1] not in ("status", "original", "custom"):
        print(__doc__)
        return 1
    if argv[1] == "status":
        return status()
    return switch(argv[1])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
