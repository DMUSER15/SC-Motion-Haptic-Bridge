# Install on Windows

From download to "connected in SimHub", in order. Every program runs natively on Windows; the Python scripts are the same files as on Linux.

> **Alpha, Windows untested on real hardware.** The Windows package has been tested on stand-ins of a Windows layout, not yet on a real Windows PC. Please report anything that differs from this guide.

**Safety first.** This drives a powered motion platform. Keep the E-stop within reach, and follow the first-run procedure in section 10 before anyone sits in the rig.

What you will end up with:

```
Star Citizen ─ D-BOX connector (unchanged) ─► capture server (127.0.0.1:61666)
   ─► bridge ─► SimHub "Star Citizen" game ─► your motion platform (Motion tab, Motion haptics)
   └► SC Haptics plugin ─► ShakeIt "SC Low" / "SC High" ─► bass shakers
```

This package does not include the Star Citizen, SimHub or D-BOX installers; each section says where to get them.

---

## 1. Python, OpenSSL and (optional) Git

Open **PowerShell** (not as administrator) and run:

```powershell
winget install -e --id Python.Python.3.13
winget install -e --id ShiningLight.OpenSSL.Light
```

Close PowerShell and open a **new** one, so the new programs are on PATH. Check:

```powershell
py -3 --version
```

The installer (section 6) installs the Python modules `grpcio` and `protobuf` for your user. `openssl.exe` is found on PATH, in `C:\Program Files\OpenSSL-Win64\bin`, or in Git for Windows.

Optional:

- **Git** (`winget install -e --id Git.Git`) if you want to install with `git clone` (section 2).
- **.NET SDK** (`winget install Microsoft.DotNet.SDK.8`) to build the SC Haptics plugin (installer step 9). Without it, motion works and the shakers stay silent until you install the SDK and run the installer again.

## 2. Get SC-Motion-Haptic-Bridge

Pick a folder you will **keep**: the launcher updates this folder when a new release comes out ([UPDATING.md](UPDATING.md)).

**Download (simplest):**

1. Open the repository's **Releases** page on GitHub and download the newest release's **Source code (zip)**.
2. Unpack it to `%USERPROFILE%\SC-Motion-Haptic-Bridge` (for example `C:\Users\<you>\SC-Motion-Haptic-Bridge`). Inside it you should see `README.md`, `release.json`, `setup`, `scripts`.

**Or with git:**

```powershell
git clone https://github.com/DMUSER15/SC-Motion-Haptic-Bridge $env:USERPROFILE\SC-Motion-Haptic-Bridge
```

## 3. Star Citizen

Install the **RSI Launcher** from robertsspaceindustries.com, install **LIVE**, start the game once, and quit.

You do not need to tell the installer where they are. It looks in Windows' installed-programs list and in the usual folders on every fixed drive. If it finds more than one, it asks (section 6). The LIVE folder is only used for checks; the game is started from the RSI Launcher.

## 4. D-BOX HaptiSync Center (provides the game's motion output)

Star Citizen sends motion telemetry only through D-BOX's LiveMotion connector. You need D-BOX's free software for that connector; **no D-BOX hardware is needed**.

