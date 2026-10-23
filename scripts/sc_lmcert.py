#!/usr/bin/env python3
"""sc_lmcert.py - long-lived certificate for SC_Motion_Server.py and the minimal
footprint in the Star Citizen prefix. Companion of sc_certswap.py (imported
unchanged; must sit next to this file in ~).

  python3 ~/sc_lmcert.py status             read-only report
  python3 ~/sc_lmcert.py check              read-only pre-flight (exit 0 PASS, 9 WARN, 10 FAIL)
  python3 ~/sc_lmcert.py new [copy-subject] stage a new key + cert in ~/lm_certs/new_<time>/
  python3 ~/sc_lmcert.py activate           use the newest staged pair (backs up the current one)
  python3 ~/sc_lmcert.py rollback           undo the last activate
  python3 ~/sc_lmcert.py selftest           TLS check of the running SC_Motion_Server.py against
                                            the certificate the game trusts
  python3 ~/sc_lmcert.py footprint minimal  custom cert only in the StarCitizen folder copy,
                                            D-BOX original in every other copy (no longer
                                            enough since 2026-10-21, see below)
  python3 ~/sc_lmcert.py footprint all      custom cert in every copy (the working layout)
  python3 ~/sc_lmcert.py repair             = footprint all: puts ~/lm.crt back into every copy
                                            after a game or D-BOX update replaced one (backups)

new: 3650 days, same key type/size as the current ~/lm.crt, subject CN=LiveMotionComm,
     SAN DNS:LiveMotionComm, basicConstraints critical CA:TRUE (same extension shape as
     the working certificate). "copy-subject" keeps the current certificate's subject.
activate: refuses while StarCitizen.exe or SC_Motion_Server.py runs; checks the staged pair
     (key matches cert, name, self-signature, >= 365 days); copies the current pair to
     ~/lm_certs/prev_<time>/ (key mode 600) with a manifest of the prefix copies; puts the
     new cert into every prefix copy that holds the current custom cert; then replaces
     ~/lm.crt and ~/lm.key. D-BOX original copies stay as they are.
rollback: newest ~/lm_certs/prev_*: reverses the above; that folder is renamed done_*.
selftest: needs SC_Motion_Server.py listening on 127.0.0.1:61666. Read-only, no game needed.
check: ~/lm.crt valid >= 30 days (else WARN), key matches, and every copy the connector may
     read == ~/lm.crt: LiveMotion/StarCitizen and LiveMotion/LiveMotionConnector. Until
     2026-10-18 the connector read the StarCitizen copy; on 2026-10-21 it read the
     LiveMotionConnector copy (Observed, probably after a game patch), so both are checked.
Private keys: generated and compared on this machine only; never printed or copied
anywhere except ~/lm.key and ~/lm_certs/*/lm.key (mode 600).
Exit codes: 0 ok, 1 usage, 2 game running, 3 bad/missing input, 4 no copies,
5 unknown copy content, 6 verify failed, 7 capture server running, 8 selftest FAIL,
9 check WARN, 10 check FAIL.
Test overrides: SC_LMCERT_HOME (folder holding lm.crt/lm.key/lm_certs), SC_LMCERT_PORT,
plus the SC_CERT_* overrides of sc_certswap.py.
"""
import json, os, shutil, socket, ssl, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sc_certswap as cs  # noqa: E402  (unchanged companion)

VERSION = "1.2"
HOME = os.environ.get("SC_LMCERT_HOME", os.path.expanduser("~"))
CRT = os.path.join(HOME, "lm.crt")
KEY = os.path.join(HOME, "lm.key")
CERTS = os.path.join(HOME, "lm_certs")
NAME = "LiveMotionComm"
PORT = int(os.environ.get("SC_LMCERT_PORT", "61666"))
READ_REL = "ProgramData/D-BOX/Gaming/LiveMotion/StarCitizen/livemotion.crt"
CONNECTOR_REL = "ProgramData/D-BOX/Gaming/LiveMotion/LiveMotionConnector/livemotion.crt"
READ_RELS = (READ_REL, CONNECTOR_REL)  # copies the connector may read (see check above)
DAYS = 3650
cs.CUSTOM = CRT  # keep both scripts on the same custom certificate


