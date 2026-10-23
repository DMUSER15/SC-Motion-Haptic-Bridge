"""scm_checks.py - the pipeline test and the last-session log check, shared by the Linux and
Windows verifiers. Both drive the user's own sc_start.py helpers (start_logged, selftest,
stop_proc, listening), passed in as module S, so the test runs exactly what a real start runs.
"""
import ast, os, socket, sys, time

import scm_common as C


def _last(out):
    lines = [x for x in (out or "").splitlines() if x.strip()]
    return lines[-1].strip() if lines else ""


def live_pipeline(R, S, capture_script, env, verify_dir, lmcert_selftest):
    """Start capture server + tee/bridge, check them, stop them. Caller has checked that the
    game, SimHub and the pipeline are not running. lmcert_selftest() -> (rc, output)."""
    S.LOG_DIR = verify_dir
    os.makedirs(verify_dir, exist_ok=True)
    srv = brg = None
    try:
        srv, slog = S.start_logged([sys.executable, "-u", capture_script], "capture server",
                                   "verify_capture.log", env=env)
        end = time.monotonic() + 15
        while time.monotonic() < end and not S.listening("tcp", C.PORT_CAPTURE_TCP) and srv.poll() is None:
            time.sleep(0.25)
        if not S.listening("tcp", C.PORT_CAPTURE_TCP):
            time.sleep(0.5)
            R.add("FAIL", "live", "capture server listening",
                  "not on 61666; log: " + S.tail(slog, 5).strip()[-240:],
                  "run the capture server by hand to see the error: python3 SC_Motion_Server.py")
            return
        R.add("PASS", "live", "capture server listening", "127.0.0.1:61666")
        rc, out = lmcert_selftest()
        R.add("PASS" if rc == 0 else "FAIL", "live", "TLS self-test (game's certificate)", _last(out),
              "sc_lmcert.py status (the game copy must equal lm.crt)")
        brg, blog = S.start_logged(S.BRIDGE_CMD, "bridge", "verify_bridge.log", env=env)
        end = time.monotonic() + 10
        while time.monotonic() < end and not S.listening("udp", C.PORT_TEE_UDP) and brg.poll() is None:
            time.sleep(0.25)
        if not S.listening("udp", C.PORT_TEE_UDP):
            time.sleep(0.5)
            R.add("FAIL", "live", "bridge listening", "not on UDP 61700; log: " + S.tail(blog, 5).strip()[-240:])
            return
        R.add("PASS", "live", "bridge listening", "UDP 61700 (%s)" % os.path.basename(S.BRIDGE_SCRIPT))
        ok, msg = S.selftest(expect_zero=True)
        R.add("PASS" if ok else "FAIL", "live", "bridge -> SimHub packet", msg)
        if os.path.basename(S.BRIDGE_SCRIPT).lower() in ("lm_simhub3t.py", "lm_simhub4t.py"):
            rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                rx.bind(("127.0.0.1", C.PORT_PLUGIN_UDP))
                rx.settimeout(0.3)
                tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                probe = b'{"t": 0, "ev": 14, "name": "verify_probe", "m": [], "v": []}'
                got = None
                end = time.monotonic() + 2
                while time.monotonic() < end and got != probe:
                    tx.sendto(probe, ("127.0.0.1", C.PORT_TEE_UDP))
                    try:
                        got, _ = rx.recvfrom(4096)
                    except socket.timeout:
                        pass
                tx.close()
                R.add("PASS" if got == probe else "FAIL", "live", "tee -> plugin copy (UDP 61701)",
                      "byte-for-byte copy received" if got == probe else "no copy within 2 s")
            except OSError as e:
                R.add("WARN", "live", "tee -> plugin copy (UDP 61701)", "port busy (%s)" % e)
            finally:
                rx.close()
    finally:
        for p, n in ((brg, "bridge"), (srv, "capture server")):
            if p is not None:
                S.stop_proc(p, n)
        R.add("INFO", "live", "logs", verify_dir)


def _read_logs(launch_dir, name):
    out = []
    for p in (os.path.join(launch_dir, name + ".1"), os.path.join(launch_dir, name)):
        if os.path.isfile(p):
            with open(p, errors="replace") as f:
                out += f.read().splitlines()
    return out


def last_session(R, launch_dir):
    """Read the capture-server and bridge logs of the last start (sc_start.py / sc_start_win.py)."""
    cap = _read_logs(launch_dir, "capture_server.log")
    if not cap:
        R.add("WARN", "session", "capture server log", "none in %s" % launch_dir,
              "start the game from the menu entry / shortcut, fly, then run --last-session")
        return
    starts = [i for i, x in enumerate(cap) if "listening on 127.0.0.1:61666" in x]
    cap = cap[starts[-1]:] if starts else cap
    R.add("INFO", "session", "last start", cap[0][:19] if cap else "?")
    ident = [x for x in cap if "GetIdentification" in x]
    R.add("PASS" if ident else "FAIL", "session", "game connected (GetIdentification)",
          "yes" if ident else "no: the game did not reach the capture server",
          "certificate (sc_lmcert.py status) and the D-BOX connector log")
    opened = any("stream opened" in x for x in cap)
    R.add("PASS" if opened else "FAIL", "session", "telemetry stream opened", "yes" if opened else "no")
    closed = [x for x in cap if "posts per event:" in x]
    if closed:
        try:
            d = ast.literal_eval(closed[-1].split("posts per event:", 1)[1].strip())
            frames = d.get(3, 0)
            R.add("PASS" if frames else "WARN", "session", "vehicle frames (ev3)", "%d frames; events %s" % (
                frames, ", ".join("%s:%s" % kv for kv in sorted(d.items()))),
                "sit in a ship's pilot seat with the engines on")
        except (ValueError, SyntaxError):
            R.add("INFO", "session", "posts per event", closed[-1][-120:])
    else:
        R.add("INFO", "session", "stream end", "not closed yet (session still running?)")
    brg = _read_logs(launch_dir, "bridge.log")
    marks = [i for i, x in enumerate(brg) if "tee: UDP" in x or "bridge v3: UDP" in x]
    if marks:
        brg = brg[marks[-1]:]
    act = [x for x in brg if "active=1 seat=1" in x]
    R.add("PASS" if act else "WARN", "session", "bridge saw you seated with motion",
          "%d status lines with active=1 seat=1" % len(act), "sit in the pilot seat (seat gate)")
    tee = [x for x in brg if " tee: rx=" in x]
    if tee:
        R.add("INFO", "session", "tee", tee[-1].split(" tee: ", 1)[1])
