#!/usr/bin/env python3
"""lm_simhub4.py - Star Citizen -> SimHub bridge, v4: v3 motion + Motion-haptics fields.

New file; lm_simhub3.py is imported UNCHANGED and stays the fallback.

Motion (the 9 fields of v3): exactly lm_simhub3's Path + Seat (unshaped, landed gate v2,
glitch hold, stale ramp, seat gate). Same numbers, same rules.

Packet layout and signature come from SimHub's own game definition file
("Star Citizen.simdef", JSON). The bridge reads it once at start: the field list (in
order), TelemetrySignature, GameSignature and layout versions. So the packet always
matches what SimHub expects; adding a supported field in SimHub's editor needs no code
change, only a bridge restart. An unsupported field stops the bridge with a message.

Extra fields (seat-gated: outside the pilot seat they read 0, except the ground state):
  SpeedKmh, GroundSpeedKmh      |ev3 id9| x 3.6
  EngineStarted, EngineIgnitionOn   1 while engines are on (ev3 id4 >= 0.5)
  EngineMaxRpm                  LM_RPM_MAX (8000)
  EngineRpm                     engines off 0; on: idle LM_RPM_IDLE (1000) + thrust share
                                up to EngineMaxRpm; thrust share = |ev3 id38| / the largest
                                non-boost thrust seen in this ship (at least 20 m/s2);
                                boost (ev4 id41 with thrust >= 20) = full
  Throttle                      the same thrust share, 0..1
  FlightIsOnGround, TyreContactSurface{FrontLeft,FrontRight,RearLeft,RearRight}
                                1 when landed: gear down (id92 >= 0.99) and the ship stands still
                                (|v| < LM_GROUND_V 0.6) for 0.3 s while the game's forces do not
                                balance (|id40| > LM_GROUND_A 1.0: the ground holds it), or a
                                touchdown contact. Cleared: gear up, |v| > 30 m/s, climbing
                                > 1 m/s for 0.5 s, or hovering (still, |id40| < LM_HOVER_A 0.5)
                                for 1 s. The ship's state: kept outside the seat, held while
                                no data
  SuspensionVelocity{FrontLeft,FrontRight,RearLeft,RearRight}Mps
                                LM_SUSP_FULL (1.0) m/s x the strongest of the sc_fx effects
                                shock (collisions, explosions: |id40| - own thrust, or
                                |id34 - id41|), touchdown (contact), hit_hull, hit_shield
                                (ev7) on a 0..1 scale; all four corners the same for now
These values are produced by sc_fx.py's engine (the same rules the SC Haptics plugin
runs), with the settings in HAPTIC_FX below.
NOTE: v3's glitch hold keeps |a| > 300 m/s2 frames out of the motion axes, so collision
spikes reach the rig only through these haptic fields (as SimHub haptic pulses).

Usage:
  python3 lm_simhub4.py [print]        run the bridge (UDP 61700 -> 30777)
  python3 lm_simhub4.py sim FILE       OFFLINE: v3 motion sim + every extra field per
                                       segment (max), on-ground changes, haptic pulses.
                                       Opens no sockets; cannot move the rig.
  python3 lm_simhub4.py trace FILE FROM TO [STEP]
                                       OFFLINE: the on-ground inputs frame by frame between
                                       FROM and TO seconds (time as in the sim report), one
                                       line every STEP s (0.1): gear, engines, |v|, v up,
                                       thrust |id38|, contact cHP, seated, on-ground, susp.
  python3 lm_simhub4.py layout         print the packet layout read from the .simdef
  python3 lm_simhub4.py testpattern    send a FIXED test packet (all motion fields 0,
                                       known values in the extra fields) for 20 s, to
                                       check the layout in SimHub's telemetry receiver
                                       tester. Motion fields are 0, but run it ONLY with
                                       the motion cabinet OFF.
Which .simdef: LM_SIMDEF if set; else the file SimHub registered (AppData/Local/SimHub/
ExternalSims/Registrations/<id>.simlink in the SimHub prefix LM_SIMHUB_PFX, default
~/linux-simracing-utils/pfx); else PluginsData/Common/ExternalSims/Star Citizen.simdef.
Env: LM_SIMDEF, LM_SIMHUB_PFX, LM_RPM_IDLE, LM_RPM_MAX, LM_SUSP_FULL, LM_GROUND_V,
LM_GROUND_A, LM_HOVER_A;
all lm_simhub3 env variables.
"""
import json, math, os, random, socket, struct, sys, threading, time

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import lm_simhub3 as v3  # noqa: E402  (unchanged module)
import sc_fx  # noqa: E402

