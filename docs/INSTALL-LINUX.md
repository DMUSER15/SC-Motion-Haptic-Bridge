# Install on CachyOS / Arch Linux

Commands are for **fish**, CachyOS's default shell (they work in bash too unless noted). The installer finds your Star Citizen prefix and SimHub itself and tells you if anything needs your input.

This package does **not** include the Star Citizen, SimHub or D-BOX installers. Each section says where to get them and how this package finds them.

**Safety first.** This drives a powered motion platform. Keep the E-stop within reach, and follow the first-run procedure in section 7 before anyone sits in the rig.

Contents:

1. Get SC-Motion-Haptic-Bridge
2. Prerequisites
3. Star Citizen with LUG-Helper
4. D-BOX HaptiSync Center in the Star Citizen prefix
5. SimHub with linux-simracing-utils (and your motion hardware)
6. Run the installer
7. Verify, then the first run with the platform
8. Daily use, updates, game patches, certificate renewal
9. Removing the motion setup
10. Troubleshooting

---

## 1. Get SC-Motion-Haptic-Bridge

Pick a folder you will **keep**: your home folder gets links into it, and the launcher updates it when a new release comes out ([UPDATING.md](UPDATING.md)). This guide uses `~/SC-Motion-Haptic-Bridge`.

**Download (simplest):** on the repository's **Releases** page on GitHub, download the newest release's **Source code (tar.gz)**, then:

```fish
mkdir -p ~/SC-Motion-Haptic-Bridge
tar xzf ~/Downloads/SC-Motion-Haptic-Bridge-0.1.0-alpha.tar.gz -C ~/SC-Motion-Haptic-Bridge --strip-components=1
```

(Use the file name you downloaded.) Inside the folder you should see `README.md`, `release.json`, `setup`, `scripts`.

**Or with git:**

```fish
git clone https://github.com/DMUSER15/SC-Motion-Haptic-Bridge ~/SC-Motion-Haptic-Bridge
```

---

## 2. Prerequisites

```fish
sudo pacman -S --needed python python-grpcio python-protobuf openssl git espeak-ng dotnet-sdk
```

What each package is for:

- `python-grpcio` and `python-protobuf`: the capture server.
- `openssl`: the certificate tools.
- `espeak-ng` (optional): spoken prompts from the launcher.
- `dotnet-sdk`: builds the SC Haptics plugin (net48; `dotnet-sdk` 10 builds it on CachyOS). Without it, motion works and the shakers stay silent.

---

## 3. Star Citizen with LUG-Helper

