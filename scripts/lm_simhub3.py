#!/usr/bin/env python3
"""lm_simhub3.py - Star Citizen -> SimHub bridge, v3 (unshaped + seat gate).

New file; lm_simhub.py, lm_simhub.py.bak and lm_simhub2.py are left untouched.
Packet header, signatures, struct and send loop are copied from lm_simhub.py.

Signal path (decisions of 2026-10-17):
  rates  : yaw = -w[2]*DEG, pitch = -w[0]*DEG, roll = +w[1]*DEG   (w = ev3 id35)
  linear : surge = a[1]*S, sway = a[0]*S, heave = a[2]*S          (a = ev3 id40)
  vel    : v[1], v[0], v[2]                                         (v = ev3 id9)
Kept (noise / crosstalk / fault handling only):
  - landed gate v2: id92 >= 0.99 and |v| < LM_GV (0.5) -> linear = 0
  - glitch reject : any |a| > LM_GLITCH (300 m/s2 raw) or non-finite value
                    -> hold last good frame
  - stale ramp    : no ev3 for STALE s -> outputs ramp linearly to 0 in LM_RAMP s
  - SEAT GATE (new): all 9 outputs are multiplied by a gain g in [0,1].
        seated = latest ev10 (Player Role) id38 == LM_SEATV (1) and that ev10 is
                 no older than LM_SEATW s (0.25); ev2 Leave Vehicle -> not seated
                 immediately. Missing ev10 counts as NOT seated (fail-safe).
        Seated: output = g * live; g fades 0 -> 1 over LM_FADE s (0.5).
        Not seated: live frames are ignored; the last seated output is held and
        faded to 0 over LM_FADE s, so nothing sent after a seat exit can pass.
        LM_SEAT=0 disables the gate (g = 1 always).
No caps, no slew limits by default. Optional, off unless set:
  LM_CAPS="30,30,30,4.5,30,30,50,50,50"   per-channel caps after scale (9 values)
  LM_STEPS="0.5,0.5,0.5,0.08,0.08,0.08,0.5,0.5,0.5"  per-tick slew steps
  LM_SRC=38   use the old linear source (comparison only)
  LM_GATE=0   disable the landed gate (comparison only)

Usage:
  python3 lm_simhub3.py [print]        run the bridge (UDP 61700 -> 30777)
  python3 lm_simhub3.py sim FILE       OFFLINE: run the full path (incl. seat gate)
                                       over a capture; per-segment max|out| and the
                                       max|out| in the 1 s after every seat change.
                                       Opens no sockets; cannot move the rig.
"""
import socket, json, struct, time, threading, random, sys, math, os

IN_PORT = 61700
OUT = ("127.0.0.1", 30777)
RATE = 100.0
STALE = 0.3
DEG = 180.0 / math.pi
GAME_SIG = 0x450BC1A7
TELE_SIG = 0x0696EF94
FMT = "<IIHHQBQBBQBBBBdI" + "f" * 9
assert struct.calcsize(FMT) == 91
Z = [0.0, 0.0, 0.0]
NAMES = ("yaw", "pitch", "roll", "surge", "sway", "heave", "vfwd", "vlat", "vup")


def env_f(name, default):
    return float(os.environ.get(name, default))


def env_list(name):
    s = os.environ.get(name, "").strip()
    if not s:
        return None
    vals = [float(x) for x in s.split(",")]
    if len(vals) != 9:
        sys.exit("%s needs 9 comma-separated values, got %d" % (name, len(vals)))
    return tuple(vals)


SCALE = env_f("LM_SCALE", "0.1")
GV = env_f("LM_GV", "0.5")
GLITCH = env_f("LM_GLITCH", "300")
RAMP = env_f("LM_RAMP", "0.5")
SRC = int(os.environ.get("LM_SRC", "40"))
GATE = os.environ.get("LM_GATE", "1") != "0"
CAPS = env_list("LM_CAPS")
STEPS = env_list("LM_STEPS")
SEAT_ON = os.environ.get("LM_SEAT", "1") != "0"
SEATV = int(os.environ.get("LM_SEATV", "1"))
SEATW = env_f("LM_SEATW", "0.25")
FADE = env_f("LM_FADE", "0.5")