VERSION = "4.2.2"
HEADER = "<IIHHQBQBBQBBBBdI"
SIMHUB_PFX = os.environ.get("LM_SIMHUB_PFX", os.path.expanduser("~/linux-simracing-utils/pfx"))
DEFAULT_SIMDEF = os.path.join(SIMHUB_PFX, "drive_c", "Program Files (x86)", "SimHub", "PluginsData", "Common",
                              "ExternalSims", "Star Citizen.simdef")
SIMDEF_UUID = "2f10c0be-479b-4b35-8671-a1b16ce4b874"


def find_simdef(windows=None):
    """-> (path, how). LM_SIMDEF, else the file SimHub itself registered (its .simlink holds a
    Windows path), else SimHub's ExternalSims folder.
    Linux (Wine): the registration is in the SimHub prefix's users/*/AppData/Local; C: = the
    prefix's drive_c, Z: = /. Windows: %LOCALAPPDATA%; the registered path is used as it is."""
    env = os.environ.get("LM_SIMDEF", "").strip()
    if env:
        return env, "LM_SIMDEF"
    if windows is None:
        windows = os.name == "nt"
    rel = os.path.join("SimHub", "ExternalSims", "Registrations", SIMDEF_UUID + ".simlink")
    if windows:
        la = os.environ.get("LOCALAPPDATA", "")
        if la:
            link = os.path.join(la, rel)
            w = _read_link(link)
            if w:
                return w, "SimHub registration " + link
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        return os.path.join(pf86, "SimHub", "PluginsData", "Common", "ExternalSims", "Star Citizen.simdef"), "default"
    users = os.path.join(SIMHUB_PFX, "drive_c", "users")
    try:
        names = sorted(os.listdir(users))
    except OSError:
        names = []
    for u in names:
        link = os.path.join(users, u, "AppData", "Local", rel)
        w = _read_link(link)
        if w and len(w) > 2 and w[1] == ":":
            rest = w[2:].replace("\\", "/").lstrip("/")
            base = os.path.join(SIMHUB_PFX, "drive_c") if w[0].upper() == "C" else ("/" if w[0].upper() == "Z" else None)
            if base is not None:
                return os.path.join(base, rest), "SimHub registration " + link
    return DEFAULT_SIMDEF, "default"


def _read_link(link):
    try:
        with open(link, encoding="utf-8-sig") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


RPM_IDLE = v3.env_f("LM_RPM_IDLE", "1000")
RPM_MAX = v3.env_f("LM_RPM_MAX", "8000")
SUSP_FULL = v3.env_f("LM_SUSP_FULL", "1.0")
GROUND_V = v3.env_f("LM_GROUND_V", "0.6")      # still: |v| below this (m/s)
GROUND_A = v3.env_f("LM_GROUND_A", "1.0")      # unbalanced: |id40| above this (m/s2)
HOVER_A = v3.env_f("LM_HOVER_A", "0.5")        # balanced (hover): |id40| below this

# the 9 motion fields of v3, in v3's output order
MOTION = ("YawRateDegreesPerSecond", "PitchRateDegreesPerSecond", "RollRateDegreesPerSecond",
          "LocalSurgeMs2", "LocalSwayMs2", "LocalHeaveMs2",
          "LocalVelocityForwardMps", "LocalVelocityLateralMps", "LocalVelocityUpMps")
