// SC Haptics - SimHub plugin for Star Citizen.
// STAGE 2: listens for raw capture-server events on UDP 127.0.0.1:61701, runs the
// effects from effects.json (reloaded when the file changes) and publishes
//   SCHaptics.Shaker1..8.Amp / .Low / .High (0..100), .Freq (Hz)   -> ShakeIt Bass Shakers
//   SCHaptics.Motor1..8.Amp (0..100)                                -> ShakeIt Motors (0.3.0)
//   SCHaptics.Fx.<name> (0..100), SCHaptics.Contact.HP, SCHaptics.Shock.Lin / .Rot,
//   SCHaptics.Hit.*, SCHaptics.Heart.BPM, SCHaptics.Config.* and the stage-1 feed properties.
// 0.4.0: intensity sliders on the left-menu page (master + one per effect) write the numbers
// into effects.json in place (ConfigEdit.cs; a link stays a link, first write of a session
// keeps a backup in <folder of effects.json>\backup) and reload it at once.
// effects.json location (0.2.3): see SCHaptics.FindConfig (SC_HAPTICS_CONFIG, the Linux home
// under Wine, %USERPROFILE%\sc_haptics on Windows, then the 0.2.2 path).
// It only publishes properties. Nothing reaches the motion platform unless a SimHub
// profile binds these properties to motion (A2 first-run rules would apply).
//
// Update resilience: uses only the SDK surface shown in SimHub's own demo
// (IPlugin, IDataPlugin, IWPFSettingsV2, AttachDelegate, SimHub.Logging).
// The left-menu page (StatusPage.cs) is plain WPF built in code: no XAML, no MahApps.
using GameReaderCommon;
using SimHub.Plugins;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

namespace StarCitizenHaptics
{
    [PluginDescription("Star Citizen haptics from the capture server: configurable effects routed to up to 8 shaker channels (ShakeIt Bass Shakers) and 8 motor channels (ShakeIt Motors)")]
    [PluginAuthor("SC-Motion-Haptic-Bridge")]
    [PluginName("SC Haptics")]
    public class SCHaptics : IPlugin, IDataPlugin, IWPFSettingsV2
    {
        public const string Version = "0.4.0";
        public const int FeedPort = 61701;
        public const int NumEv = 20;           // ev 0..19 (telemetry schema)
        public const string ConfigEnv = "SC_HAPTICS_CONFIG";

        public PluginManager PluginManager { get; set; }

        ScFeed feed;
        string configSource = "";

        /// Where effects.json is read from (decided once, at SimHub start), and why.
        ///   1. the environment variable SC_HAPTICS_CONFIG, used as given
        ///   2. under Wine: the Linux home + \sc_haptics\effects.json (from WINEHOMEDIR or HOME, on Z:)
        ///   3. %USERPROFILE%\sc_haptics\effects.json (Windows; the Windows installer puts it there)
        /// (0.3.1: the fixed path of 0.2.2 is gone; under Wine, 2 is the same file.)
        /// Of 2 and 3 the first file that exists wins. If none exists, the first candidate is
        /// used (Wine: the Linux home; Windows: the user folder) and the status says "no file".
        static string FindConfig(out string how)
        {
            string env = System.Environment.GetEnvironmentVariable(ConfigEnv);
            if (!string.IsNullOrWhiteSpace(env))
            {
                how = "environment variable " + ConfigEnv;
                return env.Trim();
            }
            var cands = new List<KeyValuePair<string, string>>();
            if (IsWine())
            {
                AddUnixHome(cands, System.Environment.GetEnvironmentVariable("WINEHOMEDIR"), "Linux home (WINEHOMEDIR)");
                AddUnixHome(cands, System.Environment.GetEnvironmentVariable("HOME"), "Linux home (HOME)");
            }
            string profile = System.Environment.GetFolderPath(System.Environment.SpecialFolder.UserProfile);
            if (!string.IsNullOrEmpty(profile))
                cands.Add(new KeyValuePair<string, string>(System.IO.Path.Combine(profile, "sc_haptics", "effects.json"), "user folder"));
            if (cands.Count == 0)
            {
                how = "no home or user folder known; set " + ConfigEnv;
                return "effects.json";
            }
            foreach (KeyValuePair<string, string> c in cands)
            {
                try
                {
                    if (File.Exists(c.Key)) { how = c.Value; return c.Key; }
                }
                catch (Exception) { }
            }
            how = "no file found; expected location (" + cands[0].Value + ")";
            return cands[0].Key;
        }

