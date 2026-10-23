#!/usr/bin/env python3
"""sc_fx.py - SC Haptics effects: config check + OFFLINE simulation over a capture.

Reference implementation of the plugin's effect engine (SCHaptics Effects.cs mirrors
this file rule for rule). Opens no sockets; cannot move the rig.

  python3 sc_fx.py check [CONFIG]          validate effects.json, list effects
  python3 sc_fx.py sim FILE [CONFIG]       run all effects over a capture (.ndjson):
                                           per effect triggers / max / active time,
                                           per shaker and motor max, contact (cHP) and
                                           shock peaks, and per recording segment (marks):
                                           active s per effect plus max thrust |ev3 id38|
CONFIG defaults to ~/sc_haptics/effects.json

effects.json:
  {"master": 100, "effects": [ {effect}, ... ]}
effect keys:
  name     (required) letters, digits, _ ; property SCHaptics.Fx.<name>
  type     (required) pulse | edge | level | contact | shock
  enabled  true
  ev       event number (required except contact / shock)
  match    {"field id": value, ...}  all must be equal (numbers)
  cond     [ {"field": 40, "axis"?, "min"?, "max"?}, ... ]  every condition must hold on the
           triggering post itself (pulse / edge: no trigger; level: value 0)
  field    field id read by edge / level; pulse: optional strength source
           (peak = gain * clamp((x - offset) * scale, 0, 1); 0 -> no trigger)
  axis     0/1/2 = x/y/z component of a vector field; default magnitude
  gain     0..100 (50)       freq  Hz for ShakeIt Bass Shakers (40)
  shakers  [1..8] ([] = none)   -> SCHaptics.Shaker<n>.*  (ShakeIt Bass Shakers)
  motors   [1..8] ([] = none)   -> SCHaptics.Motor<n>.Amp (ShakeIt Motors)
  when     any | seated | foot   (any; contact: seated)
  ms       pulse length for pulse / edge / contact / shock (100)
  threshold edge: rising through it (0.5); contact: cHP m/s2 (20);
           shock: external linear acceleration m/s2 (300)
  full     contact: cHP for full gain (90); shock: linear m/s2 for full gain (1000)
  threshold_rot / full_rot  shock: external angular acceleration (10 / 150)
  window_ms shock: thrust look-back for the linear part (100)
  scale/offset  level and pulse with field: gain * clamp((x - offset) * scale, 0, 1);
           scale 0 = on/off (1 / 0)
  min/max  level: outside -> 0 (no limit)
  hold_ms  level: value expires when posts stop (250)
  band     low | high: which per-shaker ShakeIt channel carries it
           (default: low if freq < 40 Hz, else high)
  require  [ {"ev": 3, "field": 38, "min": 20}, ... ]  latest value of that event field (axis
           as above, default magnitude) within min..max and no older than fresh_ms (250).
           level / contact: output only while every condition holds.
           pulse / edge / shock: checked once, when the effect triggers.
  note     free text, ignored
shock (ev3 only): linear = |id40| - max |id38| over the last window_ms (acceleration not
explained by own thrust: collisions, explosions); rotational = |id34 - id41| (total minus
thruster-commanded angular acceleration). Triggers when either is above its threshold;
strength = gain * min(1, max(linear / full, rotational / full_rot)).
Outputs per shaker n: Shaker<n>.Amp (all bands), Shaker<n>.Low, Shaker<n>.High
(each = max of the routed effects in that band, x master/100). Per motor n: Motor<n>.Amp
(max of the routed effects, x master/100).
"""
import json, math, os, re, sys

VERSION = "0.3.0"
MAX_SHAKERS = 8
MAX_MOTORS = 8
SEAT_WINDOW = 0.25
KNOWN = {"name", "type", "enabled", "ev", "match", "field", "axis", "gain", "freq", "shakers", "when",
         "ms", "threshold", "full", "scale", "offset", "min", "max", "hold_ms", "note", "band", "require",
         "motors", "cond", "threshold_rot", "full_rot", "window_ms"}
REQ_KNOWN = {"ev", "field", "axis", "min", "max", "fresh_ms"}
COND_KNOWN = {"field", "axis", "min", "max"}
BANDS = ("low", "high")
BAND_SPLIT_HZ = 40.0
TYPES = ("pulse", "edge", "level", "contact", "shock")
TRIGGER_REQ = ("pulse", "edge", "shock")    # require checked when triggering, not at output
WHENS = ("any", "seated", "foot")
DEFAULT_CONFIG = os.path.expanduser("~/sc_haptics/effects.json")