def ossl(*args, inp=None):
    r = subprocess.run(["openssl"] + list(args), capture_output=True, text=True,
                       input=inp, timeout=60)
    return r.returncode, r.stdout, r.stderr


def read_path():
    return os.path.join(cs.DRIVE_C, READ_REL)


def read_paths():
    return [os.path.join(cs.DRIVE_C, r) for r in READ_RELS]


def cert_info(path):
    """subject, enddate, days left, key spec, has SAN name, is CA."""
    info = {"path": path}
    rc, out, err = ossl("x509", "-in", path, "-noout", "-subject", "-enddate",
                        "-nameopt", "RFC2253")
    if rc:
        info["error"] = err.strip()
        return info
    for line in out.splitlines():
        if line.startswith("subject="):
            info["subject"] = line[8:].strip()
        if line.startswith("notAfter="):
            info["notAfter"] = line[9:].strip()
    t = ssl.cert_time_to_seconds(info["notAfter"])
    info["days"] = int((t - time.time()) // 86400)
    rc, txt, _ = ossl("x509", "-in", path, "-noout", "-text")
    if "rsaEncryption" in txt:
        bits = ""
        for line in txt.splitlines():
            if "Public-Key: (" in line:
                bits = line.split("(")[1].split()[0]
        info["keyspec"] = ("rsa", bits or "2048")
    elif "id-ecPublicKey" in txt:
        curve = "prime256v1"
        for line in txt.splitlines():
            if "ASN1 OID:" in line:
                curve = line.split(":", 1)[1].strip()
        info["keyspec"] = ("ec", curve)
    else:
        info["keyspec"] = None
    _, ch, _ = ossl("x509", "-in", path, "-noout", "-checkhost", NAME)
    info["name_ok"] = "does match" in ch and "NOT" not in ch
    info["ca"] = "CA:TRUE" in txt
    return info


def key_matches(crt, key):
    rc1, a, _ = ossl("x509", "-in", crt, "-noout", "-pubkey")
    rc2, b, _ = ossl("pkey", "-in", key, "-pubout")
    return rc1 == 0 and rc2 == 0 and a.strip() == b.strip() and a.strip() != ""


def self_verify(crt):
    rc, out, _ = ossl("verify", "-CAfile", crt, crt)
    return rc == 0 and out.strip().endswith("OK")


def newest(prefix):
    if not os.path.isdir(CERTS):
        return None
    ds = sorted(d for d in os.listdir(CERTS) if d.startswith(prefix)
                and os.path.isdir(os.path.join(CERTS, d)))
    return os.path.join(CERTS, ds[-1]) if ds else None


def unique_dir(base):
    d, n = base, 1
    while os.path.exists(d):
        n += 1
        d = "%s_%d" % (base, n)
    os.makedirs(d)
    return d


def put(src, dst, mode=None):
    """atomic copy src -> dst in dst's folder; returns True if sha matches after."""
    tmp = os.path.join(os.path.dirname(dst), "." + os.path.basename(dst) + ".sc_tmp")
    shutil.copyfile(src, tmp)
    if mode is not None:
        os.chmod(tmp, mode)
    else:
        try:
            shutil.copymode(dst, tmp)
        except OSError:
            pass
    os.replace(tmp, dst)
    return cs.sha(dst) == cs.sha(src)


def busy():
    g = cs.game_pids()
    if g:
        print("REFUSED: StarCitizen.exe is running (pids %s). Quit the game first." % ",".join(g))
        return 2
    if cs.server_running():
        print("REFUSED: the capture server (SC_Motion_Server.py) is running. Stop it first (python3 ~/sc_start.py stop, or Ctrl+C).")
        return 7
    return 0


def prefix_states(custom_sha):
    copies = cs.find_copies()
    return [(p, cs.classify(cs.sha(p), custom_sha)) for p in copies]


def backup_prefix(tag):
    copies = cs.find_copies()
    bdir = unique_dir(os.path.join(cs.BACKUP, time.strftime("%Y%m%d_%H%M%S") + "_" + tag))
    man = []
    for i, p in enumerate(copies):
        shutil.copy2(p, os.path.join(bdir, "%d_livemotion.crt" % i))
        man.append({"index": i, "path": p, "sha256": cs.sha(p)})
    with open(os.path.join(bdir, "manifest.json"), "w") as f:
        json.dump({"tool": "sc_lmcert", "version": VERSION, "action": tag, "copies": man}, f, indent=1)
    return bdir


# ---------------------------------------------------------------- commands
def cmd_status():
    print("sc_lmcert %s status (read-only)" % VERSION)
    if not os.path.isfile(CRT):
        print("  %s: MISSING" % CRT)
    else:
        i = cert_info(CRT)
        if "error" in i:
            print("  %s: unreadable (%s)" % (CRT, i["error"]))
        else:
            print("  ~/lm.crt  subject: %s" % i["subject"])
            print("            notAfter: %s (%d days left)" % (i["notAfter"], i["days"]))
            print("            key: %s, name %s: %s, CA: %s" % (
                "/".join(i["keyspec"]) if i["keyspec"] else "unknown", NAME,
                "yes" if i["name_ok"] else "NO", "yes" if i["ca"] else "no"))
            print("            ~/lm.key matches: %s" % ("yes" if os.path.isfile(KEY) and key_matches(CRT, KEY) else "NO"))
    custom_sha = cs.sha(CRT) if os.path.isfile(CRT) else None
    for p, s in prefix_states(custom_sha):
        mark = "  <- may be read by the connector" if os.path.abspath(p) in [os.path.abspath(x) for x in read_paths()] else ""
        print("    [%s] %s%s" % (s, cs.rel(p), mark))
    st = newest("new_")
    pv = newest("prev_")
    print("  staged (newest): %s" % (st or "none"))
    print("  rollback point (newest): %s" % (pv or "none"))
    print("  SC_Motion_Server.py running: %s; StarCitizen.exe running: %s" % (
        "yes" if cs.server_running() else "no", "yes" if cs.game_pids() else "no"))
    return 0


def cmd_check():
    problems, warns = [], []
    if not os.path.isfile(CRT) or not os.path.isfile(KEY):
        problems.append("~/lm.crt or ~/lm.key missing")
    else:
        i = cert_info(CRT)
        if "error" in i:
            problems.append("~/lm.crt unreadable")
        else:
            if i["days"] < 0:
                problems.append("~/lm.crt EXPIRED (%s)" % i["notAfter"])
            elif i["days"] < 30:
                warns.append("~/lm.crt expires in %d days (%s)" % (i["days"], i["notAfter"]))
            if not i["name_ok"]:
                problems.append("~/lm.crt has no %s name" % NAME)
        if not key_matches(CRT, KEY):
            problems.append("~/lm.key does not match ~/lm.crt")
    for rp in read_paths():
        if not os.path.isfile(rp):
            problems.append("copy missing: " + cs.rel(rp))
        elif os.path.isfile(CRT) and cs.sha(rp) != cs.sha(CRT):
            what = "D-BOX original" if cs.sha(rp) == cs.ORIG_SHA else "unknown content"
            problems.append("copy is %s, not ~/lm.crt (game/D-BOX update?): %s" % (what, cs.rel(rp)))
    if problems:
        print("LMCERT FAIL: " + "; ".join(problems + warns))
        return 10
    if warns:
        print("LMCERT WARN: " + "; ".join(warns))
        return 9
    i = cert_info(CRT)
    print("LMCERT PASS: connector copies = ~/lm.crt, key matches, %d days left" % i["days"])
    return 0


def cmd_new(copy_subject):
    if not os.path.isfile(CRT):
        print("REFUSED: %s missing (needed to match key type%s)." % (
            CRT, " and subject" if copy_subject else ""))
        return 3
    i = cert_info(CRT)
    if "error" in i or not i.get("keyspec"):
        print("REFUSED: cannot read key type of %s" % CRT)
        return 3
    kind, val = i["keyspec"]
    newkey = ["-newkey", "rsa:" + val] if kind == "rsa" else \
             ["-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:" + val]
    if copy_subject:
        parts, cur, esc = [], "", False
        for ch in i["subject"]:  # RFC2253, most specific first
            if esc:
                cur += ch
                esc = False
            elif ch == "\\":
                cur += ch
                esc = True
            elif ch == ",":
                parts.append(cur)
                cur = ""
            else:
                cur += ch
        parts.append(cur)
        subj = "/" + "/".join(reversed([p.strip() for p in parts if p.strip()]))
    else:
        subj = "/CN=" + NAME
    os.makedirs(CERTS, exist_ok=True)
    d = unique_dir(os.path.join(CERTS, "new_" + time.strftime("%Y%m%d_%H%M%S")))
    os.chmod(d, 0o700)
    k, c = os.path.join(d, "lm.key"), os.path.join(d, "lm.crt")
    old = os.umask(0o077)
    try:
        rc, out, err = ossl("req", "-x509", *newkey, "-nodes", "-sha256",
                            "-days", str(DAYS), "-subj", subj,
                            "-addext", "basicConstraints=critical,CA:TRUE",
                            "-addext", "subjectAltName=DNS:" + NAME,
                            "-keyout", k, "-out", c)
    finally:
        os.umask(old)
    if rc:
        print("FAILED: openssl req: %s" % err.strip()[-300:])
        return 6
    os.chmod(k, 0o600)
    j = cert_info(c)
    ok = key_matches(c, k) and self_verify(c) and j.get("name_ok") and j.get("ca") and j.get("days", 0) >= 365
    print("staged: %s" % d)
    print("  subject: %s" % j.get("subject"))
    print("  notAfter: %s (%s days)" % (j.get("notAfter"), j.get("days")))
    print("  key %s, name %s: %s, CA: %s, self-verify: %s" % (
        "/".join(j["keyspec"]) if j.get("keyspec") else "?", NAME,
        "yes" if j.get("name_ok") else "NO", "yes" if j.get("ca") else "NO",
        "OK" if self_verify(c) else "FAIL"))
    print("RESULT: %s" % ("staged pair OK; next: activate" if ok else "staged pair FAILED checks; do not activate"))
    return 0 if ok else 6


def cmd_activate():
    b = busy()
    if b:
        return b
    d = newest("new_")
    if not d:
        print("REFUSED: nothing staged; run: python3 ~/sc_lmcert.py new")
        return 3
    nk, nc = os.path.join(d, "lm.key"), os.path.join(d, "lm.crt")
    j = cert_info(nc) if os.path.isfile(nc) else {}
    if not (os.path.isfile(nk) and key_matches(nc, nk) and self_verify(nc)
            and j.get("name_ok") and j.get("days", 0) >= 365):
        print("REFUSED: staged pair %s fails checks." % d)
        return 3
    if not (os.path.isfile(CRT) and os.path.isfile(KEY)):
        print("REFUSED: current ~/lm.crt / ~/lm.key missing (nothing to roll back to).")
        return 3
    if cs.sha(nc) == cs.sha(CRT):
        print("REFUSED: staged pair is already active.")
        return 3
    old_sha = cs.sha(CRT)
    states = prefix_states(old_sha)
    if not states:
        print("REFUSED: no livemotion.crt copies found.")
        return 4
    bad = [p for p, s in states if s == "OTHER"]
    if bad:
        print("REFUSED: copies neither D-BOX original nor ~/lm.crt:")
        for p in bad:
            print("    " + cs.rel(p))
        return 5
    targets = [p for p, s in states if s.startswith("custom")]
    pdir = unique_dir(os.path.join(CERTS, "prev_" + time.strftime("%Y%m%d_%H%M%S")))
    os.chmod(pdir, 0o700)
    shutil.copy2(CRT, os.path.join(pdir, "lm.crt"))
    shutil.copy2(KEY, os.path.join(pdir, "lm.key"))
    os.chmod(os.path.join(pdir, "lm.key"), 0o600)
    with open(os.path.join(pdir, "manifest.json"), "w") as f:
        json.dump({"tool": "sc_lmcert", "version": VERSION, "staged": d,
                   "prev_crt_sha256": old_sha, "new_crt_sha256": cs.sha(nc),
                   "prefix_copies_switched": targets}, f, indent=1)
    bdir = backup_prefix("activate")
    print("rollback point: %s" % pdir)
    print("prefix backup: %s" % bdir)
    fails = 0
    for p in targets:
        ok = put(nc, p)
        fails += 0 if ok else 1
        print("  %s %s" % ("written    " if ok else "VERIFY FAIL", cs.rel(p)))
    for p, s in states:
        if p not in targets:
            print("  unchanged   %s [%s]" % (cs.rel(p), s))
    okc = put(nc, CRT, 0o644)
    okk = put(nk, KEY, 0o600)
    if fails or not (okc and okk and key_matches(CRT, KEY)):
        print("RESULT: verification FAILED; run: python3 ~/sc_lmcert.py rollback")
        return 6
    print("RESULT: new pair active (%d prefix copies switched); notAfter %s" % (len(targets), j["notAfter"]))
    if not targets:
        print("NOTE: no prefix copy held the custom cert; run: python3 ~/sc_lmcert.py footprint all")
    return 0


def cmd_rollback():
    b = busy()
    if b:
        return b
    pdir = newest("prev_")
    if not pdir:
        print("REFUSED: no rollback point in %s" % CERTS)
        return 3
    pk, pc = os.path.join(pdir, "lm.key"), os.path.join(pdir, "lm.crt")
    if not (os.path.isfile(pc) and os.path.isfile(pk) and key_matches(pc, pk)):
        print("REFUSED: rollback point %s incomplete." % pdir)
        return 3
    cur_sha = cs.sha(CRT) if os.path.isfile(CRT) else None
    states = prefix_states(cur_sha)
    prev_sha = cs.sha(pc)
    bad = [p for p, s in states if s == "OTHER" and cs.sha(p) != prev_sha]
    if bad:
        print("REFUSED: copies with unknown content:")
        for p in bad:
            print("    " + cs.rel(p))
        return 5
    bdir = backup_prefix("rollback")
    print("prefix backup: %s" % bdir)
    fails = 0
    for p, s in states:
        if s.startswith("custom"):
            ok = put(pc, p)
            fails += 0 if ok else 1
            print("  %s %s" % ("restored   " if ok else "VERIFY FAIL", cs.rel(p)))
        else:
            print("  unchanged   %s" % cs.rel(p))
    okc = put(pc, CRT, 0o644)
    okk = put(pk, KEY, 0o600)
    if fails or not (okc and okk and key_matches(CRT, KEY)):
        print("RESULT: rollback verification FAILED; backups in %s and %s" % (pdir, bdir))
        return 6
    done = os.path.join(CERTS, "done_" + os.path.basename(pdir)[5:])
    os.rename(pdir, done)
    print("RESULT: previous pair restored (from %s)" % done)
    return 0


def cmd_selftest():
    rp = read_path()
    if not os.path.isfile(rp):
        print("SELFTEST FAIL: game copy missing: %s" % cs.rel(rp))
        return 8
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = True
    # gRPC/OpenSSL in the connector does not use X509_STRICT; Python 3.13 sets it by default.
    ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
    ctx.load_verify_locations(cafile=rp)
    ctx.set_alpn_protocols(["h2"])
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=5) as s:
            with ctx.wrap_socket(s, server_hostname=NAME) as t:
                peer = t.getpeercert()
                alpn = t.selected_alpn_protocol()
    except ssl.SSLCertVerificationError as e:
        print("SELFTEST FAIL: certificate verify failed (%s) - the game would reject this server" % e.verify_message)
        return 8
    except ConnectionRefusedError:
        print("SELFTEST FAIL: nothing listens on 127.0.0.1:%d - start python3 ~/SC_Motion_Server.py first" % PORT)
        return 8
    except (OSError, ssl.SSLError) as e:
        print("SELFTEST FAIL: %s" % e)
        return 8
    print("SELFTEST PASS: server on 127.0.0.1:%d validates against %s as %s (notAfter %s, ALPN %s)" % (
        PORT, cs.rel(rp), NAME, peer.get("notAfter"), alpn))
    return 0