class Path:
    """Signal path: frame dict -> 9 output floats. No I/O. (Same as lm_simhub2.)"""

    def __init__(self):
        self.prev = [0.0] * 9
        self.good = None
        self.ramp_step = None
        self.n_gated = 0
        self.n_glitch = 0

    def raw(self, d):
        a = d.get(SRC, Z)
        w = d.get(35, Z)
        v = d.get(9, Z)
        vals = list(a) + list(w) + list(v)
        if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in vals):
            self.n_glitch += 1
            return self.good
        if max(abs(a[0]), abs(a[1]), abs(a[2])) > GLITCH:
            self.n_glitch += 1
            return self.good
        lin = (a[1] * SCALE, a[0] * SCALE, a[2] * SCALE)
        if GATE:
            spd = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
            gear = d.get(92, 0.0)
            if isinstance(gear, (int, float)) and gear >= 0.99 and spd < GV:
                lin = (0.0, 0.0, 0.0)
                self.n_gated += 1
        r = (-w[2] * DEG, -w[0] * DEG, w[1] * DEG) + lin + (v[1], v[0], v[2])
        self.good = r
        return r

    def shape(self, r):
        out = []
        for i, x in enumerate(r):
            if CAPS:
                x = max(-CAPS[i], min(CAPS[i], x))
            if STEPS:
                x = self.prev[i] + max(-STEPS[i], min(STEPS[i], x - self.prev[i]))
            out.append(x)
        self.prev = out
        return tuple(out)

    def active(self, d):
        self.ramp_step = None
        r = self.raw(d)
        if r is None:
            r = (0.0,) * 9
        return self.shape(r)

    def stale(self):
        if self.ramp_step is None:
            ticks = max(1.0, RAMP * RATE)
            self.ramp_step = [abs(p) / ticks for p in self.prev]
            self.good = None
        out = []
        for p, s in zip(self.prev, self.ramp_step):
            out.append(math.copysign(max(0.0, abs(p) - s), p) if s > 0 else 0.0)
        self.prev = out
        return tuple(out)


class Seat:
    """Seat gate: tracks ev10/ev2, returns gain g in [0,1]. No I/O."""

    def __init__(self):
        self.t10 = None
        self.v10 = None
        self.left = False   # ev2 seen and no ev10 == SEATV since
        self.g = 0.0 if SEAT_ON else 1.0

    def on_event(self, e, d, t):
        if e == 10:
            self.t10, self.v10 = t, d.get(38)
            if self.v10 == SEATV:
                self.left = False
        elif e == 2:
            self.left = True

    def seated(self, now):
        if not SEAT_ON:
            return True
        if self.left or self.t10 is None or now - self.t10 > SEATW:
            return False
        return self.v10 == SEATV

    def gain(self, now, dt):
        if not SEAT_ON:
            return 1.0
        target = 1.0 if self.seated(now) else 0.0
        step = dt / FADE if FADE > 0 else 1.0
        if self.g < target:
            self.g = min(target, self.g + step)
        elif self.g > target:
            self.g = max(target, self.g - step)
        return self.g

    def apply(self, live, now, dt):
        """Seated: g * live, remember live. Not seated: ignore live entirely and
        fade the last seated output to 0 (frames after a seat exit never pass)."""
        if not SEAT_ON:
            return live
        st = self.seated(now)
        g = self.gain(now, dt)
        if st:
            self.held = live
            return tuple(g * x for x in live)
        h = getattr(self, "held", None) or (0.0,) * len(live)
        return tuple(g * x for x in h)


def config_line():
    return ("src=id%d gate=%s scale=%.3f glitch=%.0f ramp=%.2fs seat=%s caps=%s steps=%s"
            % (SRC, "v2" if GATE else "off", SCALE, GLITCH, RAMP,
               ("ev10==%d w=%.2fs fade=%.2fs" % (SEATV, SEATW, FADE)) if SEAT_ON else "off",
               "off" if CAPS is None else ",".join("%g" % c for c in CAPS),
               "off" if STEPS is None else ",".join("%g" % s for s in STEPS)))