def num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def parse_config(text):
    """-> (master, [effect dict], errors, fatal)"""
    errors = []
    try:
        root = json.loads(text)
    except ValueError as e:
        return 100.0, [], ["JSON: %s" % e], True
    if not isinstance(root, dict):
        return 100.0, [], ["top level must be an object"], True
    master = root.get("master", 100)
    if not num(master):
        errors.append("master must be a number")
        master = 100
    master = max(0.0, min(100.0, float(master)))
    effects, names = [], set()
    lst = root.get("effects", [])
    if not isinstance(lst, list):
        return master, [], ["effects must be a list"], True
    for i, e in enumerate(lst):
        where = "effect %d" % (i + 1)
        if not isinstance(e, dict):
            errors.append("%s: not an object" % where)
            continue
        name = e.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_]+", name):
            errors.append("%s: name missing or not [A-Za-z0-9_]" % where)
            continue
        where = "effect '%s'" % name
        if name in names:
            errors.append("%s: duplicate name" % where)
            continue
        for k in e:
            if k not in KNOWN:
                errors.append("%s: unknown key '%s' (ignored)" % (where, k))
        t = e.get("type")
        if t not in TYPES:
            errors.append("%s: type must be one of %s" % (where, "/".join(TYPES)))
            continue
        shock = t == "shock"
        f = {"name": name, "type": t, "enabled": e.get("enabled", True) is not False,
             "ev": e.get("ev", -1), "field": e.get("field", -1), "axis": e.get("axis", -1),
             "gain": e.get("gain", 50), "freq": e.get("freq", 40), "when": e.get("when", "seated" if t == "contact" else "any"),
             "ms": e.get("ms", 100),
             "threshold": e.get("threshold", 20 if t == "contact" else 300 if shock else 0.5),
             "full": e.get("full", 1000 if shock else 90), "scale": e.get("scale", 1), "offset": e.get("offset", 0),
             "min": e.get("min", -math.inf), "max": e.get("max", math.inf), "hold_ms": e.get("hold_ms", 250),
             "threshold_rot": e.get("threshold_rot", 10), "full_rot": e.get("full_rot", 150),
             "window_ms": e.get("window_ms", 100)}
        bad = [k for k in ("ev", "field", "axis", "gain", "freq", "ms", "threshold", "full", "scale", "offset", "min", "max", "hold_ms",
                           "threshold_rot", "full_rot", "window_ms")
               if not num(f[k])]
        if bad:
            errors.append("%s: %s must be numbers" % (where, ", ".join(bad)))
            continue
        b = e.get("band", "low" if num(f["freq"]) and f["freq"] < BAND_SPLIT_HZ else "high")
        if b not in BANDS:
            errors.append("%s: band must be low or high" % where)
            continue
        f["band"] = b
        if f["when"] not in WHENS:
            errors.append("%s: when must be any/seated/foot" % where)
            continue
        if t not in ("contact", "shock") and f["ev"] < 0:
            errors.append("%s: ev required" % where)
            continue
        if t in ("edge", "level") and f["field"] < 0:
            errors.append("%s: field required for %s" % (where, t))
            continue
        if f["axis"] not in (-1, 0, 1, 2):
            errors.append("%s: axis must be 0, 1 or 2" % where)
            continue
        sh = e.get("shakers", [])
        if not isinstance(sh, list) or not all(num(s) and 1 <= s <= MAX_SHAKERS and s == int(s) for s in sh):
            errors.append("%s: shakers must be a list of 1..%d" % (where, MAX_SHAKERS))
            continue
        mo = e.get("motors", [])
        if not isinstance(mo, list) or not all(num(s) and 1 <= s <= MAX_MOTORS and s == int(s) for s in mo):
            errors.append("%s: motors must be a list of 1..%d" % (where, MAX_MOTORS))
            continue
        cond, c_ok = [], True
        cl = e.get("cond", [])
        if not isinstance(cl, list):
            c_ok = False
        else:
            for r in cl:
                if not isinstance(r, dict) or any(k not in COND_KNOWN for k in r):
                    c_ok = False
                    break
                q = {"field": r.get("field", -1), "axis": r.get("axis", -1),
                     "min": r.get("min", -math.inf), "max": r.get("max", math.inf)}
                if not all(num(q[k]) for k in q) or q["field"] < 0 or q["axis"] not in (-1, 0, 1, 2):
                    c_ok = False
                    break
                q["field"], q["axis"] = int(q["field"]), int(q["axis"])
                cond.append(q)
        if not c_ok:
            errors.append("%s: cond must be a list of {field, axis?, min?, max?}" % where)
            continue
        m = e.get("match", {})
        if not isinstance(m, dict) or not all(re.fullmatch(r"\d+", k) and num(v) for k, v in m.items()):
            errors.append("%s: match must be {\"id\": number}" % where)
            continue
        req, rq_ok = [], True
        rl = e.get("require", [])
        if not isinstance(rl, list):
            rq_ok = False
        else:
            for r in rl:
                if not isinstance(r, dict) or any(k not in REQ_KNOWN for k in r):
                    rq_ok = False
                    break
                q = {"ev": r.get("ev", -1), "field": r.get("field", -1), "axis": r.get("axis", -1),
                     "min": r.get("min", -math.inf), "max": r.get("max", math.inf), "fresh_ms": r.get("fresh_ms", 250)}
                if not all(num(q[k]) for k in q) or q["ev"] < 0 or q["field"] < 0 or q["axis"] not in (-1, 0, 1, 2):
                    rq_ok = False
                    break
                for k in ("ev", "field", "axis"):
                    q[k] = int(q[k])
                q["fresh_ms"] = max(1.0, float(q["fresh_ms"]))
                req.append(q)
        if not rq_ok:
            errors.append("%s: require must be a list of {ev, field, axis?, min?, max?, fresh_ms?}" % where)
            continue
        f["require"] = req
        f["cond"] = cond
        f["match"] = [(int(k), float(v)) for k, v in m.items()]
        f["shakers"] = sorted(set(int(s) for s in sh))
        f["motors"] = sorted(set(int(s) for s in mo))
        f["gain"] = max(0.0, min(100.0, float(f["gain"])))
        f["ms"] = max(1.0, float(f["ms"]))
        f["hold_ms"] = max(1.0, float(f["hold_ms"]))
        f["full"] = max(1e-6, float(f["full"]))
        f["full_rot"] = max(1e-6, float(f["full_rot"]))
        f["window_ms"] = max(1.0, float(f["window_ms"]))
        for k in ("ev", "field", "axis"):
            f[k] = int(f[k])
        names.add(name)
        effects.append(f)
    return master, effects, errors, False