CORNERS = ("FrontLeft", "FrontRight", "RearLeft", "RearRight")
# struct code per supported standard field (types as SimHub's editor lists them)
TYPES = dict({m: "f" for m in MOTION})
TYPES.update({"SpeedKmh": "f", "GroundSpeedKmh": "f", "EngineRpm": "f", "EngineMaxRpm": "f", "Throttle": "f",
              "EngineStarted": "B", "EngineIgnitionOn": "B", "FlightIsOnGround": "B"})
TYPES.update({"SuspensionVelocity%sMps" % c: "f" for c in CORNERS})
TYPES.update({"TyreContactSurface%s" % c: "H" for c in CORNERS})

# sc_fx effects whose 0..100 values drive the suspension-velocity pulses (same rules and
# defaults as the plugin's effects.json; "when": any because the bridge gates by seat itself)
HAPTIC_FX = {"master": 100, "effects": [
    {"name": "shock", "type": "shock", "threshold": 300, "full": 1000, "threshold_rot": 10, "full_rot": 150, "ms": 300, "gain": 100, "when": "any"},
    {"name": "touchdown", "type": "contact", "threshold": 20, "full": 90, "ms": 250, "gain": 90, "when": "any"},
    {"name": "hit_hull", "type": "pulse", "ev": 7, "cond": [{"field": 39, "min": 0.000001}], "field": 39, "offset": -0.0045, "scale": 60, "ms": 220, "gain": 85, "when": "any"},
    {"name": "hit_shield", "type": "pulse", "ev": 7, "cond": [{"field": 40, "min": 0.000001}], "field": 40, "offset": -0.0045, "scale": 60, "ms": 150, "gain": 60, "when": "any"},
]}


def load_simdef(path):
    """-> dict(game_sig, tele_sig, major, minor, fields, fmt). Exits with a message on problems."""
    try:
        with open(path, encoding="utf-8-sig") as fh:
            j = json.load(fh)
    except (OSError, ValueError) as e:
        sys.exit("cannot read the SimHub game definition %s (%s). Set LM_SIMDEF to its path." % (path, e))
    td = j.get("TelemetryDefinition") or {}
    fields = []
    for f in td.get("Fields") or []:
        if f.get("TypeName") != "StandardField":
            sys.exit("%s: field %r is a %s; lm_simhub4 supports standard fields only" % (path, f.get("FieldType"), f.get("TypeName")))
        fields.append(f.get("FieldType"))
    bad = [f for f in fields if f not in TYPES]
    if bad:
        sys.exit("%s: unsupported field(s) %s.\nSupported: %s" % (path, ", ".join(map(str, bad)), ", ".join(sorted(TYPES))))
    missing = [m for m in MOTION if m not in fields]
    if missing:
        sys.exit("%s: motion field(s) missing: %s" % (path, ", ".join(missing)))
    if len(set(fields)) != len(fields):
        sys.exit("%s: a field is listed twice" % path)
    for k in ("GameSignature", "TelemetrySignature"):
        if not isinstance(j.get(k), int):
            sys.exit("%s: %s missing" % (path, k))
    fmt = HEADER + "".join(TYPES[f] for f in fields)
    return {"game_sig": j["GameSignature"] & 0xFFFFFFFF, "tele_sig": j["TelemetrySignature"] & 0xFFFFFFFF,
            "major": int(td.get("LayoutMajorVersion", 1)), "minor": int(td.get("LayoutMinorVersion", 0)),
            "fields": fields, "fmt": fmt, "path": path, "how": ""}


