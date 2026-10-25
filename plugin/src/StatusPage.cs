// StatusPage - left-menu page for SC Haptics.
// Plain WPF built in code (no XAML, no MahApps, no resources) so SimHub UI
// library updates cannot break it. Refreshes 4x per second while visible.
// 0.4.0: intensity sliders (master + one per effect) above the read-only status. A change is
// saved to effects.json about half a second after the last move (after the mouse is let go),
// through ScFeed.SetGain (ConfigEdit: only that number changes). Nothing else is editable here.
using SimHub.Plugins;
using System;
using System.Collections.Generic;
using System.Text;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;

namespace StarCitizenHaptics
{
    public class StatusPage : UserControl
    {
        static ImageSource icon;

        /// 24x24 icon: two concentric rings (a shaker), white so it suits SimHub's menu.
        public static ImageSource Icon
        {
            get
            {
                if (icon == null)
                {
                    var g = new GeometryGroup();
                    g.Children.Add(new EllipseGeometry(new Point(12, 12), 10, 10));
                    g.Children.Add(new EllipseGeometry(new Point(12, 12), 5, 5));
                    g.Children.Add(new EllipseGeometry(new Point(12, 12), 1.5, 1.5));
                    var d = new GeometryDrawing(null, new Pen(Brushes.White, 2), g);
                    var img = new DrawingImage(d);
                    img.Freeze();
                    icon = img;
                }
                return icon;
            }
        }

        readonly SCHaptics plugin;
        readonly PluginManager pm;
        readonly TextBlock text;
        readonly DispatcherTimer timer;

        // 0.4.0 intensity sliders; key "" = master
        readonly StackPanel gainRows = new StackPanel();
        readonly TextBlock gainStatus;
        readonly DispatcherTimer saveTimer;
        readonly Dictionary<string, Slider> sliders = new Dictionary<string, Slider>();
        readonly Dictionary<string, TextBlock> valueText = new Dictionary<string, TextBlock>();
        readonly Dictionary<string, TextBlock> offText = new Dictionary<string, TextBlock>();
        readonly Dictionary<string, double> pending = new Dictionary<string, double>();
        string rowsFor;          // effect names the rows were built for
        bool updating;           // true while Refresh moves a slider (not a user change)

