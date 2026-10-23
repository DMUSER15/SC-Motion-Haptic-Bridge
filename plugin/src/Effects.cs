// Effects - SC Haptics effect engine. Mirrors sc_fx.py (the reference / offline
// simulator) rule for rule; change both together.
// Effects come from effects.json (see sc_fx.py docstring for the format).
// 0.3.0: motors (ShakeIt Motors outputs), cond, field-scaled pulses, shock type,
// require checked at trigger time for pulse / edge / shock.
// Not thread-safe by itself: ScFeed calls every method under its lock.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace StarCitizenHaptics
{
    enum FxType { Pulse, Edge, Level, Contact, Shock }
    enum FxWhen { Any, Seated, Foot }

    sealed class FxDef
    {
        public string Name;
        public FxType Type;
        public bool Enabled = true;
        public int Ev = -1, Field = -1, Axis = -1;
        public double Gain = 50, Freq = 40, Ms = 100, Threshold = 0.5, Full = 90;
        public double Scale = 1, Offset = 0, Min = double.NegativeInfinity, Max = double.PositiveInfinity, HoldMs = 250;
        public double ThresholdRot = 10, FullRot = 150, WindowMs = 100;
        public FxWhen When = FxWhen.Any;
        public int Band;            // 0 = low, 1 = high
        public int[] Shakers = new int[0];
        public int[] Motors = new int[0];
        public List<KeyValuePair<int, double>> Match = new List<KeyValuePair<int, double>>();
        public List<FxReq> Require = new List<FxReq>();
        public List<FxCond> Cond = new List<FxCond>();

        /// pulse / edge / shock check "require" once, when they trigger; level / contact at output.
        public bool RequireAtTrigger { get { return Type == FxType.Pulse || Type == FxType.Edge || Type == FxType.Shock; } }
    }

    /// "cond" condition on the triggering post itself: value of Field (Axis) within Min..Max.
    sealed class FxCond
    {
        public int Field = -1, Axis = -1;
        public double Min = double.NegativeInfinity, Max = double.PositiveInfinity;
    }

    /// "require" condition: latest value of (Ev, Field) within Min..Max and not older than FreshMs.
    sealed class FxReq
    {
        public int Ev = -1, Field = -1, Axis = -1;
        public double Min = double.NegativeInfinity, Max = double.PositiveInfinity, FreshMs = 250;
        public long Key { get { return (long)Ev * 1000000L + Field; } }
    }

    sealed class FxConfig
    {
        public const int MaxShakers = 8;
        public const int MaxMotors = 8;
        public const double BandSplitHz = 40.0;
        public double Master = 100;
        public List<FxDef> Effects = new List<FxDef>();
        public List<string> Errors = new List<string>();
        public bool Fatal;

        static readonly HashSet<string> Known = new HashSet<string> {
            "name", "type", "enabled", "ev", "match", "field", "axis", "gain", "freq", "shakers", "when",
            "ms", "threshold", "full", "scale", "offset", "min", "max", "hold_ms", "note", "band", "require",
            "motors", "cond", "threshold_rot", "full_rot", "window_ms" };
        static readonly HashSet<string> ReqKnown = new HashSet<string> { "ev", "field", "axis", "min", "max", "fresh_ms" };
        static readonly HashSet<string> CondKnown = new HashSet<string> { "field", "axis", "min", "max" };

        /// parses a list of 1..max (null on error)
        static int[] Channels(Dictionary<string, object> e, string key, int max)
        {
            object so;
            var sh = new SortedSet<int>();
            if (e.TryGetValue(key, out so))
            {
                var sl = so as List<object>;
                if (sl == null) return null;
                foreach (object x in sl)
                {
                    double v;
                    if (!Num(x, out v) || v != Math.Floor(v) || v < 1 || v > max) return null;
                    sh.Add((int)v);
                }
            }
            var r = new int[sh.Count];
            sh.CopyTo(r);
            return r;
        }
        static readonly Regex NameRx = new Regex("^[A-Za-z0-9_]+$");
        static readonly Regex IdRx = new Regex("^[0-9]+$");

        static bool Num(object o, out double d)
        {
            if (o is double) { d = (double)o; return true; }
            d = 0;
            return false;
        }

        // reads key k as number; missing -> default; wrong type -> adds k to bad
        static double Get(Dictionary<string, object> e, string k, double def, List<string> bad)
        {
            object o;
            if (!e.TryGetValue(k, out o)) return def;
            double d;
            if (Num(o, out d)) return d;
            bad.Add(k);
            return def;
        }

        public static FxConfig Parse(string text)
        {
            var c = new FxConfig();
            object root;
            try { root = MiniJson.Parse(text); }
            catch (Exception ex) { c.Errors.Add("JSON: " + ex.Message); c.Fatal = true; return c; }
            var d = root as Dictionary<string, object>;
            if (d == null) { c.Errors.Add("top level must be an object"); c.Fatal = true; return c; }

            object mo;
            if (d.TryGetValue("master", out mo))
            {
                double m;
                if (Num(mo, out m)) c.Master = m; else c.Errors.Add("master must be a number");
            }
            c.Master = Math.Max(0, Math.Min(100, c.Master));

            object lo;
            List<object> lst = null;
            if (d.TryGetValue("effects", out lo))
            {
                lst = lo as List<object>;
                if (lst == null) { c.Errors.Add("effects must be a list"); c.Fatal = true; return c; }
            }
            if (lst == null) return c;

            var names = new HashSet<string>();
            for (int i = 0; i < lst.Count; i++)
            {
                string where = "effect " + (i + 1);
                var e = lst[i] as Dictionary<string, object>;
                if (e == null) { c.Errors.Add(where + ": not an object"); continue; }
                object no;
                string name = e.TryGetValue("name", out no) ? no as string : null;
                if (name == null || !NameRx.IsMatch(name)) { c.Errors.Add(where + ": name missing or not [A-Za-z0-9_]"); continue; }
                where = "effect '" + name + "'";
                if (names.Contains(name)) { c.Errors.Add(where + ": duplicate name"); continue; }
                foreach (string k in e.Keys)
                    if (!Known.Contains(k)) c.Errors.Add(where + ": unknown key '" + k + "' (ignored)");

                object to;
                string t = e.TryGetValue("type", out to) ? to as string : null;
                var f = new FxDef { Name = name };
                if (t == "pulse") f.Type = FxType.Pulse;
                else if (t == "edge") f.Type = FxType.Edge;
                else if (t == "level") f.Type = FxType.Level;
                else if (t == "contact") f.Type = FxType.Contact;
                else if (t == "shock") f.Type = FxType.Shock;
                else { c.Errors.Add(where + ": type must be one of pulse/edge/level/contact/shock"); continue; }
                bool contact = f.Type == FxType.Contact;
                bool shock = f.Type == FxType.Shock;

                object eo;
                f.Enabled = !(e.TryGetValue("enabled", out eo) && eo is bool && !(bool)eo);

                var bad = new List<string>();
                f.Ev = (int)Get(e, "ev", -1, bad);
                f.Field = (int)Get(e, "field", -1, bad);
                f.Axis = (int)Get(e, "axis", -1, bad);
                f.Gain = Get(e, "gain", 50, bad);
                f.Freq = Get(e, "freq", 40, bad);
                f.Ms = Get(e, "ms", 100, bad);
                f.Threshold = Get(e, "threshold", contact ? 20 : shock ? 300 : 0.5, bad);
                f.Full = Get(e, "full", shock ? 1000 : 90, bad);
                f.Scale = Get(e, "scale", 1, bad);
                f.Offset = Get(e, "offset", 0, bad);
                f.Min = Get(e, "min", double.NegativeInfinity, bad);
                f.Max = Get(e, "max", double.PositiveInfinity, bad);
                f.HoldMs = Get(e, "hold_ms", 250, bad);
                f.ThresholdRot = Get(e, "threshold_rot", 10, bad);
                f.FullRot = Get(e, "full_rot", 150, bad);
                f.WindowMs = Get(e, "window_ms", 100, bad);
                if (bad.Count > 0) { c.Errors.Add(where + ": " + string.Join(", ", bad) + " must be numbers"); continue; }

                object bo;
                string band = e.TryGetValue("band", out bo) ? bo as string : (f.Freq < BandSplitHz ? "low" : "high");
                if (band == "low") f.Band = 0;
                else if (band == "high") f.Band = 1;
                else { c.Errors.Add(where + ": band must be low or high"); continue; }

                object wo;
                string when = e.TryGetValue("when", out wo) ? wo as string : (contact ? "seated" : "any");
                if (when == "any") f.When = FxWhen.Any;
                else if (when == "seated") f.When = FxWhen.Seated;
                else if (when == "foot") f.When = FxWhen.Foot;
                else { c.Errors.Add(where + ": when must be any/seated/foot"); continue; }

                if (!contact && !shock && f.Ev < 0) { c.Errors.Add(where + ": ev required"); continue; }
                if ((f.Type == FxType.Edge || f.Type == FxType.Level) && f.Field < 0) { c.Errors.Add(where + ": field required for " + t); continue; }
                if (f.Axis < -1 || f.Axis > 2) { c.Errors.Add(where + ": axis must be 0, 1 or 2"); continue; }

                int[] shk = Channels(e, "shakers", MaxShakers);
                if (shk == null) { c.Errors.Add(where + ": shakers must be a list of 1.." + MaxShakers); continue; }
                f.Shakers = shk;
                int[] mot = Channels(e, "motors", MaxMotors);
                if (mot == null) { c.Errors.Add(where + ": motors must be a list of 1.." + MaxMotors); continue; }
                f.Motors = mot;

                object co;
                bool cOk = true;
                if (e.TryGetValue("cond", out co))
                {
                    var cl = co as List<object>;
                    if (cl == null) cOk = false;
                    else
                        foreach (object x in cl)
                        {
                            var cd = x as Dictionary<string, object>;
                            if (cd == null) { cOk = false; break; }
                            foreach (string k in cd.Keys) if (!CondKnown.Contains(k)) cOk = false;
                            if (!cOk) break;
                            var cbad = new List<string>();
                            var q = new FxCond
                            {
                                Field = (int)Get(cd, "field", -1, cbad),
                                Axis = (int)Get(cd, "axis", -1, cbad),
                                Min = Get(cd, "min", double.NegativeInfinity, cbad),
                                Max = Get(cd, "max", double.PositiveInfinity, cbad)
                            };
                            if (cbad.Count > 0 || q.Field < 0 || q.Axis < -1 || q.Axis > 2) { cOk = false; break; }
                            f.Cond.Add(q);
                        }
                }
                if (!cOk) { c.Errors.Add(where + ": cond must be a list of {field, axis?, min?, max?}"); continue; }

                object mto;
                bool mOk = true;
                if (e.TryGetValue("match", out mto))
                {
                    var md = mto as Dictionary<string, object>;
                    if (md == null) mOk = false;
                    else
                        foreach (var kv in md)
                        {
                            double v;
                            if (!IdRx.IsMatch(kv.Key) || !Num(kv.Value, out v)) { mOk = false; break; }
                            f.Match.Add(new KeyValuePair<int, double>(int.Parse(kv.Key, CultureInfo.InvariantCulture), v));
                        }
                }
                if (!mOk) { c.Errors.Add(where + ": match must be {\"id\": number}"); continue; }

                object ro;
                bool rOk = true;
                if (e.TryGetValue("require", out ro))
                {
                    var rl = ro as List<object>;
                    if (rl == null) rOk = false;
                    else
                        foreach (object x in rl)
                        {
                            var rd = x as Dictionary<string, object>;
                            if (rd == null) { rOk = false; break; }
                            foreach (string k in rd.Keys) if (!ReqKnown.Contains(k)) rOk = false;
                            if (!rOk) break;
                            var rbad = new List<string>();
                            var q = new FxReq
                            {
                                Ev = (int)Get(rd, "ev", -1, rbad),
                                Field = (int)Get(rd, "field", -1, rbad),
                                Axis = (int)Get(rd, "axis", -1, rbad),
                                Min = Get(rd, "min", double.NegativeInfinity, rbad),
                                Max = Get(rd, "max", double.PositiveInfinity, rbad),
                                FreshMs = Get(rd, "fresh_ms", 250, rbad)
                            };
                            if (rbad.Count > 0 || q.Ev < 0 || q.Field < 0 || q.Axis < -1 || q.Axis > 2) { rOk = false; break; }
                            q.FreshMs = Math.Max(1, q.FreshMs);
                            f.Require.Add(q);
                        }
                }
                if (!rOk) { c.Errors.Add(where + ": require must be a list of {ev, field, axis?, min?, max?, fresh_ms?}"); continue; }

                f.Gain = Math.Max(0, Math.Min(100, f.Gain));
                f.Ms = Math.Max(1, f.Ms);
                f.HoldMs = Math.Max(1, f.HoldMs);
                f.Full = Math.Max(1e-6, f.Full);
                f.FullRot = Math.Max(1e-6, f.FullRot);
                f.WindowMs = Math.Max(1, f.WindowMs);
                names.Add(name);
                c.Effects.Add(f);
            }
            return c;
        }
    }

    sealed class FxState
    {
        public double Start = -1e9, Peak, Lv, LTime = -1e9;
        public double Prev = double.NaN;
        public long Count;
    }

    sealed class FxEngine
    {
        FxConfig cfg = new FxConfig();
        FxState[] st = new FxState[0];

        // latest raw value + time of fields used by "require", keyed by FxReq.Key
        readonly Dictionary<long, object> latestVal = new Dictionary<long, object>();
        readonly Dictionary<long, double> latestTime = new Dictionary<long, double>();
        List<FxReq> reqs = new List<FxReq>();

        // contact state
        double dt;
        double[] prevV;
        bool lpInit;
        double lp;
        public double Chp;          // current contact high-pass, m/s2 (for tuning)

        // shock state: recent (time, |ev3 id38|); latest external linear / rotational acceleration
        readonly List<KeyValuePair<double, double>> thrust = new List<KeyValuePair<double, double>>();
        double thrustKeep = 0.1;
        public double ShockLin, ShockRot;

        public FxConfig Config { get { return cfg; } }

        public void SetConfig(FxConfig c)
        {
            cfg = c;
            st = new FxState[c.Effects.Count];
            for (int i = 0; i < st.Length; i++) st[i] = new FxState();
            reqs = new List<FxReq>();
            double keep = 100;
            foreach (FxDef d in c.Effects)
            {
                reqs.AddRange(d.Require);
                if (d.Type == FxType.Shock) keep = Math.Max(keep, d.WindowMs);
            }
            thrustKeep = keep / 1000.0;
            latestVal.Clear();
            latestTime.Clear();
        }

        public int IndexOf(string name)
        {
            for (int i = 0; i < cfg.Effects.Count; i++) if (cfg.Effects[i].Name == name) return i;
            return -1;
        }

        public long Count(int i) { return i >= 0 && i < st.Length ? st[i].Count : 0; }

        bool Required(FxDef f, double now)
        {
            foreach (FxReq q in f.Require)
            {
                object v;
                double t, x;
                if (!latestVal.TryGetValue(q.Key, out v) || !latestTime.TryGetValue(q.Key, out t)) return false;
                if (now - t > q.FreshMs / 1000.0) return false;
                if (!Scalar(v, q.Axis, out x) || !(q.Min <= x && x <= q.Max)) return false;
            }
            return true;
        }

        static bool Scalar(object v, int axis, out double x)
        {
            x = 0;
            if (v is double) { x = (double)v; return true; }
            var l = v as List<object>;
            if (l == null || l.Count != 3 || !(l[0] is double) || !(l[1] is double) || !(l[2] is double)) return false;
            double a = (double)l[0], b = (double)l[1], c = (double)l[2];
            x = axis == 0 ? a : axis == 1 ? b : axis == 2 ? c : Math.Sqrt(a * a + b * b + c * c);
            return true;
        }

        static double[] Vec(object v)
        {
            var l = v as List<object>;
            if (l == null || l.Count != 3) return null;
            var r = new double[3];
            for (int k = 0; k < 3; k++)
            {
                if (!(l[k] is double)) return null;
                r[k] = (double)l[k];
                if (double.IsNaN(r[k]) || double.IsInfinity(r[k])) return null;
            }
            return r;
        }

        public void OnPacket(int ev, Dictionary<string, object> pkt, double now)
        {
            foreach (FxReq q in reqs)
            {
                if (q.Ev != ev) continue;
                object v = MiniJson.Field(pkt, q.Field);
                if (v == null) continue;
                latestVal[q.Key] = v;
                latestTime[q.Key] = now;
            }
            if (ev == 0)
            {
                double x;
                if (MiniJson.TryNumber(MiniJson.Field(pkt, 93), out x) && x > 0 && x < 0.5) dt = x;
            }
            if (ev == 3) { Contact(pkt, now); Shock(pkt, now); }

            for (int i = 0; i < cfg.Effects.Count; i++)
            {
                FxDef f = cfg.Effects[i];
                FxState s = st[i];
                if (!f.Enabled || f.Type == FxType.Contact || f.Type == FxType.Shock || f.Ev != ev) continue;
                bool ok = true;
                foreach (var kv in f.Match)
                {
                    double v;
                    if (!MiniJson.TryNumber(MiniJson.Field(pkt, kv.Key), out v) || Math.Abs(v - kv.Value) > 1e-6) { ok = false; break; }
                }
                if (!ok) continue;
                bool cOk = CondOk(f, pkt);

                if (f.Type == FxType.Pulse)
                {
                    if (!cOk || !Required(f, now)) continue;
                    double k = 1.0;
                    if (f.Field >= 0)
                    {
                        double x;
                        if (!Scalar(MiniJson.Field(pkt, f.Field), f.Axis, out x)) continue;
                        k = f.Scale == 0 ? 1.0 : Math.Max(0.0, Math.Min(1.0, (x - f.Offset) * f.Scale));
                        if (k <= 0) continue;
                    }
                    s.Start = now; s.Peak = f.Gain * k; s.Count++;
                }
                else if (f.Type == FxType.Edge)
                {
                    if (!cOk) continue;
                    double x;
                    if (!Scalar(MiniJson.Field(pkt, f.Field), f.Axis, out x)) continue;
                    if (!double.IsNaN(s.Prev) && s.Prev <= f.Threshold && f.Threshold < x && Required(f, now))
                    {
                        s.Start = now; s.Peak = f.Gain; s.Count++;
                    }
                    s.Prev = x;
                }
                else // Level
                {
                    double x;
                    if (!Scalar(MiniJson.Field(pkt, f.Field), f.Axis, out x)) continue;
                    double k = 0;
                    if (cOk && f.Min <= x && x <= f.Max)
                        k = f.Scale == 0 ? 1.0 : Math.Max(0.0, Math.Min(1.0, (x - f.Offset) * f.Scale));
                    if (k > 0 && Raw(f, s, now) == 0) s.Count++;   // count activations (0 -> on)
                    s.Lv = f.Gain * k;
                    s.LTime = now;
                }
            }
        }

        void Contact(Dictionary<string, object> pkt, double now)
        {
            double[] v = Vec(MiniJson.Field(pkt, 9));
            double[] w = Vec(MiniJson.Field(pkt, 35));
            double[] a = Vec(MiniJson.Field(pkt, 40));
            if (v == null || w == null || a == null) return;
            if (prevV != null && dt > 0)
            {
                double dvz = (v[2] - prevV[2]) / dt;
                double crz = w[0] * v[1] - w[1] * v[0];
                double cz = dvz + crz - a[2];
                if (!lpInit) { lp = cz; lpInit = true; }
                else lp += (cz - lp) * Math.Min(1.0, dt / 0.5);
                Chp = Math.Max(0.0, cz - lp);
                double gear, eng;
                bool down = MiniJson.TryNumber(MiniJson.Field(pkt, 92), out gear) && gear >= 0.99
                         && MiniJson.TryNumber(MiniJson.Field(pkt, 4), out eng) && eng >= 0.5;
                for (int i = 0; i < cfg.Effects.Count; i++)
                {
                    FxDef f = cfg.Effects[i];
                    FxState s = st[i];
                    if (!f.Enabled || f.Type != FxType.Contact) continue;
                    if (down && Chp > f.Threshold)
                    {
                        double p = f.Gain * Math.Min(1.0, Chp / f.Full);
                        double cur = Raw(f, s, now);
                        if (p > cur)
                        {
                            if (cur == 0) s.Count++;
                            s.Start = now; s.Peak = p;
                        }
                    }
                }
            }
            prevV = v;
        }

        static bool CondOk(FxDef f, Dictionary<string, object> pkt)
        {
            foreach (FxCond q in f.Cond)
            {
                double x;
                if (!Scalar(MiniJson.Field(pkt, q.Field), q.Axis, out x) || !(q.Min <= x && x <= q.Max)) return false;
            }
            return true;
        }

        static double Norm(double[] a) { return Math.Sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]); }

        double ThrustMax(double windowMs, double now)
        {
            double w = windowMs / 1000.0, m = 0.0;
            foreach (var kv in thrust) if (now - kv.Key <= w && kv.Value > m) m = kv.Value;
            return m;
        }

        /// External acceleration (mirrors sc_fx.py Engine.shock): linear = |id40| - max |id38| over
        /// the effect's window_ms, rotational = |id34 - id41|.
        void Shock(Dictionary<string, object> pkt, double now)
        {
            double[] a40 = Vec(MiniJson.Field(pkt, 40));
            double[] a38 = Vec(MiniJson.Field(pkt, 38));
            double[] w34 = Vec(MiniJson.Field(pkt, 34));
            double[] w41 = Vec(MiniJson.Field(pkt, 41));
            if (a38 != null) thrust.Add(new KeyValuePair<double, double>(now, Norm(a38)));
            thrust.RemoveAll(kv => now - kv.Key > thrustKeep);
            bool lin = a40 != null && a38 != null;
            double mag = lin ? Norm(a40) : 0.0;
            double rot = 0.0;
            if (w34 != null && w41 != null)
                rot = Norm(new double[] { w34[0] - w41[0], w34[1] - w41[1], w34[2] - w41[2] });
            ShockRot = rot;
            ShockLin = lin ? Math.Max(0.0, mag - ThrustMax(100.0, now)) : 0.0;
            for (int i = 0; i < cfg.Effects.Count; i++)
            {
                FxDef f = cfg.Effects[i];
                FxState s = st[i];
                if (!f.Enabled || f.Type != FxType.Shock) continue;
                double ext = lin ? Math.Max(0.0, mag - ThrustMax(f.WindowMs, now)) : 0.0;
                if (ext > f.Threshold || rot > f.ThresholdRot)
                {
                    if (!Required(f, now)) continue;
                    double p = f.Gain * Math.Min(1.0, Math.Max(ext / f.Full, rot / f.FullRot));
                    double cur = Raw(f, s, now);
                    if (p > cur)
                    {
                        if (cur == 0) s.Count++;
                        s.Start = now; s.Peak = p;
                    }
                }
            }
        }

        static double Raw(FxDef f, FxState s, double now)
        {
            if (f.Type == FxType.Level) return now - s.LTime < f.HoldMs / 1000.0 ? s.Lv : 0.0;
            double el = now - s.Start, L = f.Ms / 1000.0;
            return el >= 0 && el < L ? s.Peak * (1.0 - el / L) : 0.0;
        }

        public double Value(int i, double now, bool seated)
        {
            if (i < 0 || i >= st.Length) return 0;
            FxDef f = cfg.Effects[i];
            if (!f.Enabled) return 0;
            if (f.When == FxWhen.Seated && !seated || f.When == FxWhen.Foot && seated) return 0;
            if (!f.RequireAtTrigger && !Required(f, now)) return 0;
            return Raw(f, st[i], now);
        }

        /// ShakeIt Motors channel n: max of the effects routed to it, x master.
        public double Motor(int n, double now, bool seated)
        {
            double amp = 0;
            for (int i = 0; i < cfg.Effects.Count; i++)
            {
                if (Array.IndexOf(cfg.Effects[i].Motors, n) < 0) continue;
                double x = Value(i, now, seated);
                if (x > amp) amp = x;
            }
            return Math.Min(100.0, amp * cfg.Master / 100.0);
        }

        /// band: -1 = all, 0 = low, 1 = high
        public void Shaker(int n, int band, double now, bool seated, out double amp, out double freq)
        {
            amp = 0; freq = 0;
            for (int i = 0; i < cfg.Effects.Count; i++)
            {
                if (Array.IndexOf(cfg.Effects[i].Shakers, n) < 0) continue;
                if (band >= 0 && cfg.Effects[i].Band != band) continue;
                double x = Value(i, now, seated);
                if (x > amp) { amp = x; freq = cfg.Effects[i].Freq; }
            }
            amp = Math.Min(100.0, amp * cfg.Master / 100.0);
        }
    }
}
