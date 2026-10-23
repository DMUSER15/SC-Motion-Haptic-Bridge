"""scm_common.py - shared helpers for the sc-motion-bridge installers and verifiers
(Linux and Windows). Functions are read-only unless their name or docstring says they write.

Facts used here come from the project notes (continuation v10, PART C2/D):
  - the game loads D-BOX's connector from ProgramData\\D-BOX\\Gaming\\LiveMotion\\StarCitizen\\
    (copied from the LiveMotionConnector folder the D-BOX installer creates);
  - the connector connects to 127.0.0.1:61666, expects the name LiveMotionComm and trusts one
    livemotion.crt: the StarCitizen folder copy until 2026-10-18, the LiveMotionConnector copy
    on 2026-10-21 (Observed), so the setup puts the custom certificate in every copy;
  - the D-BOX original certificate has sha256 6f5b4b7a...97ad (HaptiSync Center 1.4.0).
"""
import datetime, hashlib, json, os, re, shutil, socket, subprocess, sys, tempfile

DBOX_ORIG_SHA = os.environ.get(
    "SC_CERT_ORIG_SHA", "6f5b4b7ad89b482a298e5947bdf0cb87480930e8ecf3275aa9c27117178d97ad")
CERT_NAME = "LiveMotionComm"
CERT_DAYS = 3650
SIMDEF_UUID = "2f10c0be-479b-4b35-8671-a1b16ce4b874"
PORT_CAPTURE_TCP = 61666
PORT_TEE_UDP = 61700
PORT_PLUGIN_UDP = 61701
PORT_BRIDGE_UDP = 61702
PORT_SIMHUB_UDP = 30777
LM_PARTS = ("ProgramData", "D-BOX", "Gaming", "LiveMotion")
DBOX_ROOTS = (("ProgramData", "D-BOX"), ("Program Files", "D-BOX"))
SERVER_SCRIPT = "SC_Motion_Server.py"     # the capture server (lm_server2.py before kit 1.4)
SERVER_NAMES = (SERVER_SCRIPT, "lm_server2.py")
DESC_FILE = "motion_desc.bin"             # its schema, written by hand (tools/motion_schema.py)
DESC_NEEDLES = (b"dbox.proto.v1", b"IdentificationRequest", b"IdentificationResponse",
                b"LiveMotionRequest", b"LiveMotionResponse")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def lm_root(drive_c):
    return os.path.join(drive_c, *LM_PARTS)


def connector_dir(drive_c):
    return os.path.join(lm_root(drive_c), "LiveMotionConnector")


def game_dir(drive_c):
    return os.path.join(lm_root(drive_c), "StarCitizen")


def dbox_cert_copies(drive_c):
    """Every livemotion.crt below ProgramData\\D-BOX and Program Files\\D-BOX (same rule as
    sc_certswap.py)."""
    out = []
    for parts in DBOX_ROOTS:
        base = os.path.join(drive_c, *parts)
        if not os.path.isdir(base):
            continue
        for d, _, files in os.walk(base):
            for n in files:
                if n.lower() == "livemotion.crt":
                    out.append(os.path.join(d, n))
    return sorted(out)