        /// Adds (home)\sc_haptics\effects.json for a Linux home given as "/home/x" (HOME) or
        /// "\??\Z:\home\x" (WINEHOMEDIR). Z: is Wine's default drive for "/".
        static void AddUnixHome(List<KeyValuePair<string, string>> cands, string home, string how)
        {
            if (string.IsNullOrWhiteSpace(home)) return;
            string h = home.Trim();
            if (h.StartsWith(@"\??\", StringComparison.Ordinal)) h = h.Substring(4);
            else if (h.StartsWith("/", StringComparison.Ordinal)) h = "Z:" + h.Replace('/', '\\');
            if (h.Length < 3 || h[1] != ':') return;
            string p = h.TrimEnd('\\') + @"\sc_haptics\effects.json";
            foreach (KeyValuePair<string, string> c in cands)
                if (string.Equals(c.Key, p, StringComparison.OrdinalIgnoreCase)) return;
            cands.Add(new KeyValuePair<string, string>(p, how));
        }

        /// True inside a Wine prefix (Wine's own registry keys exist; real Windows has none).
        static bool IsWine()
        {
            try
            {
                using (Microsoft.Win32.RegistryKey k = Microsoft.Win32.Registry.LocalMachine.OpenSubKey(@"Software\Wine"))
                {
                    if (k != null) return true;
                }
                using (Microsoft.Win32.RegistryKey k = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(@"Software\Wine"))
                {
                    if (k != null) return true;
                }
            }
            catch (Exception) { }
            return !string.IsNullOrEmpty(System.Environment.GetEnvironmentVariable("WINEHOMEDIR"));
        }

        /// Feed of the current Init/End cycle (null between End and Init). Read by the status page.
        internal ScFeed CurrentFeed { get { return feed; } }

        /// Left menu icon (drawn in code, no resource file).
        public System.Windows.Media.ImageSource PictureIcon { get { return StatusPage.Icon; } }

        /// Left menu title.
        public string LeftMenuTitle { get { return "SC Haptics"; } }

        /// Read-only status page shown in the left menu / plugin settings.
        public System.Windows.Controls.Control GetWPFSettingsControl(PluginManager pluginManager)
        {
            return new StatusPage(this, pluginManager);
        }

        public void Init(PluginManager pluginManager)
        {
            SimHub.Logging.Current.Info("SC Haptics " + Version + ": starting, UDP 127.0.0.1:" + FeedPort);
            string how;
            string configPath = FindConfig(out how);
            configSource = how;
            SimHub.Logging.Current.Info("SC Haptics: effects file " + configPath + " (" + how + ")");
            feed = new ScFeed(FeedPort, configPath);
            ScFeed f = feed;
            SimHub.Logging.Current.Info("SC Haptics: config " + f.ConfigStatus);

            this.AttachDelegate(name: "Version", valueProvider: () => Version);
            this.AttachDelegate(name: "Status", valueProvider: () => f.Status);
            this.AttachDelegate(name: "Connected", valueProvider: () => f.Connected);
            this.AttachDelegate(name: "PacketsPerSec", valueProvider: () => f.PacketsPerSec);
            this.AttachDelegate(name: "PacketsTotal", valueProvider: () => f.PacketsTotal);
            this.AttachDelegate(name: "ParseErrors", valueProvider: () => f.ParseErrors);
            this.AttachDelegate(name: "LastEvent", valueProvider: () => f.LastEvent);
            this.AttachDelegate(name: "PlayerRole", valueProvider: () => f.PlayerRole);
            this.AttachDelegate(name: "Seated", valueProvider: () => f.Seated);
            this.AttachDelegate(name: "Ship", valueProvider: () => f.Ship);
            for (int i = 0; i < NumEv; i++)
            {
                int ev = i;
                this.AttachDelegate(name: "Ev" + ev.ToString("00") + ".Rate", valueProvider: () => f.EvRate(ev));
            }
            for (int n = 1; n <= FxConfig.MaxShakers; n++)
            {
                int sh = n;
                this.AttachDelegate(name: "Shaker" + sh + ".Amp", valueProvider: () => f.ShakerAmp(sh));
                this.AttachDelegate(name: "Shaker" + sh + ".Freq", valueProvider: () => f.ShakerFreq(sh));
                this.AttachDelegate(name: "Shaker" + sh + ".Low", valueProvider: () => f.ShakerBand(sh, 0));
                this.AttachDelegate(name: "Shaker" + sh + ".High", valueProvider: () => f.ShakerBand(sh, 1));
            }
            for (int n = 1; n <= FxConfig.MaxMotors; n++)
            {
                int mo = n;
                this.AttachDelegate(name: "Motor" + mo + ".Amp", valueProvider: () => f.MotorAmp(mo));
            }
            // one property per effect present at start; effects added later need a SimHub restart
            // for their Fx property (shaker routing of all effects updates live).
            foreach (string nm in f.EffectNames())
            {
                string fxName = nm;
                this.AttachDelegate(name: "Fx." + fxName, valueProvider: () => f.FxValue(fxName));
            }
            this.AttachDelegate(name: "Contact.HP", valueProvider: () => f.ContactHP);
            this.AttachDelegate(name: "Shock.Lin", valueProvider: () => f.ShockLin);
            this.AttachDelegate(name: "Shock.Rot", valueProvider: () => f.ShockRot);
            this.AttachDelegate(name: "Hit.Count", valueProvider: () => f.HitCount);
            this.AttachDelegate(name: "Hit.Kind", valueProvider: () => f.HitKind);
            this.AttachDelegate(name: "Hit.Damage", valueProvider: () => f.HitDamage);
            this.AttachDelegate(name: "Hit.Azimuth", valueProvider: () => f.HitAzimuth);
            this.AttachDelegate(name: "Hit.Elevation", valueProvider: () => f.HitElevation);
            this.AttachDelegate(name: "Hit.Age", valueProvider: () => f.HitAge);
            this.AttachDelegate(name: "Heart.BPM", valueProvider: () => f.HeartBPM);
            this.AttachDelegate(name: "Config.Status", valueProvider: () => f.ConfigStatus);
            this.AttachDelegate(name: "Config.Effects", valueProvider: () => f.EffectCount);
            string src = configSource;
            this.AttachDelegate(name: "Config.Path", valueProvider: () => f.ConfigPath);
            this.AttachDelegate(name: "Config.Source", valueProvider: () => src);
            feed.Start();
        }

        public void DataUpdate(PluginManager pluginManager, ref GameData data)
        {
            // Stage 1: nothing per frame. The feed runs on its own thread.
        }

        public void End(PluginManager pluginManager)
        {
            if (feed != null) feed.Stop();
            feed = null;
            SimHub.Logging.Current.Info("SC Haptics: stopped");
        }
    }

    /// UDP receiver + counters. Thread-safe getters for the properties.
    sealed class ScFeed
    {
        const double ConnectedWindow = 0.5;    // s without packets -> not connected
        const double SeatWindow = 0.25;        // same rule as the bridge (informational only)

        readonly int port;
        readonly string configPath;
        readonly FxEngine engine = new FxEngine();
        DateTime configStamp = DateTime.MinValue;
        string configStatus = "not loaded";
        List<string> configErrors = new List<string>();
        readonly Stopwatch clock = Stopwatch.StartNew();
        readonly object lk = new object();
        readonly object ioLock = new object();   // 0.4.0: effects.json read (CheckConfig) vs write (SetGain)
        bool backedUp;                           // 0.4.0: backup made before the first write of this session
        volatile bool stopping;
        Thread thread;
        Socket sock;
        System.Threading.Timer rateTimer;

        // guarded by lk
        long packets, parseErrors;
        readonly long[] evCount = new long[SCHaptics.NumEv];
        double lastRx = -1.0;
        int lastEvent = -1;
        int playerRole = -1;
        double roleTime = -1.0;
        bool leftVehicle;
        string ship = "";
        string status = "starting";
        // last ev7 Vehicle Impact (meanings Probable: id40 shield damage, id39 hull damage,
        // id42 azimuth rad, id43 elevation rad) and last ev14 heart rate
        long hitCount;
        int hitKind;               // 0 none yet, 1 shield, 2 hull
        double hitDamage, hitAz, hitEl, hitTime = -1.0;
        double heartBpm;

        // rates, updated once per second
        long ratePackets;
        readonly long[] rateEv = new long[SCHaptics.NumEv];
        double packetsPerSec;
        readonly double[] evPerSec = new double[SCHaptics.NumEv];
        double rateTime;

        public ScFeed(int port, string configPath)
        {
            this.port = port;
            this.configPath = configPath;
            CheckConfig();
        }

        /// Loads effects.json when its modification time changes. A file with a JSON
        /// error is reported and the previous good config stays active.
        void CheckConfig()
        {
            lock (ioLock) CheckConfigLocked();
        }

        void CheckConfigLocked()
        {
            try
            {
                if (!File.Exists(configPath))
                {
                    lock (lk) { configStatus = "no file " + configPath + " (no effects)"; configStamp = DateTime.MinValue; }
                    return;
                }
                DateTime stamp = File.GetLastWriteTimeUtc(configPath);
                lock (lk) { if (stamp == configStamp) return; }
                string text = File.ReadAllText(configPath);
                FxConfig c = FxConfig.Parse(text);
                string when = DateTime.Now.ToString("HH:mm:ss", System.Globalization.CultureInfo.InvariantCulture);
                lock (lk)
                {
                    configStamp = stamp;
                    configErrors = c.Errors;
                    if (c.Fatal)
                        configStatus = "ERROR at " + when + ", previous config kept: " + (c.Errors.Count > 0 ? c.Errors[0] : "?");
                    else
                    {
                        engine.SetConfig(c);
                        configStatus = "loaded " + when + ": " + c.Effects.Count + " effects, master " + c.Master
                                     + (c.Errors.Count > 0 ? ", " + c.Errors.Count + " warning(s)" : "");
                    }
                }
                SimHub.Logging.Current.Info("SC Haptics: config " + ConfigStatus);
            }
            catch (Exception e)
            {
                lock (lk) configStatus = "cannot read " + configPath + ": " + e.Message;
            }
        }

        bool SeatedAt(double now)   // call under lk
        {
            return !leftVehicle && roleTime >= 0 && now - roleTime <= SeatWindow && playerRole == 1;
        }

        double Now { get { return clock.Elapsed.TotalSeconds; } }

        public void Start()
        {
            rateTimer = new System.Threading.Timer(_ => UpdateRates(), null, 1000, 1000);
            thread = new Thread(Run) { IsBackground = true, Name = "SCHaptics feed" };
            thread.Start();
        }

        public void Stop()
        {
            stopping = true;
            try { if (rateTimer != null) rateTimer.Dispose(); } catch (Exception) { }
            try { Socket s = sock; if (s != null) s.Close(); } catch (Exception) { }
            try { if (thread != null) thread.Join(2000); } catch (Exception) { }
        }

        void SetStatus(string s) { lock (lk) status = s; }

        void Run()
        {
            byte[] buf = new byte[65536];
            while (!stopping)
            {
                // (re)bind; retries every 2 s if the port is busy
                try
                {
                    var s = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
                    s.ReceiveTimeout = 500;
                    s.Bind(new IPEndPoint(IPAddress.Loopback, port));
                    sock = s;
                    SetStatus("listening on 127.0.0.1:" + port);
                    SimHub.Logging.Current.Info("SC Haptics: listening on 127.0.0.1:" + port);
                }
                catch (Exception e)
                {
                    SetStatus("cannot bind UDP " + port + " (" + e.Message + "), retrying");
                    if (sock != null) { try { sock.Close(); } catch (Exception) { } sock = null; }
                    for (int k = 0; k < 20 && !stopping; k++) Thread.Sleep(100);
                    continue;
                }

                while (!stopping)
                {
                    int n;
                    try
                    {
                        n = sock.Receive(buf);
                    }
                    catch (SocketException e)
                    {
                        if (e.SocketErrorCode == SocketError.TimedOut || e.SocketErrorCode == SocketError.WouldBlock) continue;
                        if (stopping) break;
                        // e.g. ConnectionReset from a stray ICMP on Windows sockets: keep listening
                        if (e.SocketErrorCode == SocketError.ConnectionReset) continue;
                        SetStatus("receive error " + e.SocketErrorCode + ", rebinding");
                        break;
                    }
                    catch (ObjectDisposedException)
                    {
                        break;
                    }
                    if (n > 0) Handle(Encoding.UTF8.GetString(buf, 0, n));
                }
                try { if (sock != null) sock.Close(); } catch (Exception) { }
                sock = null;
            }
            SetStatus("stopped");
        }

        void Handle(string text)
        {
            double now = Now;
            Dictionary<string, object> pkt = null;
            try { pkt = MiniJson.Parse(text) as Dictionary<string, object>; }
            catch (Exception) { pkt = null; }

            lock (lk)
            {
                packets++;
                lastRx = now;
                if (pkt == null) { parseErrors++; return; }
                object evo;
                double evd;
                if (!pkt.TryGetValue("ev", out evo) || !MiniJson.TryNumber(evo, out evd)) { parseErrors++; return; }
                int ev = (int)evd;
                lastEvent = ev;
                if (ev >= 0 && ev < SCHaptics.NumEv) evCount[ev]++;
                try { engine.OnPacket(ev, pkt, now); } catch (Exception) { parseErrors++; }

                if (ev == 10)
                {
                    double r;
                    if (MiniJson.TryNumber(MiniJson.Field(pkt, 38), out r))
                    {
                        playerRole = (int)r;
                        roleTime = now;
                        if (playerRole == 1) leftVehicle = false;
                    }
                }
                else if (ev == 2)
                {
                    leftVehicle = true;
                }
                else if (ev == 1)
                {
                    object name = MiniJson.Field(pkt, 30);
                    if (name != null) ship = Convert.ToString(name, System.Globalization.CultureInfo.InvariantCulture);
                }
                else if (ev == 7)
                {
                    double sd, hd, az, el;
                    bool hs = MiniJson.TryNumber(MiniJson.Field(pkt, 40), out sd) && sd > 0;
                    bool hh = MiniJson.TryNumber(MiniJson.Field(pkt, 39), out hd) && hd > 0;
                    hitCount++;
                    hitTime = now;
                    hitKind = hh ? 2 : hs ? 1 : 0;
                    hitDamage = hh ? hd : hs ? sd : 0.0;
                    if (MiniJson.TryNumber(MiniJson.Field(pkt, 42), out az)) hitAz = az;
                    if (MiniJson.TryNumber(MiniJson.Field(pkt, 43), out el)) hitEl = el;
                }
                else if (ev == 14)
                {
                    double b;
                    if (MiniJson.TryNumber(MiniJson.Field(pkt, 38), out b)) heartBpm = b;
                }
            }
        }

        void UpdateRates()
        {
            try
            {
                double now = Now;
                lock (lk)
                {
                    double dt = now - rateTime;
                    if (dt <= 0) return;
                    packetsPerSec = (packets - ratePackets) / dt;
                    ratePackets = packets;
                    for (int i = 0; i < SCHaptics.NumEv; i++)
                    {
                        evPerSec[i] = (evCount[i] - rateEv[i]) / dt;
                        rateEv[i] = evCount[i];
                    }
                    rateTime = now;
                }
                CheckConfig();
            }
            catch (Exception) { }
        }

        // ---- property getters ----
        public string Status { get { lock (lk) return status; } }
        public bool Connected { get { lock (lk) return lastRx >= 0 && Now - lastRx < ConnectedWindow; } }
        public double PacketsPerSec { get { lock (lk) return Math.Round(packetsPerSec, 1); } }
        public long PacketsTotal { get { lock (lk) return packets; } }
        public long ParseErrors { get { lock (lk) return parseErrors; } }
        public int LastEvent { get { lock (lk) return lastEvent; } }
        public int PlayerRole { get { lock (lk) return playerRole; } }
        public string Ship { get { lock (lk) return ship; } }
        public bool Seated
        {
            get
            {
                lock (lk) return SeatedAt(Now);
            }
        }
        public double EvRate(int ev)
        {
            lock (lk) return Math.Round(evPerSec[ev], 1);
        }

        // ---- effects ----
        public string ConfigStatus { get { lock (lk) return configStatus; } }
        public string ConfigPath { get { return configPath; } }
        public List<string> ConfigErrors { get { lock (lk) return new List<string>(configErrors); } }
        public int EffectCount { get { lock (lk) return engine.Config.Effects.Count; } }
        public double ContactHP { get { lock (lk) return Math.Round(engine.Chp, 1); } }
        public double ShockLin { get { lock (lk) return Math.Round(engine.ShockLin, 1); } }
        public double ShockRot { get { lock (lk) return Math.Round(engine.ShockRot, 2); } }
        public double Master { get { lock (lk) return engine.Config.Master; } }

        /// 0.4.0, for the sliders: master first (name ""), then every loaded effect:
        /// (name, gain 0..100, enabled).
        public List<Tuple<string, double, bool>> Gains()
        {
            lock (lk)
            {
                var r = new List<Tuple<string, double, bool>>();
                r.Add(Tuple.Create("", engine.Config.Master, true));
                foreach (FxDef d in engine.Config.Effects) r.Add(Tuple.Create(d.Name, d.Gain, d.Enabled));
                return r;
            }
        }

        /// 0.4.0: writes master (effect "" or null) or one effect's gain into effects.json and
        /// reloads it at once. Never throws; returns the text for the status page. A refused or
        /// failed write leaves the file as it was.
        public string SetGain(string effect, double value)
        {
            string msg;
            try
            {
                lock (ioLock)
                {
                    string dir = Path.GetDirectoryName(configPath);
                    string bdir = Path.Combine(string.IsNullOrEmpty(dir) ? "." : dir, "backup");
                    msg = ConfigEdit.WriteGain(configPath, effect, value, bdir, ref backedUp);
                }
            }
            catch (Exception e)
            {
                msg = "NOT saved (" + (string.IsNullOrEmpty(effect) ? "master" : effect) + "): " + e.Message;
                SimHub.Logging.Current.Info("SC Haptics: " + msg);
                return msg;
            }
            lock (lk) configStamp = DateTime.MinValue;   // reload even if the file time did not move
            CheckConfig();
            SimHub.Logging.Current.Info("SC Haptics: " + msg);
            return msg;
        }

        public long HitCount { get { lock (lk) return hitCount; } }
        public int HitKind { get { lock (lk) return hitKind; } }
        public double HitDamage { get { lock (lk) return hitDamage; } }
        public double HitAzimuth { get { lock (lk) return Math.Round(hitAz, 3); } }
        public double HitElevation { get { lock (lk) return Math.Round(hitEl, 3); } }
        /// seconds since the last hit (-1 = none yet)
        public double HitAge { get { lock (lk) return hitTime < 0 ? -1.0 : Math.Round(Now - hitTime, 2); } }
        public double HeartBPM { get { lock (lk) return Math.Round(heartBpm, 1); } }

        public double MotorAmp(int n)
        {
            lock (lk) return Math.Round(engine.Motor(n, Now, SeatedAt(Now)), 1);
        }

        public List<string> EffectNames()
        {
            lock (lk)
            {
                var r = new List<string>();
                foreach (FxDef d in engine.Config.Effects) r.Add(d.Name);
                return r;
            }
        }

        public double FxValue(string name)
        {
            lock (lk) return Math.Round(engine.Value(engine.IndexOf(name), Now, SeatedAt(Now)), 1);
        }

        public double ShakerAmp(int n) { return ShakerBand(n, -1); }

        /// band: -1 = all, 0 = low, 1 = high
        public double ShakerBand(int n, int band)
        {
            double a, f;
            lock (lk) engine.Shaker(n, band, Now, SeatedAt(Now), out a, out f);
            return Math.Round(a, 1);
        }

        public double ShakerFreq(int n)
        {
            double a, f;
            lock (lk) engine.Shaker(n, -1, Now, SeatedAt(Now), out a, out f);
            return f;
        }

        static string ChannelList(int[] ch)
        {
            if (ch.Length == 0) return "-";
            var l = new List<string>();
            foreach (int x in ch) l.Add(x.ToString(System.Globalization.CultureInfo.InvariantCulture));
            return string.Join(",", l);
        }

        /// Rows for the status page: name, type, enabled, value, count, shakers, when, band, motors.
        public List<string[]> EffectRows()
        {
            lock (lk)
            {
                double now = Now;
                bool st = SeatedAt(now);
                var r = new List<string[]>();
                for (int i = 0; i < engine.Config.Effects.Count; i++)
                {
                    FxDef d = engine.Config.Effects[i];
                    r.Add(new string[] {
                        d.Name, d.Type.ToString().ToLowerInvariant(), d.Enabled ? "" : "(off)",
                        engine.Value(i, now, st).ToString("0.0", System.Globalization.CultureInfo.InvariantCulture),
                        engine.Count(i).ToString(System.Globalization.CultureInfo.InvariantCulture),
                        ChannelList(d.Shakers), d.When.ToString().ToLowerInvariant(),
                        d.Band == 0 ? "low" : "high", ChannelList(d.Motors) });
                }
                return r;
            }
        }
    }
}