def field(d, fid):
    return d.get(fid)


def scalar(v, axis):
    if num(v):
        return float(v)
    if isinstance(v, list) and len(v) == 3 and all(num(x) for x in v):
        if axis >= 0:
            return float(v[axis])
        return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    return None


def vec(v):
    if isinstance(v, list) and len(v) == 3 and all(num(x) and math.isfinite(x) for x in v):
        return [float(x) for x in v]
    return None


class Engine:
    def __init__(self, master, effects):
        self.master, self.fx = master, effects
        self.st = [{"start": -1e9, "peak": 0.0, "lv": 0.0, "ltime": -1e9, "prev": None, "count": 0} for _ in effects]
        self.dt = 0.0
        self.prev_v = None
        self.lp = None
        self.chp = 0.0
        # shock state: recent (time, |ev3 id38|), latest external linear / rotational acceleration
        self.thrust = []
        self.thrust_keep = max([f["window_ms"] for f in effects if f["type"] == "shock"] + [100.0]) / 1000.0
        self.shock_lin, self.shock_rot = 0.0, 0.0
        self.latest = {}    # (ev, field) -> (raw value, time) for fields used by "require"
        self.req_keys = {(q["ev"], q["field"]) for f in effects for q in f["require"]}
        # seat (same rule as the plugin / bridge)
        self.role, self.role_t, self.left = -1, -1.0, False

    def seated(self, now):
        return (not self.left) and self.role_t >= 0 and now - self.role_t <= SEAT_WINDOW and self.role == 1

    def on_packet(self, ev, d, now):
        for (re_, rf) in self.req_keys:
            if re_ == ev and rf in d:
                self.latest[(re_, rf)] = (d[rf], now)
        if ev == 10:
            r = d.get(38)
            if num(r):
                self.role, self.role_t = int(r), now
                if self.role == 1:
                    self.left = False
        elif ev == 2:
            self.left = True
        elif ev == 0:
            x = d.get(93)
            if num(x) and 0 < x < 0.5:
                self.dt = float(x)
        if ev == 3:
            self.contact(d, now)
            self.shock(d, now)
        for f, s in zip(self.fx, self.st):
            if not f["enabled"] or f["type"] in ("contact", "shock") or f["ev"] != ev:
                continue
            if any(not num(d.get(k)) or abs(float(d.get(k)) - v) > 1e-6 for k, v in f["match"]):
                continue
            t = f["type"]
            c_ok = self.cond_ok(f, d)
            if t == "pulse":
                if not c_ok or not self.required(f, now):
                    continue
                k = 1.0
                if f["field"] >= 0:
                    x = scalar(d.get(f["field"]), f["axis"])
                    if x is None:
                        continue
                    k = 1.0 if f["scale"] == 0 else max(0.0, min(1.0, (x - f["offset"]) * f["scale"]))
                    if k <= 0:
                        continue
                s["start"], s["peak"] = now, f["gain"] * k
                s["count"] += 1
            elif t == "edge":
                if not c_ok:
                    continue
                x = scalar(d.get(f["field"]), f["axis"])
                if x is None:
                    continue
                if s["prev"] is not None and s["prev"] <= f["threshold"] < x and self.required(f, now):
                    s["start"], s["peak"] = now, f["gain"]
                    s["count"] += 1
                s["prev"] = x
            elif t == "level":
                x = scalar(d.get(f["field"]), f["axis"])
                if x is None:
                    continue
                if c_ok and f["min"] <= x <= f["max"]:
                    k = 1.0 if f["scale"] == 0 else max(0.0, min(1.0, (x - f["offset"]) * f["scale"]))
                else:
                    k = 0.0
                if k > 0 and self.value_raw(f, s, now) == 0:
                    s["count"] += 1          # count activations (0 -> on)
                s["lv"], s["ltime"] = f["gain"] * k, now

    def contact(self, d, now):
        v, w, a = vec(d.get(9)), vec(d.get(35)), vec(d.get(40))
        if v is None or w is None or a is None:
            return
        dt = self.dt
        if self.prev_v is not None and dt > 0:
            dvz = (v[2] - self.prev_v[2]) / dt
            crz = w[0] * v[1] - w[1] * v[0]
            cz = dvz + crz - a[2]
            if self.lp is None:
                self.lp = cz
            else:
                self.lp += (cz - self.lp) * min(1.0, dt / 0.5)
            self.chp = max(0.0, cz - self.lp)
            gear, eng = d.get(92), d.get(4)
            down = num(gear) and gear >= 0.99 and num(eng) and eng >= 0.5
            for f, s in zip(self.fx, self.st):
                if not f["enabled"] or f["type"] != "contact":
                    continue
                if down and self.chp > f["threshold"]:
                    p = f["gain"] * min(1.0, self.chp / f["full"])
                    if p > self.value_raw(f, s, now):
                        if self.value_raw(f, s, now) == 0:
                            s["count"] += 1
                        s["start"], s["peak"] = now, p
        self.prev_v = v

    def cond_ok(self, f, d):
        for c in f["cond"]:
            x = scalar(d.get(c["field"]), c["axis"])
            if x is None or not (c["min"] <= x <= c["max"]):
                return False
        return True

    def shock(self, d, now):
        a40, a38 = vec(d.get(40)), vec(d.get(38))
        w34, w41 = vec(d.get(34)), vec(d.get(41))
        if a38 is not None:
            self.thrust.append((now, math.sqrt(a38[0] ** 2 + a38[1] ** 2 + a38[2] ** 2)))
        self.thrust = [x for x in self.thrust if now - x[0] <= self.thrust_keep]
        # |id40| (None when id40 or id38 is missing: no linear part then)
        mag = math.sqrt(a40[0] ** 2 + a40[1] ** 2 + a40[2] ** 2) if a40 is not None and a38 is not None else None
        rot = math.sqrt(sum((w34[k] - w41[k]) ** 2 for k in range(3))) if w34 is not None and w41 is not None else 0.0
        self.shock_rot = rot
        self.shock_lin = max(0.0, mag - self.thrust_max(100.0, now)) if mag is not None else 0.0
        for f, s in zip(self.fx, self.st):
            if not f["enabled"] or f["type"] != "shock":
                continue
            ext = max(0.0, mag - self.thrust_max(f["window_ms"], now)) if mag is not None else 0.0
            if ext > f["threshold"] or rot > f["threshold_rot"]:
                if not self.required(f, now):
                    continue
                p = f["gain"] * min(1.0, max(ext / f["full"], rot / f["full_rot"]))
                cur = self.value_raw(f, s, now)
                if p > cur:
                    if cur == 0:
                        s["count"] += 1
                    s["start"], s["peak"] = now, p

    def thrust_max(self, window_ms, now):
        w = window_ms / 1000.0
        return max([m for t, m in self.thrust if now - t <= w] + [0.0])

    def value_raw(self, f, s, now):
        if f["type"] == "level":
            return s["lv"] if now - s["ltime"] < f["hold_ms"] / 1000.0 else 0.0
        el = now - s["start"]
        L = f["ms"] / 1000.0
        return s["peak"] * (1.0 - el / L) if 0 <= el < L else 0.0

    def value(self, i, now):
        f, s = self.fx[i], self.st[i]
        if not f["enabled"]:
            return 0.0
        st = self.seated(now)
        if f["when"] == "seated" and not st or f["when"] == "foot" and st:
            return 0.0
        if f["type"] not in TRIGGER_REQ and not self.required(f, now):
            return 0.0
        return self.value_raw(f, s, now)

    def required(self, f, now):
        for q in f["require"]:
            lv = self.latest.get((q["ev"], q["field"]))
            if lv is None or now - lv[1] > q["fresh_ms"] / 1000.0:
                return False
            x = scalar(lv[0], q["axis"])
            if x is None or not (q["min"] <= x <= q["max"]):
                return False
        return True

    def shaker(self, n, now, band=None):
        amp, freq = 0.0, 0.0
        for i, f in enumerate(self.fx):
            if n in f["shakers"] and (band is None or f["band"] == band):
                x = self.value(i, now)
                if x > amp:
                    amp, freq = x, f["freq"]
        return min(100.0, amp * self.master / 100.0), freq

    def motor(self, n, now):
        amp = 0.0
        for i, f in enumerate(self.fx):
            if n in f["motors"]:
                amp = max(amp, self.value(i, now))
        return min(100.0, amp * self.master / 100.0)