class Haptics:
    """Extra-field values from the raw event stream. No I/O."""

    def __init__(self):
        master, fx, errors, fatal = sc_fx.parse_config(json.dumps(HAPTIC_FX))
        if fatal or errors:
            raise RuntimeError("HAPTIC_FX: %s" % errors)
        self.eng = sc_fx.Engine(master, fx)
        self.idx = {f["name"]: i for i, f in enumerate(fx)}
        self.d3 = {}
        self.t3 = None
        self.boost_t = None
        self.ref = 20.0
        self.ground = None      # unknown until the first ev3 frame (read as "on ground")
        self.still_t = None     # since when: still while forces do not balance (ground holds it)
        self.hover_t = None     # since when: still while forces balance (hovering)
        self.climb_t = None     # since when: climbing faster than 1 m/s
        self.td_count = 0

    def on_event(self, e, d, t):
        self.eng.on_packet(e, d, t)
        if e == 1:
            self.ref = 20.0           # new ship: thrust reference starts again
        elif e == 4:
            b = d.get(41)
            if isinstance(b, (int, float)) and b >= 0.5:
                self.boost_t = t
        elif e == 3:
            self.d3, self.t3 = d, t
            self._ground(d, t)

    def _thrust(self):
        a = sc_fx.vec(self.d3.get(38))
        return math.sqrt(sum(x * x for x in a)) if a else 0.0

    def _speed(self):
        v = sc_fx.vec(self.d3.get(9))
        return math.sqrt(sum(x * x for x in v)) if v else 0.0

    def _ground(self, d, t):
        """Landed = gear down and the ship stands still although the game's own forces
        (ev3 id40 = thrust + gravity + drag, no ground reaction) do not balance: something
        holds it. Hovering = still with balanced forces (|id40| ~ 0). Own thrust (id38) is
        not used: it keeps its last commanded value on the ground (7.57 / 22.93 m/s2 seen)."""
        gear = d.get(92)
        gear_down = isinstance(gear, (int, float)) and gear >= 0.99
        spd = self._speed()
        v = sc_fx.vec(d.get(9)) or [0.0, 0.0, 0.0]
        a = sc_fx.vec(d.get(40))
        acc = math.sqrt(sum(x * x for x in a)) if a else 0.0
        still = spd < GROUND_V
        self.still_t = (self.still_t if self.still_t is not None else t) if (still and acc > GROUND_A) else None
        self.hover_t = (self.hover_t if self.hover_t is not None else t) if (spd < 0.3 and acc < HOVER_A) else None
        self.climb_t = (self.climb_t if self.climb_t is not None else t) if v[2] > 1.0 else None
        if self.ground is None:
            # first frame: take the state as it is, without a 0 -> 1 change (no touchdown
            # pulse when data starts while parked)
            self.ground = bool(gear_down and still and acc > GROUND_A)
            return
        if not self.ground:
            contact = self.eng.chp > HAPTIC_FX["effects"][1]["threshold"]
            if gear_down and ((self.still_t is not None and t - self.still_t >= 0.3) or contact):
                self.ground = True
                self.td_count += 1
        else:
            if (not gear_down or spd > 30.0 or (self.climb_t is not None and t - self.climb_t >= 0.5)
                    or (self.hover_t is not None and t - self.hover_t >= 1.0)):
                self.ground = False

    def values(self, now, seated, g):
        """-> {field: value} for every supported extra field."""
        d = self.d3
        eng_on = isinstance(d.get(4), (int, float)) and d.get(4) >= 0.5
        thr = self._thrust()
        boosting = self.boost_t is not None and now - self.boost_t <= 0.25 and thr >= 20.0
        if not boosting:
            self.ref = max(self.ref, thr)
        share = 1.0 if boosting else min(1.0, thr / self.ref)
        rpm = (RPM_IDLE + (RPM_MAX - RPM_IDLE) * share) if eng_on else 0.0
        spd = self._speed() * 3.6
        pulse = max(self.eng.value(self.idx[n], now) for n in self.idx) / 100.0
        on = seated and g > 0.0
        out = {
            "SpeedKmh": spd * g if on else 0.0,
            "GroundSpeedKmh": spd * g if on else 0.0,
            "EngineStarted": 1 if on and eng_on else 0,
            "EngineIgnitionOn": 1 if on and eng_on else 0,
            "EngineMaxRpm": RPM_MAX,
            "EngineRpm": rpm * g if on else 0.0,
            "Throttle": (share if eng_on else 0.0) * g if on else 0.0,
            "FlightIsOnGround": 0 if self.ground is False else 1,
        }
        for c in CORNERS:
            out["SuspensionVelocity%sMps" % c] = SUSP_FULL * pulse * g if on else 0.0
            out["TyreContactSurface%s" % c] = 0 if self.ground is False else 1
        return out


