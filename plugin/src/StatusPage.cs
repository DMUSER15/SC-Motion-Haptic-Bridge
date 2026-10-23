// StatusPage - read-only left-menu page for SC Haptics.
// Plain WPF built in code (no XAML, no MahApps, no resources) so SimHub UI
// library updates cannot break it. Refreshes 4x per second while visible.
using SimHub.Plugins;
using System;
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
            Content = new ScrollViewer
            {
                Content = text,
                HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto
            };
            timer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(250) };
            timer.Tick += (s, e) => Refresh();
            Loaded += (s, e) => { Refresh(); timer.Start(); };
            Unloaded += (s, e) => timer.Stop();
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