        public StatusPage(SCHaptics plugin, PluginManager pm)
        {
            this.plugin = plugin;
            this.pm = pm;
            text = new TextBlock
            {
                FontFamily = new FontFamily("Consolas, Courier New, monospace"),
                FontSize = 13,
                Margin = new Thickness(16),
                TextWrapping = TextWrapping.NoWrap
            };
            gainStatus = new TextBlock { Margin = new Thickness(0, 6, 0, 0), TextWrapping = TextWrapping.Wrap };
            var intensity = new StackPanel { Margin = new Thickness(16, 16, 16, 0) };
            intensity.Children.Add(new TextBlock
            {
                Text = "Intensity (0..100; saved to effects.json half a second after you let go; 0 = silent)",
                FontWeight = FontWeights.Bold,
                TextWrapping = TextWrapping.Wrap,
                Margin = new Thickness(0, 0, 0, 6)
            });
            intensity.Children.Add(gainRows);
            intensity.Children.Add(gainStatus);
            var page = new StackPanel();
            page.Children.Add(intensity);
            page.Children.Add(text);
            Content = new ScrollViewer
            {
                Content = page,
                HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto
            };
            timer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(250) };
            timer.Tick += (s, e) => Refresh();
            saveTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(500) };
            saveTimer.Tick += (s, e) => SavePending(false);
            Loaded += (s, e) => { Refresh(); timer.Start(); };
            Unloaded += (s, e) => { timer.Stop(); SavePending(true); };
        }

        static string Fmt(double v)
        {
            return v.ToString("0.#", System.Globalization.CultureInfo.InvariantCulture);
        }

        /// One row per gain: label, slider 0..100, value, "(off)" for a disabled effect.
        void BuildRows(List<Tuple<string, double, bool>> gains)
        {
            gainRows.Children.Clear();
            sliders.Clear(); valueText.Clear(); offText.Clear();
            foreach (Tuple<string, double, bool> g in gains)
            {
                string key = g.Item1;
                var row = new Grid { Margin = new Thickness(0, 1, 0, 1) };
                row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(170) });
                row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(320) });
                row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(50) });
                row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                var label = new TextBlock
                {
                    Text = key == "" ? "MASTER (all effects)" : key,
                    FontWeight = key == "" ? FontWeights.Bold : FontWeights.Normal,
                    VerticalAlignment = VerticalAlignment.Center
                };
                var sl = new Slider
                {
                    Minimum = 0, Maximum = 100, SmallChange = 1, LargeChange = 5,
                    TickFrequency = 1, IsSnapToTickEnabled = true,
                    Value = g.Item2,
                    VerticalAlignment = VerticalAlignment.Center,
                    Margin = new Thickness(0, 0, 8, 0)
                };
                var val = new TextBlock { Text = Fmt(g.Item2), VerticalAlignment = VerticalAlignment.Center };
                var off = new TextBlock { Text = g.Item3 ? "" : "(off in effects.json)", VerticalAlignment = VerticalAlignment.Center };
                sl.ValueChanged += (s, e) => OnSlider(key, e.NewValue);
                Grid.SetColumn(label, 0); Grid.SetColumn(sl, 1); Grid.SetColumn(val, 2); Grid.SetColumn(off, 3);
                row.Children.Add(label); row.Children.Add(sl); row.Children.Add(val); row.Children.Add(off);
                gainRows.Children.Add(row);
                sliders[key] = sl; valueText[key] = val; offText[key] = off;
            }
        }

        void OnSlider(string key, double v)
        {
            if (updating) return;
            pending[key] = v;
            TextBlock t;
            if (valueText.TryGetValue(key, out t)) t.Text = Fmt(v);
            gainStatus.Text = "changed, saving ...";
            saveTimer.Stop();
            saveTimer.Start();
        }

        /// Writes the pending slider values. Waits while a slider is still held (unless force).
        void SavePending(bool force)
        {
            saveTimer.Stop();
            if (pending.Count == 0) return;
            if (!force)
                foreach (Slider s in sliders.Values)
                    if (s.IsMouseCaptureWithin) { saveTimer.Start(); return; }
            ScFeed f = plugin.CurrentFeed;
            if (f == null)
            {
                gainStatus.Text = "plugin not running: NOT saved";
                pending.Clear();
                return;
            }
            var msgs = new List<string>();
            foreach (string k in new List<string>(pending.Keys)) msgs.Add(f.SetGain(k, pending[k]));
            pending.Clear();
            gainStatus.Text = DateTime.Now.ToString("HH:mm:ss", System.Globalization.CultureInfo.InvariantCulture)
                + "  " + string.Join("; ", msgs) + "   (backup of the first change: backup folder next to effects.json)";
        }

        /// Builds the rows when the effect list changes; otherwise moves sliders to the loaded
        /// values (a hand edit of effects.json shows up here), except the ones being changed.
        void RefreshGains(ScFeed f)
        {
            if (f == null)
            {
                gainRows.IsEnabled = false;
                return;
            }
            gainRows.IsEnabled = true;
            List<Tuple<string, double, bool>> gains = f.Gains();
            var names = new StringBuilder();
            foreach (Tuple<string, double, bool> g in gains) names.Append(g.Item1).Append('|');
            if (names.ToString() != rowsFor)
            {
                if (pending.Count > 0) return;   // save first, rebuild on the next tick
                BuildRows(gains);
                rowsFor = names.ToString();
                return;
            }
            updating = true;
            try
            {
                foreach (Tuple<string, double, bool> g in gains)
                {
                    Slider sl;
                    if (!sliders.TryGetValue(g.Item1, out sl)) continue;
                    offText[g.Item1].Text = g.Item3 ? "" : "(off in effects.json)";
                    if (pending.ContainsKey(g.Item1) || sl.IsMouseCaptureWithin) continue;
                    if (Math.Abs(sl.Value - g.Item2) > 1e-9)
                    {
                        sl.Value = g.Item2;
                        valueText[g.Item1].Text = Fmt(g.Item2);
                    }
                }
            }
            finally { updating = false; }
        }

        string PropCheck(string name)
        {
            try
            {
                object v = pm == null ? null : pm.GetPropertyValue(name);
                return v == null ? "(not found)" : Convert.ToString(v, System.Globalization.CultureInfo.InvariantCulture);
            }
            catch (Exception e)
            {
                return "(error: " + e.GetType().Name + ")";
            }
        }

        void Refresh()
        {
            try
            {
                var sb = new StringBuilder();
                sb.AppendLine("SC Haptics " + SCHaptics.Version);
                sb.AppendLine();
                ScFeed f = plugin.CurrentFeed;
                RefreshGains(f);
                if (f == null)
                {
                    sb.AppendLine("plugin not running");
                }
                else
                {
                    sb.AppendLine("Status        : " + f.Status);
                    sb.AppendLine("Connected     : " + f.Connected);
                    sb.AppendLine("Packets/s     : " + f.PacketsPerSec.ToString("0.0"));
                    sb.AppendLine("Packets total : " + f.PacketsTotal);
                    sb.AppendLine("Parse errors  : " + f.ParseErrors);
                    sb.AppendLine("Last event    : " + f.LastEvent);
                    sb.AppendLine("Player role   : " + f.PlayerRole + "   (1 seated, 0 on foot, 3 settling)");
                    sb.AppendLine("Seated        : " + f.Seated + "   (informational; motion is gated by the bridge)");
                    sb.AppendLine("Ship          : " + f.Ship);
                    sb.AppendLine();
                    sb.AppendLine("Event rates (posts/s):");
                    for (int i = 0; i < SCHaptics.NumEv; i++)
                    {
                        sb.Append(("ev" + i.ToString("00") + " " + f.EvRate(i).ToString("0.0")).PadRight(16));
                        if (i % 5 == 4) sb.AppendLine();
                    }

                    sb.AppendLine();
                    sb.AppendLine("Config        : " + f.ConfigPath);
                    sb.AppendLine("                " + f.ConfigStatus);
                    foreach (string err in f.ConfigErrors) sb.AppendLine("  ! " + err);
                    sb.AppendLine("Contact cHP   : " + f.ContactHP.ToString("0.0") + " m/s2");
                    sb.AppendLine("Shock         : linear " + f.ShockLin.ToString("0.0") + " m/s2, rotational " + f.ShockRot.ToString("0.00"));
                    sb.AppendLine("Last hit      : " + (f.HitAge < 0 ? "none yet" :
                        (f.HitKind == 1 ? "shield" : f.HitKind == 2 ? "hull" : "?") + " " + f.HitDamage.ToString("0.0000")
                        + ", azimuth " + f.HitAzimuth.ToString("0.00") + ", elevation " + f.HitElevation.ToString("0.00")
                        + ", " + f.HitAge.ToString("0.0") + " s ago, " + f.HitCount + " hits"));
                    sb.AppendLine("Heart rate    : " + f.HeartBPM.ToString("0.0") + " BPM");
                    sb.AppendLine();
                    sb.AppendLine("Shakers (0..100)   Low    High      Motors (0..100)");
                    for (int n = 1; n <= Math.Max(FxConfig.MaxShakers, FxConfig.MaxMotors); n++)
                    {
                        string row = n <= FxConfig.MaxShakers
                            ? ("  Shaker" + n).PadRight(18) + f.ShakerBand(n, 0).ToString("0.0").PadLeft(5) + f.ShakerBand(n, 1).ToString("0.0").PadLeft(8)
                            : "".PadRight(31);
                        if (n <= FxConfig.MaxMotors) row += ("      Motor" + n).PadRight(18) + f.MotorAmp(n).ToString("0.0").PadLeft(5);
                        sb.AppendLine(row);
                    }
                    sb.AppendLine();
                    sb.AppendLine("Effect          type     now  triggers  band  shakers   motors    when");
                    foreach (string[] r in f.EffectRows())
                        sb.AppendLine(r[0].PadRight(15) + " " + r[1].PadRight(7) + r[3].PadLeft(6) + r[4].PadLeft(10) + "  " + r[7].PadRight(5) + " " + r[5].PadRight(9) + " " + r[8].PadRight(9) + " " + r[6] + " " + r[2]);
                }
                sb.AppendLine();
                sb.AppendLine("Property name check (what SimHub formulas will use):");
                sb.AppendLine("  SCHaptics.Connected     = " + PropCheck("SCHaptics.Connected"));
                sb.AppendLine("  SCHaptics.Shaker1.Low   = " + PropCheck("SCHaptics.Shaker1.Low"));
                sb.AppendLine("  SCHaptics.Shaker1.High  = " + PropCheck("SCHaptics.Shaker1.High"));
                sb.AppendLine("  SCHaptics.Motor1.Amp    = " + PropCheck("SCHaptics.Motor1.Amp"));
                text.Text = sb.ToString();
            }
            catch (Exception e)
            {
                text.Text = "status page error: " + e.Message;
            }
        }
    }
}