Install Star Citizen with **LUG-Helper** ([github.com/starcitizen-lug/lug-helper](https://github.com/starcitizen-lug/lug-helper)), the Star Citizen Linux Users Group's helper.

1. Let it create the Wine prefix (default `~/Games/star-citizen`; any location works).
2. Install a runner.
3. Install **LIVE** through the RSI Launcher, start it once from LUG's own menu entry, and quit.

The installer recognises a prefix by two things: a `drive_c` folder and LUG's `sc-launch.sh`.

**Explicit choice.** This always wins, in this order:

1. `--game-prefix PATH`
2. `SC_GAME_PREFIX`
3. the prefix saved by an earlier `apply` in `~/.config/sc-motion/config.json`

**Otherwise** it lists every prefix it finds in LUG-Helper's `winedir.conf`, `~/Games/star-citizen`, and up to three folders deep on drives under `/run/media/$USER` and `/mnt`. A single prefix found is used. With several, `apply` shows a numbered menu; the likely prefix is pre-selected (named by LUG-Helper's config, with the D-BOX connector and LIVE), so Enter accepts it. `apply --select` shows the menu again later.

The launcher starts the game with LUG's `sc-launch.sh`, unchanged.

---

## 4. D-BOX HaptiSync Center in the Star Citizen prefix

The game only produces motion telemetry when it can load D-BOX's LiveMotion connector. **No D-BOX hardware is needed**, and D-BOX's Motion Engine is not used.

1. **Download** D-BOX HaptiSync Center from [d-box.com/en/software-downloads](https://www.d-box.com/en/software-downloads) into `~/Downloads`. The guide finds any `HSC_<version>_setup.exe` and takes the newest; the Wine workarounds are proven with 1.4.0.

2. **Guided install.** Close the RSI Launcher and the game first.

   ```fish
   python3 ~/SC-Motion-Haptic-Bridge/setup/linux/sc_motion_guide.py dbox            # read-only: every step and its state
   python3 ~/SC-Motion-Haptic-Bridge/setup/linux/sc_motion_guide.py dbox --apply    # asks [y/N] before each change
   ```

   HaptiSync Center's installer rolls itself back under Wine for three reasons; the guide applies three workarounds that let it finish (details: `setup/linux/dbox-wine/README.md`). They affect only D-BOX's installer, never the game.

   | # | Step | What |
   |---|---|---|
   | 1 | installer | newest `HSC_*_setup.exe` in `~/Downloads`, `~` or `~/dbox-install` (or `--installer FILE`) |
   | 2 | prefix | the Star Citizen prefix, and its LUG runner from `sc-launch.sh` |
   | 3 | powershell | PowerShell 7 in the prefix (`winetricks powershell_core powershell`) |
   | 4 | shim | builds `~/dbox-install/sc.exe` from `setup/linux/dbox-wine/sc_shim.c` (needs `mingw-w64-gcc`) |
   | 5 | stage | copies the installer next to the shim; the shim only works there |
   | 6 | netsecurity | stand-in firewall module into `drive_c/Program Files/PowerShell/7/Modules/NetSecurity/` |
   | 7 | wmic | `wmic.bat` into `drive_c/wmicshim/` |
   | 8 | run | the installer with `WINEPATH='C:\wmicshim'`; log in `~/dbox-install/install_<time>.log` |
   | 9 | check | the connector is installed; summary of `C:\scshim.log` |

3. **Expected result:** `drive_c/ProgramData/D-BOX/Gaming/LiveMotion/LiveMotionConnector/` exists and holds `dbxLive64.dll`, `LiveMotionConnector.config` and `livemotion.crt`. The HaptiSync Center window itself crashes under Wine; that is expected and harmless.

4. **What the installer (section 6) then does with it:**
   - copies that folder to `…/LiveMotion/StarCitizen/`, the folder the game loads the connector from;
   - keeps a copy of D-BOX's original certificate as `~/livemotion.crt.orig`;
   - puts your own certificate in every copy of `livemotion.crt` (`footprint all`), so the connector accepts the local capture server.

---

## 5. SimHub with linux-simracing-utils (and your motion hardware)

Install SimHub with **linux-simracing-utils** ([github.com/srounce/linux-simracing-utils](https://github.com/srounce/linux-simracing-utils)) into `~/linux-simracing-utils`. The guide downloads it and runs its `install.sh`; answer yes for SimHub.

```fish
python3 ~/SC-Motion-Haptic-Bridge/setup/linux/sc_motion_guide.py simhub            # read-only
python3 ~/SC-Motion-Haptic-Bridge/setup/linux/sc_motion_guide.py simhub --apply
```

The launcher starts SimHub from these paths. Other SimHub copies (for example in `~/.wine` or a Steam prefix) are listed by the installer but never used:

- prefix: `~/linux-simracing-utils/pfx`
- Wine: `~/linux-simracing-utils/var/wine/bin/wine`
- SimHub: `…/pfx/drive_c/Program Files (x86)/SimHub/SimHubWPF.exe`

To start or stop SimHub by hand:

```fish
set shpfx ~/linux-simracing-utils/pfx
set wbin ~/linux-simracing-utils/var/wine/bin
env WINEPREFIX="$shpfx" $wbin/wine 'C:\Program Files (x86)\SimHub\SimHubWPF.exe'
env WINEPREFIX="$shpfx" $wbin/wineserver -k   # stop; never while the game runs
```

### Your motion hardware (optional)

**Any motion platform or controller SimHub supports works.** Connect and set it up in SimHub the way its maker describes. The installer never requires it; its step 10 only records which help to show.

General notes for SimHub under Wine:

- A controller on a **USB serial port** (FTDI, CH340, CDC-ACM and so on) needs a COM port in SimHub's Wine prefix: a `HKLM\Software\Wine\Ports` entry plus a `dosdevices/comNN` link to the device's stable `/dev/serial/by-id/` name, and read/write access to the device (a udev rule). The separate tool [simhub-thanos-linux](https://github.com/DMUSER15/simhub-thanos-linux) does this for the THANOS and, with `--profile serial`, for any other USB serial device.
- Controllers SimHub reaches another way (network, its own USB driver) may need nothing extra; check with their maker.
- In SimHub's **Arduino** settings, turn **off** "scan all COM ports" and enable only the ports you use. The scan can take a motion controller's port.
- Power and start order: follow your hardware's instructions. Many platforms want motion power OFF while SimHub starts and connects, then ON.

**Example: THANOS AMC.** See [THANOS-LINUX.md](THANOS-LINUX.md) and the simhub-thanos-linux README. In short:

```fish
git clone https://github.com/DMUSER15/simhub-thanos-linux ~/simhub-thanos-linux
python3 ~/simhub-thanos-linux/simhub_thanos.py plan
python3 ~/simhub-thanos-linux/simhub_thanos.py apply     # SimHub closed, motion power OFF
```

### SimHub: game definition, Motion tab, Motion haptics, ShakeIt

- **Game definition "Star Citizen" (installer step 7).** The installer copies `config/simhub/Star Citizen.simdef` into SimHub's `PluginsData\Common\ExternalSims` folder and registers it in SimHub's prefix (`drive_c/users/<you>/AppData/Local/SimHub/ExternalSims/Registrations/2f10c0be-479b-4b35-8671-a1b16ce4b874.simlink`). **Close SimHub before running the installer.** The definition has 24 standard fields: the nine motion fields (yaw, pitch and roll rate; surge, sway and heave; velocity forward, lateral and up), then speed, engine, throttle, ground contact and suspension fields for SimHub's Motion haptics. Data conversion revision 3, Flight game family, UDP port 30777. The bridge reads the registered file at start-up and packs exactly those fields, so the two always agree.
  - A usable definition that is already registered (for example one you edited in SimHub's Game Definition Editor) is kept. The installer replaces only a missing or unreadable one, renaming the old file to `*.bak-<time>` first.
  - **After the first install, select "Star Citizen" in SimHub's game list once.** With another game selected, SimHub shows "Waiting for data" and the platform gets nothing. The launcher and the verifier warn about this.
- **Motion tab.** Set up your motion profile for Star Citizen: which axes drive the platform, intensity, smoothing, limits, motion scaling and crash protection. **These values are yours to choose for your platform**: the bridge sends the game's values unshaped and never scales them. Start low.
- **Motion haptics (Motion tab).** Engine vibration, Traction loss and Suspensions are available for Star Citizen; set their gain and smoothing yourself. Touch down is shown as not available (known open item).
- **ShakeIt (bass shakers, optional).** Two Custom Effects, mode Front/Rear, NCalc:
  - "SC Low" (~30 Hz): FL/FR `[SCHaptics.Shaker1.Low]`, RL/RR `[SCHaptics.Shaker2.Low]`.
  - "SC High" (~50 Hz): FL/FR `[SCHaptics.Shaker1.High]`, RL/RR `[SCHaptics.Shaker2.High]`.
  - Both: high frequency off, gamma 1, threshold 0, minimum force 0.
  - ShakeIt only runs while SimHub shows a game as connected.
- **Shaker sound cards (optional).** If you use two identical USB sound cards for the shakers, give them stable names with a WirePlumber rule in `~/.config/wireplumber/wireplumber.conf.d/` that matches each card by its USB port path, so front and rear never swap after a reboot.

---

## 6. Run the installer

```fish
python3 ~/SC-Motion-Haptic-Bridge/setup/linux/sc_motion_install.py plan
```

The plan is read-only. It lists every Star Citizen prefix and every SimHub copy it found (`->` marks the one in use), then checks the **dependencies**. `apply` changes nothing at all until every dependency is DONE:

| # | Dependency | DONE when |
|---|---|---|
| D1 | prereqs | Python modules and openssl are installed (the pacman line is printed) |
| D2 | star-citizen | one prefix is assigned (section 3) |
| D3 | dbox | D-BOX's connector is in that prefix (section 4) |
| D4 | simhub | SimHub is in `~/linux-simracing-utils` (section 5) |
| D5 | descriptor | `scripts/motion_desc.bin` parses (it is part of the release) |

Then the install steps (**DONE**, **TODO** = apply will do it, **BLOCKED** = needs you):

| # | Step | What happens |
|---|---|---|
| 1 | dbox-folder | copies missing files LiveMotionConnector → StarCitizen (never overwrites) |
| 2 | dbox-config | connector points at 127.0.0.1:61666 / LiveMotionComm (check only) |
| 3 | dbox-original | `~/livemotion.crt.orig` = D-BOX's original (sha256 `6f5b4b7a…97ad`) |
| 4 | cert-pair | creates `~/lm.crt` + `~/lm.key` **only if both are missing** (RSA 4096, CN/SAN LiveMotionComm, CA:TRUE, 10 years) |
| 5 | scripts | links only the files needed to run into your home folder: `sc_start.py`, `SC_Motion_Server.py` with its `motion_desc.bin`, the bridge `lm_simhub4t.py` with `lm_simhub4.py`, `lm_simhub3.py`, `sc_fx.py` (and `lm_simhub3t.py`, the 9-field bridge), `sc_lmcert.py`, `sc_certswap.py`, `sc_haptics_install.py`, `sc_motion_clean.py`, the plugin source and `effects.json` |
| 6 | footprint | `sc_lmcert.py footprint all`: your certificate in every copy, backups first |
| 7 | simdef | installs and registers the game definition "Star Citizen" in SimHub (section 5); keeps a usable one that is already registered; SimHub closed |
| 8 | plugin | `dotnet build` + `sc_haptics_install.py install` (SimHub closed) |
| 9 | menu | writes the menu entries "Start StarCitizen - Motion Enabled" and "Star Citizen - Remove Motion Setup" |
| 10 | motion-hw | **optional**, never a dependency: which motion hardware help to show |

Then:

```fish
python3 ~/SC-Motion-Haptic-Bridge/setup/linux/sc_motion_install.py apply
```

**Motion hardware (step 10).** `apply` asks once (Enter = none) and saves the choice:

| Choice | Effect |
|---|---|
| `none` (default) | nothing hardware-specific; set up any motion hardware in SimHub yourself, or use no rig |
| `thanos` | example: step 10 runs simhub-thanos-linux's `plan` (read-only) and is DONE when it reports `READY`. Changing the port stays a separate step for you |
| `other` | generic notes only (section 5); the verifier lists USB serial COM ports as INFO |

Change only this choice later with `python3 ~/SC-Motion-Haptic-Bridge/setup/linux/sc_motion_install.py motion-hw none`.

`apply` saves the assigned locations in `~/.config/sc-motion/config.json`; the launcher, the certificate tools and the plugin installer use them. To change the prefix: `apply --select` (menu) or `apply --game-prefix PATH`. `apply` refuses while Star Citizen runs, and stops at the first failed step without changing anything after it.

**The message schema.** `SC_Motion_Server.py` reads `motion_desc.bin`, which sits next to it in `scripts/`. It is generated from the table in `tools/motion_schema.py`. To check it: `python3 ~/SC-Motion-Haptic-Bridge/tools/motion_schema.py check`.

---

## 7. Verify, then the first run with the platform

```fish
python3 ~/SC-Motion-Haptic-Bridge/setup/linux/sc_motion_verify.py
python3 ~/SC-Motion-Haptic-Bridge/setup/linux/sc_motion_verify.py --live
```

`--live` starts the capture server and bridge without the game and checks the TLS certificate the game will see, the SimHub packets against the registered game definition (size and signature), and the copy for the haptics plugin. Then it stops both. SimHub must be closed, so nothing can reach the platform. [VERIFY.md](VERIFY.md) explains every line.

**First real run** (and again after any update that warns that platform movement values have changed). E-stop within reach, one change at a time:

1. **Nobody in the rig, motion power OFF, SimHub motion OFF.** Start "Start StarCitizen - Motion Enabled" from the app menu, fly briefly, and check SimHub's telemetry for the "Star Citizen" game moves.
2. **Motion power ON, still nobody in the rig.** Repeat.
3. **Only then sit in the rig.**

After the session:

```fish
python3 ~/SC-Motion-Haptic-Bridge/setup/linux/sc_motion_verify.py --last-session
```

Expected PASS lines: `game connected (GetIdentification)`, `telemetry stream opened`, `vehicle frames (ev3)` with a large count, `bridge saw you seated with motion`.

---

## 8. Daily use, updates, game patches, certificate renewal

- **Play:** app menu → **Start StarCitizen - Motion Enabled**. It checks for updates (one line when up to date; a pause only when a newer release exists, see [UPDATING.md](UPDATING.md)), starts and self-tests the pipeline, starts SimHub, then pauses for the rig (the checklist is printed): bring the platform up, then press Enter. Thirty seconds after the game exits, the launcher and Wine in the game prefix are closed and the pipeline stops. SimHub stays open.
- **Clean up after a crash:** `python3 ~/sc_start.py stop`
- **After a Star Citizen or D-BOX update:**
  1. Run `python3 ~/sc_lmcert.py check`.
  2. If a copy is not `~/lm.crt` any more, the update replaced it. The launcher asks at start-up: **[r]epair** puts your certificate back (backups first). By hand, with the game closed: `python3 ~/sc_lmcert.py repair`.
  3. Run the verifier with `--live`.
- **Certificate renewal** (the pair is valid 10 years; `check` warns within 30 days). With the game closed and the capture server stopped: `python3 ~/sc_lmcert.py new`, then `python3 ~/sc_lmcert.py activate`; if anything goes wrong, `python3 ~/sc_lmcert.py rollback`.

---

## 9. Removing the motion setup

App menu → **Star Citizen - Remove Motion Setup**, or `python3 ~/sc_motion_clean.py plan` (read-only) and then `apply`. Close the game and SimHub first; `apply` asks you to type `CLEAN`. It:

1. stops the capture server and bridge;
2. puts D-BOX's original back into every `livemotion.crt` copy (`sc_certswap.py original`, backups in `~/sc_certswap_backup/`);
3. moves the connector folder `ProgramData/D-BOX/Gaming/LiveMotion/StarCitizen` (made by installer step 1) to the backup; `--keep-connector` skips this;
4. moves `SCHaptics.dll` out of SimHub;
5. moves the "Start StarCitizen - Motion Enabled" menu entry.

Everything moved goes to `~/sc_motion_clean_backup/<time>/` with a `manifest.json`; nothing is deleted. Not touched: this folder, your certificate pair, `effects.json`, SimHub's own settings and the D-BOX HaptiSync install (remove that with D-BOX's uninstaller if you want). To set everything up again: `python3 ~/SC-Motion-Haptic-Bridge/setup/linux/sc_motion_install.py apply`.

## 10. Troubleshooting

| Symptom | Check |
|---|---|
| `certificate check FAILED` at start | answer **r** (repair), or `python3 ~/sc_lmcert.py status` and `python3 ~/sc_lmcert.py repair` with the game closed |
| `game connected` FAIL in `--last-session` | did the game load the connector? `LiveMotionConnector.log` in the LIVE folder; the verifier's `dbox` lines |
| `pipeline ports free` FAIL | the verifier names the process holding the port: another bridge, a recorder, or D-BOX's Motion Engine on 61666 |
| SimHub shows "Waiting for data" | select "Star Citizen" in SimHub's game list |
| SimHub motion stuck on "Starting" | Arduino port scan off; your hardware's power order (section 5) |
| `THANOS COM port` WARN (THANOS choice only) | run `python3 ~/simhub-thanos-linux/simhub_thanos.py plan`; controller unplugged or powered off also shows here |
| Shakers silent, motion fine | ShakeIt only runs while SimHub shows the game connected; plugin check in the verifier; your sound card volumes |
| `home files linked` WARN SAME/DRIFT | an editor replaced a link: `python3 ~/SC-Motion-Haptic-Bridge/tools/repo_link.py link` (or `--adopt-home` to keep your edited copy) |
| update check says "GitHub not reachable" | no network or GitHub down; the launcher carries on with your version |