def sim(path):
    """Offline: one tick per ev3 frame; fade uses the frame's game dt (ev0 id93)."""
    evs = []
    seg = "(pre)"
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            e = o.get("ev")
            if e == "mark":
                seg = str(o.get("label", "?"))
                continue
            t = o.get("t")
            if not isinstance(t, (int, float)) or not isinstance(e, int):
                continue
            m, v = o.get("m", []) or [], o.get("v", []) or []
            evs.append((float(t), e, seg, dict(zip(m, v)) if len(m) == len(v) else {}))
    evs.sort(key=lambda x: x[0])
    t0 = evs[0][0] if evs else 0.0

    p, s = Path(), Seat()
    dt = 1.0 / RATE
    order, mx = [], {}
    changes = []          # (t, seg, text, [max over next 1 s], ...)
    was = None
    outs = []             # (t, out)
    for t, e, sg, d in evs:
        s.on_event(e, d, t)
        if e == 0:
            x = d.get(93)
            if isinstance(x, (int, float)) and 0 < x < 0.5:
                dt = float(x)
            continue
        if e != 3:
            continue
        st = s.seated(t)
        if st != was:
            changes.append((t, sg, "seated" if st else "NOT seated (ev10=%s%s)" % (s.v10, ", after ev2" if s.left else "")))
            was = st
        out = s.apply(p.active(d), t, dt)
        outs.append((t, out))
        if sg not in mx:
            order.append(sg)
            mx[sg] = [0.0] * 9
        row = mx[sg]
        for i, x in enumerate(out):
            if abs(x) > row[i]:
                row[i] = abs(x)

    print("OFFLINE sim of %s  (%s)" % (os.path.basename(path), config_line()))
    print("max|out| per segment; rates deg/s, linear m/s2 after scale, vel m/s")
    print("%-16s" % "segment" + "".join("%8s" % n for n in NAMES))
    tot = [0.0] * 9
    for sg in order:
        row = mx[sg]
        tot = [max(a, b) for a, b in zip(tot, row)]
        print("%-16s" % sg[:16] + "".join("%8.2f" % x for x in row))
    print("%-16s" % "ALL" + "".join("%8.2f" % x for x in tot))
    print("frames gated=%d glitch-held=%d" % (p.n_gated, p.n_glitch))
    print("\nseat changes: max|out| (motion channels) in the 1 s after each change")
    print("%9s %-14s %-36s %s" % ("t(s)", "segment", "change", "  ".join(NAMES[:6])))
    for t, sg, txt in changes:
        w = [o for ot, o in outs if t <= ot < t + 1.0]
        m = [max((abs(o[i]) for o in w), default=0.0) for i in range(6)]
        print("%9.2f %-14s %-36s %s" % (t - t0, sg[:14], txt[:36], " ".join("%6.2f" % x for x in m)))
    if not changes:
        print("(none)")


def run(show):
    st = {"d": {}, "t": 0.0}
    seat = Seat()
    lock = threading.Lock()
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        rx.bind(("127.0.0.1", IN_PORT))
    except OSError as e:
        sys.exit("cannot bind UDP %d (%s). Another process holds it: run  ss -ulpn | grep %d  and kill that pid" % (IN_PORT, e, IN_PORT))

    def rx_loop():
        while True:
            data, _ = rx.recvfrom(65535)
            try:
                p = json.loads(data)
            except Exception:
                continue
            e = p.get("ev")
            now = time.time()
            if e == 3:
                with lock:
                    st["d"] = dict(zip(p["m"], p["v"]))
                    st["t"] = now
            elif e in (10, 2):
                m, v = p.get("m") or [], p.get("v") or []
                with lock:
                    seat.on_event(e, dict(zip(m, v)) if len(m) == len(v) else {}, now)

    threading.Thread(target=rx_loop, daemon=True).start()
    path = Path()
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    emitter = random.getrandbits(64) or 1
    session = random.getrandbits(64) or 1
    counter = 0
    disc = 0
    stime = 0.0
    was_active = False
    last = time.time()
    nxt = last
    last_print = 0.0
    print("bridge v3: UDP %d -> SimHub %s:%d at %d Hz" % (IN_PORT, OUT[0], OUT[1], RATE), flush=True)
    print("bridge v3: " + config_line(), flush=True)
    while True:
        now = time.time()
        with lock:
            d = st["d"]
            t_rx = st["t"]
            seated = seat.seated(now)
        active = t_rx > 0 and now - t_rx < STALE
        if active and not was_active:
            disc += 1
            session = random.getrandbits(64) or 1
        was_active = active
        f = path.active(d) if active else path.stale()
        with lock:
            f = seat.apply(f, now, 1.0 / RATE)
            g = seat.g
        stime += now - last
        last = now
        counter += 1
        pkt = struct.pack(FMT, GAME_SIG, TELE_SIG, 1, 0, emitter, 0, counter, 1, 0, session, 0, 1 if active else 0, 0, 0, stime, disc, *f)
        tx.sendto(pkt, OUT)
        if show and now - last_print > 1.0:
            last_print = now
            print("active=%d seat=%d g=%.2f yaw=%.2f pitch=%.2f roll=%.2f surge=%.2f sway=%.2f heave=%.2f gated=%d glitch=%d"
                  % ((int(active), int(seated), g) + f[:6] + (path.n_gated, path.n_glitch)), flush=True)
        nxt += 1.0 / RATE
        time.sleep(max(0.0, nxt - time.time()))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "sim":
        if len(sys.argv) < 3 or not os.path.isfile(sys.argv[2]):
            sys.exit("usage: lm_simhub3.py sim FILE")
        sim(sys.argv[2])
    else:
        run(len(sys.argv) > 1 and sys.argv[1] == "print")