def pack(sd, hdr, motion, extra):
    vals = []
    for f in sd["fields"]:
        if f in MOTION:
            vals.append(motion[MOTION.index(f)])
        else:
            x = extra[f]
            vals.append(int(x) if TYPES[f] in "BH" else float(x))
    return struct.pack(sd["fmt"], *(tuple(hdr) + tuple(vals)))


def header(sd, emitter, counter, session, running, stime, disc):
    return (sd["game_sig"], sd["tele_sig"], sd["major"], sd["minor"], emitter, 0, counter, 1, 0, session,
            0, running, 0, 0, stime, disc)


def layout(sd):
    print("simdef %s  (%s)\n  GameSignature 0x%08X  TelemetrySignature 0x%08X  layout %d.%d  packet %d bytes"
          % (sd["path"], sd["how"], sd["game_sig"], sd["tele_sig"], sd["major"], sd["minor"], struct.calcsize(sd["fmt"])))
    off = struct.calcsize(HEADER)
    print("  header %d bytes, then:" % off)
    for f in sd["fields"]:
        n = struct.calcsize("<" + TYPES[f])
        print("  @%3d  %-6s %s%s" % (off, {"f": "Float32", "B": "Byte", "H": "UInt16"}[TYPES[f]], f, "  (motion, v3)" if f in MOTION else ""))
        off += n


TEST_VALUES = {"SpeedKmh": 123.4, "GroundSpeedKmh": 123.4, "EngineStarted": 1, "EngineIgnitionOn": 1,
               "EngineMaxRpm": 8000.0, "EngineRpm": 4321.0, "Throttle": 0.5, "FlightIsOnGround": 1}
TEST_VALUES.update({"SuspensionVelocity%sMps" % c: 0.11 * (i + 1) for i, c in enumerate(CORNERS)})
TEST_VALUES.update({"TyreContactSurface%s" % c: 1 for c in CORNERS})


def testpattern(sd, seconds=20.0):
    print("TEST PATTERN for %.0f s to 127.0.0.1:30777. Motion cabinet must be OFF.\nExpected in the receiver tester:" % seconds)
    for f in sd["fields"]:
        print("  %-34s %s" % (f, 0.0 if f in MOTION else TEST_VALUES[f]))
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    emitter, session = random.getrandbits(64) or 1, random.getrandbits(64) or 1
    t0 = time.time()
    n = 0
    while time.time() - t0 < seconds:
        n += 1
        tx.sendto(pack(sd, header(sd, emitter, n, session, 1, time.time() - t0, 1), (0.0,) * 9, TEST_VALUES), v3.OUT)
        time.sleep(0.01)
    print("sent %d packets" % n)