def load(path):
    with open(path) as fh:
        return parse_config(fh.read())


def check(path):
    master, fx, errors, fatal = load(path)
    print("config %s: %s, master %g, %d effects (sc_fx %s)" % (path, "FATAL" if fatal else "ok", master, len(fx), VERSION))
    for e in errors:
        print("  error: %s" % e)
    for f in fx:
        if f["type"] in ("contact", "shock"):
            src = "ev3 " + f["type"]
        else:
            src = "ev%d%s%s%s" % (f["ev"], "".join(" %d=%g" % kv for kv in f["match"]),
                                 (" field %d" % f["field"]) if f["field"] >= 0 else "",
                                 " +cond" if f["cond"] else "")
        print("  %-14s %-7s %-26s gain %5.1f freq %4.0f %-4s when %-6s shakers %-9s motors %s%s" % (
            f["name"], f["type"], src, f["gain"], f["freq"], f["band"], f["when"],
            ",".join(map(str, f["shakers"])) or "-", ",".join(map(str, f["motors"])) or "-",
            "" if f["enabled"] else "  (disabled)"))
    return 1 if fatal or errors else 0


def sim(cap, path):
    master, fx, errors, fatal = load(path)
    if fatal:
        sys.exit("config error: %s" % "; ".join(errors))
    for e in errors:
        print("config warning: %s" % e)
    evs = []
    with open(cap) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            e, t = o.get("ev"), o.get("t")
            if e == "mark":
                evs.append((float(t) if num(t) else None, "mark", o.get("label", "?")))
                continue
            if not isinstance(e, int) or not num(t):
                continue
            m, v = o.get("m") or [], o.get("v") or []
            evs.append((float(t), e, dict(zip(m, v)) if len(m) == len(v) else {}))
    evs = [x for x in evs if x[0] is not None]
    evs.sort(key=lambda x: x[0])
    if not evs:
        sys.exit("no events in %s" % cap)
    eng = Engine(master, fx)
    t0, step = evs[0][0], 0.01
    n = len(fx)
    mx, act = [0.0] * n, [0.0] * n
    smax = [0.0] * (MAX_SHAKERS + 1)
    mmax = [0.0] * (MAX_MOTORS + 1)
    bmax = {(s, b): 0.0 for s in range(1, MAX_SHAKERS + 1) for b in BANDS}
    chp_peaks = []          # (t, chp) local maxima > 10
    shock_peaks = []        # [t, lin, rot, seg] episodes with lin > 50 or rot > 5
    in_shock = False
    seg_order, seg_act, seg_thr = [], {}, {}
    above = False
    seg = "(pre)"
    nxt = t0
    for t, e, d in evs:
        while nxt <= t:       # sample outputs at 100 Hz like a consumer would
            if seg not in seg_act:
                seg_order.append(seg)
                seg_act[seg] = [0.0] * n
                seg_thr[seg] = 0.0
            for i in range(n):
                x = eng.value(i, nxt)
                mx[i] = max(mx[i], x)
                if x > 0:
                    act[i] += step
                    seg_act[seg][i] += step
            for s in range(1, MAX_SHAKERS + 1):
                smax[s] = max(smax[s], eng.shaker(s, nxt)[0])
                for b in BANDS:
                    bmax[(s, b)] = max(bmax[(s, b)], eng.shaker(s, nxt, b)[0])
            for s in range(1, MAX_MOTORS + 1):
                mmax[s] = max(mmax[s], eng.motor(s, nxt))
            nxt += step
        if e == "mark":
            seg = d
            continue
        eng.on_packet(e, d, t)
        if e == 3:
            th = scalar(d.get(38), -1)
            if th is not None and seg in seg_thr:
                seg_thr[seg] = max(seg_thr[seg], th)
            if eng.chp > 10 and not above:
                chp_peaks.append([t - t0, eng.chp, seg])
                above = True
            elif above and eng.chp > chp_peaks[-1][1]:
                chp_peaks[-1][1] = eng.chp
            elif eng.chp <= 10:
                above = False
            hot = eng.shock_lin > 50 or eng.shock_rot > 5
            if hot and not in_shock:
                shock_peaks.append([t - t0, eng.shock_lin, eng.shock_rot, seg])
                in_shock = True
            elif hot:
                shock_peaks[-1][1] = max(shock_peaks[-1][1], eng.shock_lin)
                shock_peaks[-1][2] = max(shock_peaks[-1][2], eng.shock_rot)
            else:
                in_shock = False
    dur = evs[-1][0] - t0
    print("OFFLINE effects sim of %s, %.1f s, config %s (master %g)" % (os.path.basename(cap), dur, path, master))
    print("%-14s %-7s %8s %8s %9s  %-9s %s" % ("effect", "type", "triggers", "max", "active s", "shakers", "motors"))
    for i, f in enumerate(fx):
        print("%-14s %-7s %8d %8.1f %9.2f  %-9s %s%s" % (f["name"], f["type"], eng.st[i]["count"], mx[i], act[i],
                                                     ",".join(map(str, f["shakers"])) or "-",
                                                     ",".join(map(str, f["motors"])) or "-",
                                                     "" if f["enabled"] else "  (disabled)"))
    used = [s for s in range(1, MAX_SHAKERS + 1) if any(s in f["shakers"] for f in fx)]
    print("shaker max amp: " + ("  ".join("S%d=%.1f (low %.1f, high %.1f)" % (s, smax[s], bmax[(s, "low")], bmax[(s, "high")]) for s in used) or "-"))
    mused = [s for s in range(1, MAX_MOTORS + 1) if any(s in f["motors"] for f in fx)]
    print("motor max amp: " + ("  ".join("M%d=%.1f" % (s, mmax[s]) for s in mused) or "-"))
    print("per segment: active seconds per effect (0 omitted), max thrust |ev3 id38| raw m/s2:")
    for sg in seg_order:
        parts = ["%s %.2f" % (fx[i]["name"], seg_act[sg][i]) for i in range(n) if seg_act[sg][i] > 0]
        print("  %-16s thrust %6.1f  %s" % (str(sg)[:16], seg_thr[sg], "  ".join(parts) or "-"))
    print("contact cHP episodes > 10 m/s2 (t s, peak, segment; gear/engine/seat not applied):")
    for t, p, sg in chp_peaks[:40]:
        print("  %8.2f  %6.1f  %s" % (t, p, sg))
    if not chp_peaks:
        print("  (none)")
    print("shock episodes (external linear > 50 m/s2 or rotational > 5; t s, linear peak, rotational peak, segment):")
    for t, li, ro, sg in shock_peaks[:40]:
        print("  %8.2f  %7.1f  %6.1f  %s" % (t, li, ro, sg))
    if not shock_peaks:
        print("  (none)")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["check"]:
        sys.exit(check(a[1] if len(a) > 1 else DEFAULT_CONFIG))
    if a[:1] == ["sim"] and len(a) >= 2:
        sim(a[1], a[2] if len(a) > 2 else DEFAULT_CONFIG)
        sys.exit(0)
    sys.exit(__doc__)
