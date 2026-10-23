# plugin/ — SC Haptics (SimHub plugin)

- `src/`: C# source (`SCHaptics.cs`, `Effects.cs`, `StatusPage.cs`, `MiniJson.cs`, `SCHaptics.csproj`). On Linux, `~/sc_haptics/src/*` are symlinks to these files. Build output goes to `~/sc_haptics/src/bin`, outside the repository.
- `effects.default.json`: default effects. Your tuned live config is `config/effects.json`, linked to `~/sc_haptics/effects.json`; the plugin hot-reloads it within 1 s.

Build and install on Linux (SimHub closed):

```fish
env DOTNET_CLI_TELEMETRY_OPTOUT=1 dotnet build ~/sc_haptics/src/SCHaptics.csproj -c Release
python3 ~/sc_haptics_install.py install
```

`sc_fx.py` is the reference implementation of the effect rules. `Effects.cs` mirrors it rule for rule; change both together.

**Where the plugin looks for `effects.json` (0.2.3; 0.3.1 dropped the fixed 0.2.2 path).** It decides once, when SimHub starts, and shows the result on its status page ("Config") and in the properties `SCHaptics.Config.Path` and `SCHaptics.Config.Source`:

1. the environment variable `SC_HAPTICS_CONFIG`, used as given;
2. under Wine: your Linux home + `\sc_haptics\effects.json` on the Z: drive (from `WINEHOMEDIR` or `HOME`);
3. `%USERPROFILE%\sc_haptics\effects.json` (Windows; the Windows installer puts it there).

Of 2 and 3 the first file that exists wins.

## Outputs (0.3.0)

Each effect in `effects.json` can go to bass shakers, ShakeIt motors, both, or neither (property only):

| Key | Output properties | Bind in SimHub |
|---|---|---|
| `"shakers": [1, 2]` | `SCHaptics.Shaker<n>.Amp`, `.Low`, `.High` (0..100), `.Freq` (Hz) | ShakeIt **Bass Shakers** custom effects (as before) |
| `"motors": [1, 2]` | `SCHaptics.Motor<n>.Amp` (0..100) | ShakeIt **Motors** custom effects (optional; the default config uses none) |

The default config sends every effect to the bass shakers. Events on the **motion platform** (SimHub Motion -> Haptics: Suspensions, Touch down, Engine vibration) do not come from this plugin: SimHub's Motion haptics read only the game's own telemetry fields, so the bridge `scripts/lm_simhub4.py` sends them (suspension velocity from the same shock / hit / touchdown rules, RPM, speed, on-ground). The plugin's `Shock.*` and `Hit.*` properties show the same events for tuning.

**Tuning properties** (read-only, for SimHub's property list or a dashboard): `SCHaptics.Shock.Lin` (m/s2 not explained by own thrust), `SCHaptics.Shock.Rot` (|id34 - id41|), `SCHaptics.Hit.Count / Kind (1 shield, 2 hull) / Damage / Azimuth / Elevation / Age`, `SCHaptics.Heart.BPM`.

New effect keys: `motors`, `cond` (conditions on the triggering post), `field` on pulses (strength from a field), type `shock` (`threshold`, `full`, `threshold_rot`, `full_rot`, `window_ms`). Full reference: the docstring of `scripts/sc_fx.py`. Check a config offline: `python3 ~/SC-Motion-Haptic-Bridge/scripts/sc_fx.py check FILE`; replay a recording: `python3 ~/SC-Motion-Haptic-Bridge/scripts/sc_fx.py sim RECORDING FILE` (lists shock episodes so thresholds can be checked against real flights).