def sim(sd, path):
    """Offline: v3's own sim output first (motion, unchanged), then the extra fields."""
    v3.sim(path)
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
    if not evs:
        return
    t0 = evs[0][0]
    extra = [f for f in sd["fields"] if f not in MOTION]
    hp, seat, p = Haptics(), v3.Seat(), v3.Path()
    dt = 1.0 / v3.RATE
    order, mx = [], {}
    ground_changes, pulses = [], []
    was_ground, in_pulse = None, False
    for t, e, sg, d in evs:
        seat.on_event(e, d, t)
        hp.on_event(e, d, t)
        if e == 0:
            x = d.get(93)
            if isinstance(x, (int, float)) and 0 < x < 0.5:
                dt = float(x)
            continue
        if e != 3:
            continue
        seat.apply(p.active(d), t, dt)          # advances the seat fade exactly like v3
        vals = hp.values(t, seat.seated(t), seat.g)
        if sg not in mx:
            order.append(sg)
            mx[sg] = {f: 0.0 for f in extra}
        for f in extra:
            mx[sg][f] = max(mx[sg][f], abs(vals[f]))
        gnd = vals["FlightIsOnGround"]
        if gnd != was_ground:
            ground_changes.append((t - t0, sg, gnd))
            was_ground = gnd
        sv = vals["SuspensionVelocityFrontLeftMps"]
        if sv > 0.05 and not in_pulse:
            pulses.append([t - t0, sv, sg])
            in_pulse = True
        elif in_pulse and sv > pulses[-1][1]:
            pulses[-1][1] = sv
        elif sv <= 0.05:
            in_pulse = False
    print("\nlm_simhub4 %s extra fields (simdef %s, TelemetrySignature 0x%08X): max per segment"
          % (VERSION, os.path.basename(sd["path"]), sd["tele_sig"]))
    short = {f: f.replace("SuspensionVelocity", "Susp").replace("Mps", "").replace("TyreContactSurface", "Tyre")
             .replace("FrontLeft", "FL").replace("FrontRight", "FR").replace("RearLeft", "RL").replace("RearRight", "RR")
             for f in extra}
    print("%-16s" % "segment" + "".join("%11s" % short[f][:10] for f in extra))
    for sg in order:
        print("%-16s" % sg[:16] + "".join("%11.2f" % mx[sg][f] for f in extra))
    print("FlightIsOnGround changes (t s, segment, value): %d" % len(ground_changes))
    for t, sg, g in ground_changes[:40]:
        print("  %8.2f  %-16s %d" % (t, sg[:16], g))
    print("suspension pulses > 0.05 m/s (t s, peak m/s, segment): %d" % len(pulses))
    for t, sv, sg in pulses[:60]:
        print("  %8.2f  %5.2f  %s" % (t, sv, sg[:16]))


def trace(path, t_from, t_to, step=0.1):
    """Offline: ground-detection inputs and result, one line per STEP s in [FROM, TO]."""
    evs = []
    seg = "(pre)"
    with open(path) as f:
        for line in f:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            e = o.get("ev")
            if e == "mark":
                seg = str(o.get("label", "?"))
                continue
            tt = o.get("t")
            if not isinstance(tt, (int, float)) or not isinstance(e, int):
                continue
            m, v = o.get("m", []) or [], o.get("v", []) or []
            evs.append((float(tt), e, seg, dict(zip(m, v)) if len(m) == len(v) else {}))
    evs.sort(key=lambda x: x[0])
    if not evs:
        return
    t0 = evs[0][0]
    hp, seat = Haptics(), v3.Seat()
    nxt = None
    print("trace %s  %.1f..%.1f s  (landed: gear>=0.99, |v|<%.2f and |id40|>%.1f for 0.3 s, or contact; clears: gear up, |v|>30, v up>1 for 0.5 s, hover |v|<0.3 and |id40|<%.1f for 1 s)"
          % (os.path.basename(path), t_from, t_to, GROUND_V, GROUND_A, HOVER_A))
    print("%9s %-16s %5s %4s %7s %7s %7s %7s %7s %4s %4s %5s" % ("t(s)", "segment", "gear", "eng", "|v|", "v_up", "thrust", "|id40|", "cHP", "seat", "gnd", "susp"))
    for tt, e, sg, d in evs:
        seat.on_event(e, d, tt)
        hp.on_event(e, d, tt)
        if e != 3:
            continue
        r = tt - t0
        if r < t_from:
            continue
        if r > t_to:
            break
        if nxt is not None and r < nxt:
            continue
        nxt = r + step
        v = sc_fx.vec(d.get(9)) or [0.0, 0.0, 0.0]
        vals = hp.values(tt, seat.seated(tt), 1.0)
        gear, eng = d.get(92), d.get(4)
        a40 = sc_fx.vec(d.get(40))
        print("%9.2f %-16s %5s %4s %7.2f %7.2f %7.2f %7.2f %7.1f %4d %4d %5.2f" % (
            r, sg[:16], ("%.2f" % gear) if isinstance(gear, (int, float)) else "-", ("%.0f" % eng) if isinstance(eng, (int, float)) else "-",
            hp._speed(), v[2], hp._thrust(), math.sqrt(sum(x * x for x in a40)) if a40 else 0.0, hp.eng.chp, int(seat.seated(tt)), vals["FlightIsOnGround"], vals["SuspensionVelocityFrontLeftMps"]))