def cmd_footprint(mode):
    g = cs.game_pids()
    if g:
        print("REFUSED: StarCitizen.exe is running (pids %s)." % ",".join(g))
        return 2
    if not os.path.isfile(cs.ORIG) or cs.sha(cs.ORIG) != cs.ORIG_SHA:
        print("REFUSED: %s missing or not the known D-BOX original." % cs.ORIG)
        return 3
    if not os.path.isfile(CRT):
        print("REFUSED: %s missing." % CRT)
        return 3
    csha = cs.sha(CRT)
    states = prefix_states(csha)
    if not states:
        print("REFUSED: no livemotion.crt copies found.")
        return 4
    rp = os.path.abspath(read_path())
    if rp not in [os.path.abspath(p) for p, _ in states]:
        print("REFUSED: the game copy %s does not exist." % READ_REL)
        return 4
    bad = [p for p, s in states if s == "OTHER"]
    if bad:
        print("REFUSED: copies neither D-BOX original nor ~/lm.crt:")
        for p in bad:
            print("    " + cs.rel(p))
        return 5
    bdir = backup_prefix("footprint_" + mode)
    print("prefix backup: %s" % bdir)
    fails, n_custom = 0, 0
    for p, s in states:
        want_custom = (mode == "all") or os.path.abspath(p) == rp
        src, want = (CRT, csha) if want_custom else (cs.ORIG, cs.ORIG_SHA)
        n_custom += 1 if want_custom else 0
        label = "custom" if want_custom else "D-BOX original"
        if cs.sha(p) == want:
            print("  unchanged   %-15s %s" % (label, cs.rel(p)))
            continue
        ok = put(src, p)
        fails += 0 if ok else 1
        print("  %s %-15s %s" % ("written    " if ok else "VERIFY FAIL", label, cs.rel(p)))
    if fails:
        print("RESULT: %d copies failed verification; backup in %s" % (fails, bdir))
        return 6
    print("RESULT: footprint %s - custom in %d of %d copies" % (mode, n_custom, len(states)))
    return 0


def main(a):
    if len(a) >= 2 and a[1] == "status" and len(a) == 2:
        return cmd_status()
    if len(a) == 2 and a[1] == "check":
        return cmd_check()
    if a[1:2] == ["new"] and (len(a) == 2 or (len(a) == 3 and a[2] == "copy-subject")):
        return cmd_new(len(a) == 3)
    if len(a) == 2 and a[1] == "activate":
        return cmd_activate()
    if len(a) == 2 and a[1] == "rollback":
        return cmd_rollback()
    if len(a) == 2 and a[1] == "selftest":
        return cmd_selftest()
    if len(a) == 2 and a[1] == "repair":
        return cmd_footprint("all")
    if len(a) == 3 and a[1] == "footprint" and a[2] in ("minimal", "all"):
        return cmd_footprint(a[2])
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