def read_text_any(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-16"):
        try:
            t = raw.decode(enc)
            if "\x00" not in t:
                return t
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def connector_config_check(folder):
    """(ok, detail) for LiveMotionConnector.config in folder: expects 127.0.0.1, 61666 and
    LiveMotionComm. The file format is not documented; the check only looks for the values
    next to their names."""
    p = os.path.join(folder, "LiveMotionConnector.config")
    if not os.path.isfile(p):
        return False, "LiveMotionConnector.config missing"
    t = read_text_any(p)
    want = {"ReceiverIpAddress": "127.0.0.1", "ReceiverPort": str(PORT_CAPTURE_TCP),
            "CertificateCommonName": CERT_NAME}
    bad = []
    for k, v in want.items():
        m = re.search(re.escape(k) + r"[^\n]{0,40}?" + re.escape(v), t)
        if not m:
            bad.append("%s=%s" % (k, v))
    if bad:
        return False, "not found in config: " + ", ".join(bad)
    return True, "ReceiverIpAddress 127.0.0.1, ReceiverPort 61666, CertificateCommonName LiveMotionComm"


def find_openssl():
    p = shutil.which("openssl")
    if p:
        return p
    if os.name == "nt":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        for c in (os.path.join(pf, "OpenSSL-Win64", "bin", "openssl.exe"),
                  os.path.join(pf, "Git", "usr", "bin", "openssl.exe"),
                  os.path.join(pf, "Git", "mingw64", "bin", "openssl.exe")):
            if os.path.isfile(c):
                return c
    return None


def ossl(openssl, *args, timeout=120):
    r = subprocess.run([openssl] + list(args), capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def pair_ok(openssl, crt, key):
    """(ok, detail): key matches certificate, certificate carries LiveMotionComm, CA:TRUE,
    verifies against itself, days left."""
    rc1, a, _ = ossl(openssl, "x509", "-in", crt, "-noout", "-pubkey")
    rc2, b, _ = ossl(openssl, "pkey", "-in", key, "-pubout")
    if rc1 or rc2 or not a.strip() or a.strip() != b.strip():
        return False, "key does not match certificate"
    _, ch, _ = ossl(openssl, "x509", "-in", crt, "-noout", "-checkhost", CERT_NAME)
    if "does match" not in ch or "NOT" in ch:
        return False, "certificate has no %s name" % CERT_NAME
    _, txt, _ = ossl(openssl, "x509", "-in", crt, "-noout", "-text")
    if "CA:TRUE" not in txt:
        return False, "certificate is not CA:TRUE"
    rc, out, _ = ossl(openssl, "verify", "-CAfile", crt, crt)
    if rc or not out.strip().endswith("OK"):
        return False, "certificate does not verify against itself"
    rc, out, _ = ossl(openssl, "x509", "-in", crt, "-noout", "-checkend", str(365 * 86400))
    return True, "pair OK" + ("" if rc == 0 else " (expires within a year)")


def bootstrap_pair(openssl, crt, key):
    """WRITES crt and key: a first capture-server pair with the same shape as the one in use
    (RSA 4096, CN and SAN LiveMotionComm, basicConstraints critical CA:TRUE, 10 years).
    Refuses if either file exists. Later renewals: sc_lmcert.py new + activate."""
    if os.path.exists(crt) or os.path.exists(key):
        return False, "refused: %s or %s already exists" % (crt, key)
    cnf = ("[req]\nprompt = no\ndistinguished_name = dn\nx509_extensions = v3\n"
           "[dn]\nCN = %s\n[v3]\nbasicConstraints = critical,CA:TRUE\n"
           "subjectAltName = DNS:%s\nsubjectKeyIdentifier = hash\n"
           "authorityKeyIdentifier = keyid:always\n" % (CERT_NAME, CERT_NAME))
    os.makedirs(os.path.dirname(os.path.abspath(crt)), exist_ok=True)
    fd, cpath = tempfile.mkstemp(suffix=".cnf")
    with os.fdopen(fd, "w") as f:
        f.write(cnf)
    old = os.umask(0o077) if hasattr(os, "umask") else None
    try:
        rc, out, err = ossl(openssl, "req", "-x509", "-newkey", "rsa:4096", "-nodes", "-sha256",
                            "-days", str(CERT_DAYS), "-config", cpath,
                            "-keyout", key, "-out", crt, timeout=300)
    finally:
        if old is not None:
            os.umask(old)
        os.remove(cpath)
    if rc:
        for p in (crt, key):
            if os.path.exists(p):
                os.remove(p)
        return False, "openssl req failed: " + err.strip()[-300:]
    try:
        os.chmod(key, 0o600)
        os.chmod(crt, 0o644)
    except OSError:
        pass
    ok, detail = pair_ok(openssl, crt, key)
    return ok, ("new pair written: %s, %s; %s" % (crt, key, detail))


def backup_dbox_original(drive_c, dest):
    """WRITES dest (if missing): a copy of D-BOX's original livemotion.crt, taken from any copy
    in the prefix whose sha256 is the known original. (ok, detail)."""
    if os.path.isfile(dest):
        if sha256(dest) == DBOX_ORIG_SHA:
            return True, "already present"
        return False, "%s exists but is not the known D-BOX original" % dest
    for p in dbox_cert_copies(drive_c):
        if sha256(p) == DBOX_ORIG_SHA:
            shutil.copyfile(p, dest)
            return sha256(dest) == DBOX_ORIG_SHA, "copied from %s" % os.path.relpath(p, drive_c)
    return False, "no copy in the prefix matches the known D-BOX original sha256"


def original_available(drive_c, dest):
    if os.path.isfile(dest) and sha256(dest) == DBOX_ORIG_SHA:
        return True
    return any(sha256(p) == DBOX_ORIG_SHA for p in dbox_cert_copies(drive_c))


def game_folder_missing(drive_c):
    """Files of the connector folder that are missing in the StarCitizen folder."""
    src, dst = connector_dir(drive_c), game_dir(drive_c)
    if not os.path.isdir(src):
        return None
    out = []
    for d, _, files in os.walk(src):
        for n in files:
            rel = os.path.relpath(os.path.join(d, n), src)
            if not os.path.exists(os.path.join(dst, rel)):
                out.append(rel)
    return sorted(out)


def ensure_game_folder(drive_c):
    """WRITES: copies files missing in ProgramData\\D-BOX\\Gaming\\LiveMotion\\StarCitizen from
    the LiveMotionConnector folder (the fix found 2026-10-15; the HaptiSync Center UI probably
    does this on Windows). Never overwrites an existing file."""
    missing = game_folder_missing(drive_c)
    if missing is None:
        return False, "LiveMotionConnector folder missing (D-BOX not installed?)"
    for rel in missing:
        s = os.path.join(connector_dir(drive_c), rel)
        d = os.path.join(game_dir(drive_c), rel)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copy2(s, d)
    return True, "copied %d files" % len(missing) if missing else "nothing missing"


def server_desc_path(server_script, home):
    """The motion_desc.bin the capture server at server_script would load (same order as
    SC_Motion_Server.py: env SC_MOTION_DESC, next to the real file, next to the link, home)."""
    cands = [os.environ.get("SC_MOTION_DESC"),
             os.path.join(os.path.dirname(os.path.realpath(server_script)), DESC_FILE),
             os.path.join(os.path.dirname(os.path.abspath(server_script)), DESC_FILE),
             os.path.join(home, DESC_FILE)]
    for p in cands:
        if p and os.path.isfile(p):
            return p
    return None


def desc_check(path):
    """(ok, detail) for the protobuf schema the capture server loads (motion_desc.bin)."""
    if not path:
        return False, "missing"
    if not os.path.isfile(path):
        return False, "missing"
    raw = open(path, "rb").read()
    miss = [n.decode() for n in DESC_NEEDLES if n not in raw]
    if miss:
        return False, "does not look like the LiveMotion descriptor (missing %s)" % ", ".join(miss)
    try:
        from google.protobuf import descriptor_pb2
        fdp = descriptor_pb2.FileDescriptorProto.FromString(raw)
        names = {m.name for m in fdp.message_type}
        need = {"IdentificationRequest", "IdentificationResponse", "LiveMotionRequest", "LiveMotionResponse"}
        if fdp.package != "dbox.proto.v1" or not need <= names:
            return False, "parsed, but package/messages differ (%s)" % fdp.package
        return True, "package %s, %d messages, sha256 %s..." % (fdp.package, len(names), sha256(path)[:12])
    except ImportError:
        return True, "names present (protobuf not installed, not parsed)"
    except Exception as e:  # noqa: BLE001
        return False, "protobuf could not parse it (%s)" % e


def python_modules():
    """{name: version or None}"""
    out = {}
    for mod, attr in (("grpc", "__version__"), ("google.protobuf", "__version__")):
        try:
            m = __import__(mod, fromlist=["x"])
            out[mod] = getattr(m, attr, "?")
        except Exception:  # noqa: BLE001
            out[mod] = None
    return out


def tcp_listening_local(port, timeout=0.5):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def udp_free(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def json_ok(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            d = json.load(f)
        n = len(d.get("effects", [])) if isinstance(d, dict) else 0
        return True, "valid JSON, %d effects, master %s" % (n, d.get("master") if isinstance(d, dict) else "?")
    except Exception as e:  # noqa: BLE001
        return False, "invalid: %s" % e


def compile_ok(path):
    try:
        with open(path, "rb") as f:
            compile(f.read(), path, "exec")
        return True, ""
    except SyntaxError as e:
        return False, "line %s: %s" % (e.lineno, e.msg)


# ----------------------------------------------------------------------------- report
class Report:
    ORDER = {"FAIL": 0, "WARN": 1, "PASS": 2, "INFO": 3, "SKIP": 4}

    def __init__(self, title):
        self.title = title
        self.rows = []

    def add(self, status, area, check, detail="", fix=""):
        self.rows.append((status, area, check, detail, fix))
        line = "  %-4s  %-10s %-34s %s" % (status, area, check, detail)
        print(line.rstrip(), flush=True)
        if fix and status in ("FAIL", "WARN"):
            print("        fix: %s" % fix, flush=True)

    def counts(self):
        c = {}
        for r in self.rows:
            c[r[0]] = c.get(r[0], 0) + 1
        return c

    def summary(self):
        c = self.counts()
        verdict = "FAIL" if c.get("FAIL") else ("WARN" if c.get("WARN") else "PASS")
        print()
        print("RESULT: %s  (%d pass, %d warn, %d fail, %d info)" % (
            verdict, c.get("PASS", 0), c.get("WARN", 0), c.get("FAIL", 0), c.get("INFO", 0)))
        return verdict

    def save(self, folder, name="verify"):
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        p = os.path.join(folder, "%s_%s.txt" % (name, stamp))
        with open(p, "w") as f:
            f.write("%s  %s  host %s\n\n" % (self.title, stamp, socket.gethostname()))
            for st, area, chk, det, fix in self.rows:
                f.write("%-4s  %-10s %-34s %s\n" % (st, area, chk, det))
                if fix and st in ("FAIL", "WARN"):
                    f.write("      fix: %s\n" % fix)
            c = self.counts()
            f.write("\n%s\n" % json.dumps(c))
        return p