def run(sd, show):
    st = {"t": 0.0, "d": {}}
    seat, hp = v3.Seat(), Haptics()
    lock = threading.Lock()
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        rx.bind(("127.0.0.1", v3.IN_PORT))
    except OSError as e:
        sys.exit("cannot bind UDP %d (%s). Another process holds it: run  ss -ulpn | grep %d  and kill that pid" % (v3.IN_PORT, e, v3.IN_PORT))

    def rx_loop():
        while True:
            data, _ = rx.recvfrom(65535)
            try:
                p = json.loads(data)
            except Exception:
                continue
            e = p.get("ev")
            if not isinstance(e, int):
                continue
            m, v = p.get("m") or [], p.get("v") or []
            d = dict(zip(m, v)) if len(m) == len(v) else {}
            now = time.time()
            with lock:
                try:
                    hp.on_event(e, d, now)
                except Exception:
                    pass
                if e == 3:
                    st["d"], st["t"] = d, now
                elif e in (10, 2):
                    seat.on_event(e, d, now)

    threading.Thread(target=rx_loop, daemon=True).start()
    path = v3.Path()
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    emitter = random.getrandbits(64) or 1
    session = random.getrandbits(64) or 1
    counter = disc = 0
    stime = 0.0
    was_active = False
    last = nxt = time.time()
    last_print = 0.0
    print("bridge v4 (%s): UDP %d -> SimHub %s:%d at %d Hz" % (VERSION, v3.IN_PORT, v3.OUT[0], v3.OUT[1], v3.RATE), flush=True)
    print("bridge v4: " + v3.config_line(), flush=True)
    print("bridge v4: simdef %s (%s), TelemetrySignature 0x%08X, %d fields (%d extra)"
          % (sd["path"], sd["how"], sd["tele_sig"], len(sd["fields"]), len(sd["fields"]) - 9), flush=True)
    while True:
        now = time.time()
        with lock:
            d = st["d"]
            t_rx = st["t"]
            seated = seat.seated(now)
        active = t_rx > 0 and now - t_rx < v3.STALE
        if active and not was_active:
            disc += 1
            session = random.getrandbits(64) or 1
        was_active = active
        f = path.active(d) if active else path.stale()
        with lock:
            f = seat.apply(f, now, 1.0 / v3.RATE)
            g = seat.g
            # stale: everything 0 except the ground state, which is held (no false touchdown
            # when the data comes back)
            ex = hp.values(now, seated, g) if active else hp.values(now, False, 0.0)
        stime += now - last
        last = now
        counter += 1
        tx.sendto(pack(sd, header(sd, emitter, counter, session, 1 if active else 0, stime, disc), f, ex), v3.OUT)
        if show and now - last_print > 1.0:
            last_print = now
            print("active=%d seat=%d g=%.2f yaw=%.2f pitch=%.2f roll=%.2f surge=%.2f sway=%.2f heave=%.2f rpm=%.0f gnd=%d susp=%.2f"
                  % ((int(active), int(seated), g) + tuple(f[:6]) + (ex["EngineRpm"], ex["FlightIsOnGround"], ex["SuspensionVelocityFrontLeftMps"])), flush=True)
        nxt += 1.0 / v3.RATE
        time.sleep(max(0.0, nxt - time.time()))


def main(argv):
    if len(argv) > 1 and argv[1] == "trace":
        if len(argv) < 5 or not os.path.isfile(argv[2]):
            sys.exit("usage: lm_simhub4.py trace FILE FROM TO [STEP]")
        trace(argv[2], float(argv[3]), float(argv[4]), float(argv[5]) if len(argv) > 5 else 0.1)
        return
    path, how = find_simdef()
    sd = load_simdef(path)
    sd["how"] = how
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "layout":
        layout(sd)
    elif cmd == "sim":
        if len(argv) < 3 or not os.path.isfile(argv[2]):
            sys.exit("usage: lm_simhub4.py sim FILE")
        sim(sd, argv[2])
    elif cmd == "testpattern":
        testpattern(sd)
    else:
        run(sd, cmd == "print")


if __name__ == "__main__":
    main(sys.argv)