1. Download **D-BOX HaptiSync Center** from [d-box.com/en/software-downloads](https://www.d-box.com/en/software-downloads) and install it normally.
2. It must create `C:\ProgramData\D-BOX\Gaming\LiveMotion\LiveMotionConnector\`.

> **NOTE: Port 61666.** The capture server must own `127.0.0.1:61666`. If the capture server reports an error or says the port is in use, open `services.msc` and look for the **D-BOX Motion Engine** service. If it is running, it is holding the port: set its startup type to **Manual**, click **Stop**, then run the installer or the launcher again. This pipeline does not need that service. The installer (step 3) and the verifier name the program that holds the port.

## 5. SimHub and your motion hardware

1. Install **SimHub** from [simhubdash.com](https://www.simhubdash.com). Any folder works; the installer finds it.
2. Connect your motion platform and set it up in SimHub as its maker describes. **Any platform SimHub supports works**; this package never requires a particular one.
3. In SimHub's Arduino settings, keep "scan all COM ports" **off** and enable only the ports you use. The scan can take a motion controller's port.
4. Follow your hardware's power and start order. Many platforms want motion power OFF while SimHub starts and connects to the controller, then ON.
5. **Close SimHub** before section 6: the installer puts SimHub's game definition in place, and SimHub must not be running for that.

## 6. Run the installer

Open PowerShell **as administrator** (right-click → *Run as administrator*) with your **normal Windows account**: steps 1 and 7 write under `C:\ProgramData\D-BOX`, step 9 into the SimHub folder. The certificate files go into that account's user folder, so use the same account you play with.

```powershell
cd $env:USERPROFILE\SC-Motion-Haptic-Bridge
powershell -ExecutionPolicy Bypass -File .\setup\windows\Install-SCMotion.ps1
```

This first run is **read-only**. It shows every RSI Launcher, LIVE folder and SimHub it found, the dependencies D1–D5 and the steps 1–12. When it looks right:

> **NOTE: TODO and BLOCKED lines are normal on this first run.** Steps that depend on an earlier step show **BLOCKED**, and steps that have not been done yet show **TODO**. They will not clear until you run the next command with `-Apply` in an **administrator** PowerShell.

```powershell
powershell -ExecutionPolicy Bypass -File .\setup\windows\Install-SCMotion.ps1 -Apply
```

`-Apply` changes nothing until all five dependencies are DONE:

| | Dependency | What has to be there |
|---|---|---|
| D1 | prereqs | Python modules `grpcio` + `protobuf` (`-Apply` installs them with pip), `openssl.exe` |
| D2 | star-citizen | the RSI Launcher (section 3) |
| D3 | dbox | HaptiSync Center installed (section 4) |
| D4 | simhub | SimHub installed (section 5) |
| D5 | descriptor | `scripts\motion_desc.bin` in this folder (it is part of the release) |

Then it runs the steps that are TODO:

| # | Step | What happens |
|---|---|---|
| 1 | dbox-folder | LiveMotionConnector → StarCitizen (copies missing files only; elevated) |
| 2 | dbox-config | connector points at 127.0.0.1:61666 / LiveMotionComm (check only) |
| 3 | port-61666 | nothing else holds the port (section 4) |
| 4 | dbox-original | keeps D-BOX's original certificate as `%USERPROFILE%\livemotion.crt.orig` |
| 5 | cert-pair | creates `%USERPROFILE%\lm.crt` + `lm.key`, only if both are missing |
| 6 | files | copies the runtime scripts to `%USERPROFILE%\sc-motion` |
| 7 | footprint | your certificate in every copy of `livemotion.crt` in D-BOX's folders, backups first (elevated) |
| 8 | simdef | installs SimHub's game definition **"Star Citizen"** and registers it in `%LOCALAPPDATA%\SimHub\ExternalSims\Registrations` (SimHub closed). A usable one that is already registered is kept |
| 9 | plugin | builds the **SC Haptics** plugin (`dotnet`) and copies `SCHaptics.dll` into SimHub |
| 10 | effects | `%USERPROFILE%\sc_haptics\effects.json`, your effects (never overwritten later) |
| 11 | shortcut | Start menu entry **Start StarCitizen - Motion Enabled** |
| 12 | motion-hw | **optional**: `none` (default), `thanos` (example) or `other`; information only, never blocks |

If a step says "permission denied", PowerShell was not elevated: run the `-Apply` line again **as administrator** with the same account.

**More than one RSI Launcher, LIVE folder or SimHub?** `-Apply` shows a numbered menu (Enter = the one marked `*`, `q` = cancel). The choice is saved in `%APPDATA%\sc-motion\config.json`, together with this folder's location (for updates); the launcher and the verifier use it. To choose again: add `-Select`. To name a folder directly: `-SimHubDir 'D:\SimHub'`, `-RsiLauncher '…\RSI Launcher.exe'`, `-LiveDir '…\LIVE'`.

## 7. SimHub: select the game, then tune it for your platform

1. Start SimHub.
2. In SimHub's game list, **select "Star Citizen"** once. With another game selected, SimHub shows "Waiting for data" and the platform gets nothing (the launcher warns about this).
3. **Motion tab.** Set up your motion profile for Star Citizen: which axes drive the platform, intensity, smoothing, limits and crash protection. **These values are yours to choose for your platform**: the bridge sends the game's values unshaped and never scales them for you. Start low.
4. **Motion haptics** (Motion tab): Engine vibration, Traction loss and Suspensions are available for Star Citizen. Set their gain and smoothing yourself. (Touch down is shown as not available; known open item.)
5. **Bass shakers (optional).** In ShakeIt Bass Shakers, add two **Custom Effects**, mode Front/Rear, formula type NCalc:
   - **"SC Low"** (~30 Hz): front left/right `[SCHaptics.Shaker1.Low]`, rear left/right `[SCHaptics.Shaker2.Low]`;
   - **"SC High"** (~50 Hz): front `[SCHaptics.Shaker1.High]`, rear `[SCHaptics.Shaker2.High]`;
   - both: high frequency off, gamma 1, threshold 0, minimum force 0.

   ShakeIt only runs while SimHub shows the game as connected. The plugin's own page in SimHub (left menu, **SC Haptics**) shows which `effects.json` it loaded.

## 8. Verify

With the game, SimHub and the pipeline closed:

```powershell
py -3 $env:USERPROFILE\sc-motion\sc_verify_win.py
py -3 $env:USERPROFILE\sc-motion\sc_verify_win.py --live
```

`--live` starts the capture server and bridge briefly without the game and checks the certificate the game will see, the packets SimHub will receive (against the registered game definition) and the copy for the haptics plugin. SimHub must be closed, so nothing can reach the platform. [VERIFY.md](VERIFY.md) explains every line.

**What a good result looks like.** Each run ends with `RESULT: PASS` and `0 warn, 0 fail`, followed by the path of a report file in `%USERPROFILE%\lm_capture\verify`. The number of `pass` and `info` lines can differ from machine to machine.

- **INFO lines are normal.** With nothing running you will see `game no, SimHub no, pipeline no`, and `motion hardware none` if you did not choose a platform in installer step 12.
- **`--live` adds these checks, all PASS:** capture server listening on `127.0.0.1:61666`; TLS self-test (the server validates against the game's certificate); bridge listening on UDP 61700; bridge → SimHub packet answered (with the bundled game definition, 134-byte packets, motion values all zero because no game is running); and the copy for the plugin received byte for byte.
- Any `WARN` or `FAIL`: see [VERIFY.md](VERIFY.md), fix it, and run again. If it will not clear, open an Issue and paste the full output.

## 9. Start

Start the pipeline **from the Start menu**: open **Start → Start StarCitizen - Motion Enabled** (the shortcut installer step 11 created). It opens its own PowerShell window. Do not start it from another window, and leave that window open while you play.

**Steps 1 to 6 below are the stages you will see running in that window**, in this order:

1. update check ([UPDATING.md](UPDATING.md)): one line when you are up to date; a pause only when a newer release exists;
2. certificate check, capture server, TLS self-test, bridge, zero-frame self-test;
3. SimHub starts; the launcher warns if its game is not Star Citizen;
4. **rig pause**: bring up your platform in its start order, check SimHub shows the controller connected, then press Enter in that window;
5. the RSI Launcher opens; start the game from it;
6. thirty seconds after the game exits, the pipeline stops. The RSI Launcher and SimHub stay open.

> **NOTE: If the plugin or pipeline crashes** and leaves processes running, clear them with: `py -3 $env:USERPROFILE\sc-motion\sc_start_win.py stop`

## 10. First run with the platform

Follow this for the first run, and again after any update that warns that platform movement values have changed. E-stop within reach, one change at a time:

1. **Nobody in the rig, motion power OFF, SimHub motion OFF.** Start the launcher, fly briefly, and watch SimHub's telemetry for the "Star Citizen" game move.
2. **Motion power ON, still nobody in the rig.** Fly again; watch the platform.
3. **Only then a person.**

**After the first session**, run this command in PowerShell and compare the output with the lines below to confirm the plugin is working. If the output does not match, submit an Issue report and paste in the PowerShell output.

```powershell
py -3 $env:USERPROFILE\sc-motion\sc_verify_win.py --last-session
```

Expected PASS lines: `game connected (GetIdentification)`, `telemetry stream opened`, `vehicle frames (ev3)` with a large count, `bridge saw you seated with motion`.

## 11. Later

- **Updates:** the launcher offers them ([UPDATING.md](UPDATING.md)). Keep the folder from section 2.
- **After a Star Citizen or D-BOX update (certificate):** a game or D-BOX update can replace your certificate with D-BOX's original in one or more copies of `livemotion.crt`. The launcher checks the certificate at every start (the "certificate check" line) and, on a FAIL, asks whether to abort or continue without motion. Repairing the certificate writes under `C:\ProgramData\D-BOX`, so it needs an administrator PowerShell; the launcher's normal window is not elevated. To repair it, close the game and run these in a PowerShell **as administrator** with your normal Windows account:

  ```powershell
  py -3 $env:USERPROFILE\sc-motion\sc_cert_win.py lmcert check
  py -3 $env:USERPROFILE\sc-motion\sc_cert_win.py lmcert repair
  ```

  `repair` puts your certificate back into every copy, with backups. Then run the verifier with `--live` (section 8). The SC Haptics plugin does not touch certificates.
- **Certificate tools:** `py -3 $env:USERPROFILE\sc-motion\sc_cert_win.py lmcert status`. All commands work: `check`, `selftest`, `repair`, `footprint all`, `new`, `activate`, `rollback` (run the writing ones elevated).
- **Your effects:** `%USERPROFILE%\sc_haptics\effects.json`; the plugin reloads it within a second. To use another file, set the user environment variable `SC_HAPTICS_CONFIG` to its full path and restart SimHub.

## How the Windows package differs from Linux

| Linux | Windows |
|---|---|
| process list from `/proc` | `winproc.py` (PowerShell `Get-CimInstance`) |
| ports from `/proc/net` | `winproc.py` (`netstat -ano`) |
| Wine launch | native `.exe` |
| `sc_start.py` | `sc_start_win.py` (same order and checks, reusing `sc_start.py`'s helpers) |
| `python3 ~/sc_lmcert.py …` | `py -3 sc_cert_win.py lmcert …` |
| Wine prefix search and menu | `winlocate.py`: finds every RSI Launcher, LIVE folder and SimHub |
