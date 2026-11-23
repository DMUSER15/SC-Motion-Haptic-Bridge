# Changelog

## 0.2.2-alpha

### Control window: install and update the MFD add-on
- The **MFD add-on** card shows while the MFD is not installed and has an **Install MFD** button next to **MFD configurator**. It asks first, downloads the newest release of [SC-MFD-Motion-Addon](https://github.com/DMUSER15/SC-MFD-Motion-Addon), checks every file against its `release.json` and runs the MFD's own installer (the same as `addon install mfd`).
- When the update check finds a newer MFD release the button reads **UPDATE MFD** and opens the update dialog for the MFD only; otherwise **MFD up to date**.
- `addons/catalog.json` now ships with the release and points `mfd` at the public repository, so `addon install mfd` works on every install.
- README: link to the MFD repository and its dependencies.
- The install and update dialogs link to the repository and the release page, and show the new CHANGELOG sections (**What's new…**, **Show changes…**). Versions are shown without the tag's `v`.
- Failed, refused or partly checked installs and updates are added to the run's error log (Logs card), with the add-on installer's output; add-on hook output now appears in the window too.

- After an MFD install or update with motion running there is no Stop / Start any more: the window restarts the MFD (same port) or the bridge (new MFD; the capture server keeps running, so the game stays connected). With Star Citizen running the bridge restart waits for **Connect MFD** on the MFD card (motion pauses about two seconds).

### Control window: fixes
- Linux: the window has its logo in the taskbar again. It writes the menu entry `sc-control.desktop` (the taskbar takes the icon from it) when there is none; the logo `config/icons/sc-motion.svg` ships with the release.
- Updating a git clone on a platform other than Linux or Windows no longer asks for the Windows installer.

## 0.2.1-alpha

> **Windows: still untested on a real PC.** Tested on Linux with Windows stand-ins only (simulated user folders, stand-in SimHub and RSI Launcher). No motion file changed since 0.2.0-alpha; from 0.1.1-alpha the 0.2.0 motion warning applies.

### Control window on Windows (new)
- Start menu **Star Citizen Motion - Control window**: the same window as on Linux, driving the installed Windows launcher (`%USERPROFILE%\sc-motion\sc_start_win.py`) step by step. SimHub and the RSI Launcher are started directly; no Wine card.
- Installer step 13 installs PySide6 for your user (pip) and adds the Start menu entry. An install updated from 0.1.1 gets the entry from the terminal launcher at its next start; the window offers to install PySide6 when it is missing.
- Updates from the window also copy the runtime files into `%USERPROFILE%\sc-motion` and update SimHub's game definition / plugin, like the terminal launcher's update.
- `sc_start_win.py gui` opens the window from a terminal.

### Fixes
- Control window: no longer crashes right after opening on Windows (`signal.SIGHUP` does not exist there).
- Windows launcher: SimHub is detected on Windows (0.2.0 asked Linux's `/proc`, so the window would have seen "not running" and started a second SimHub).
- Update check: no crash when the window has no console (`pythonw.exe`) and a follow-up step needs you.
- Git clones of `main` update again: this release is built on top of `main` (0.2.0-alpha was not, so `git merge --ff-only` refused).
- `docs/CONTROL-WINDOW.md`: removed the stale "private for now" note.

## 0.2.0-alpha

> **Windows: largely untested — use at your own risk.** Everything below is included for Windows where it applies, but this release has not been run on a real Windows PC with a motion platform. The motion path (capture server, tee, bridge) has changed since 0.1.1-alpha and platform movement on Windows **MAY** have changed. Treat the first Windows start as a first run (nobody in the rig, motion power OFF, SimHub motion OFF) and start with low SimHub settings.
>
> **Motion warning:** this update changes files that shape platform movement (`lm_simhub4.py`, `lm_simhub3.py`, `lm_simhub4t.py`). The update check will ask you to type YES. Re-check your SimHub motion settings with the first-run procedure in the README.

### Control window (Linux, new)
- `gui/sc_control.py`: one window that runs the same start-up as the launcher, step by step: certificates, leftovers from an earlier run, capture server, TLS self-test, bridge, zero-motion self-test (only without SimHub), add-ons, SimHub, then the RSI Launcher.
- Cards for Updates, Motion bridge (live heartbeat, Start / Stop / Restart / Repair), SimHub (detects a second or orphaned SimHub and can end it after a rig checklist), RSI Launcher (opens only when the bridge answers, SimHub runs and the rig checklist is done; closes 30 s after the game exits, cancellable), add-ons and Logs.
- Update check when the window opens; refuses to update while the game runs, and while SimHub runs if the game definition or plugin would change.
- Closing with "clean up when this window closes" on asks once (Clean up and close / Close without clean-up / Cancel). Cancel keeps the window open and stops nothing.
- Needs PySide6. Linux only for now; Windows keeps the terminal launcher. No menu entry yet: start it with `python3 gui/sc_control.py`. Guide: `docs/CONTROL-WINDOW.md`.

### Bridge 4.3.2 (motion path)
- **Ship names into SimHub:** fills SimHub's standard fields Vehicle id / Vehicle name from the seat you enter (e.g. `AEGS_Gladius` / `AEGS Gladius`). Not seated → `SC_OnFoot` / "On Foot"; seated with no name after 1 s → `SC_UnknownVehicle` / "Unknown Vehicle" plus a log line. SimHub's Known Vehicles list fills itself and **motion profiles can switch automatically per ship** (tested on Linux).
  - Only when SimHub's Star Citizen game definition declares those two fields. The shipped game definition is unchanged (24 fields); add **Vehicle id (UTF8)** and **Vehicle name (UTF8)** in SimHub's Sim definition editor to use this.
  - With the 24-field definition, the packets sent to SimHub are byte-identical to bridge 4.2.3.
- Vehicles seen are kept in `~/.config/sc-motion/vehicles_seen.json` with size and kind (ship / gravlev / ground); `python3 ~/lm_simhub4.py vehicles` lists them.
- Standard SimHub fields that a game definition declares but the bridge cannot fill are sent as 0 / empty with a warning instead of stopping the bridge (`LM_STRICT_FIELDS=1` stops instead). A field of an unknown type still stops the bridge.
- **Malformed posts no longer stop the bridge** (4.2.3): bad frames are skipped and counted (`bridge: skipped malformed post #N`). Before, one bad frame could end the receive thread (motion frozen at its last value) or exit the bridge.

### Tee: add-on taps (motion path)
- The tee can send a receive-only copy of every post to up to 8 add-ons, on ports 61704–61708 and 61720–61739, **after** the bridge and plugin copies (those are byte-identical to before). Add-ons register with a small file in `~/.config/sc-motion/taps.d/` (Windows `%APPDATA%\sc-motion\taps.d`). `LM_TEE_TAPS=0` switches taps off.
- The launcher starts and stops each add-on's program with the pipeline; an add-on stopping by itself is reported once and never stops motion. `SC_START_NO_ADDONS=1` starts without add-ons.

### Field database (new)
- `telemetry/sc_fields.json`: every known telemetry event and field with name, unit, type and how sure we are. Add-ons read it at start. `tools/fields_db.py check` validates it.

### Add-on installer (new)
- `sc_motion_install.py addon …` / `sc_install_win.py addon …` (`list | install SOURCE | update | plan | rollback | remove`). Installs from an archive, a folder or a GitHub release; checks every file against the add-on's `release.json`, refuses links, outside paths, oversized packages and port clashes; asks before running the add-on's own installer; keeps the previous version for rollback.

### Updates
- **Rollback:** `python3 ~/sc_start.py rollback` (Windows `sc_start_win.py rollback`) undoes the newest update from its backup, with the motion warning when motion files are involved. Nothing is deleted; files that were new in the update are moved aside. Running it again redoes the update.
- `release.json` now carries a contract number for add-ons (`"contract": 1`).

### Certificates and error logs
- `sc_lmcert.py` 1.3: repair recreates missing certificate copies and keeps a vault of the originals; per-copy result lines; a clear message on permission errors. Installers' apply step uses repair.
- New error log (`scm_errlog.py`): launchers, installers and verifiers record program errors to a log you can attach to an issue. Normal stops (Ctrl-C) are no longer logged as errors.
- Launcher: the SimHub check no longer mistakes an orphaned SimHub (no Wine server) for a running one.

### SC Haptics plugin 0.4.1
- Effects file reader limits nesting depth (64), so a damaged `effects.json` cannot crash SimHub.

### Known limits
- **Windows largely untested** (see the top of this entry).
- Control window: Linux only, no menu entry yet.
- Ship-name fields need the two extra fields in SimHub's game definition (see above).
- SimHub's crash protection covers accelerations and angles, not rotation rates; a ship crash can send very large rotation rates. Keep SimHub limits conservative.
- SimHub's "Touch down" Motion haptic is still not available for Star Citizen; some ship weapons still produce no haptic event.

## 0.1.1-alpha

- README: optional Buy Me a Coffee link (badge and Support section); GitHub Sponsor button via `.github/FUNDING.yml`.
- **SC Haptics plugin 0.4.0:** intensity sliders (master and one per effect) on the plugin's SimHub page; they change only those numbers in your `effects.json` and keep a backup of the first change of each session.
- **New effect `turret_guns`:** shots from a remote turret station (no weapon event, only the shot flag) now reach the shakers while you sit at a turret. Added to `plugin/effects.default.json`; your own `effects.json` is never changed by an update (copy the entry from the default file if you want it).
- **New effect `missile_hit` (testing):** a strong 0.5 s pulse on all shakers when an incoming missile hits you. It is based on one confirmed missile hit so far and is marked for further live testing; other missile impacts may not trigger it. Added to `plugin/effects.default.json` only.
- **Capture server name:** it now identifies itself as `SC-Motion-Haptic-Bridge` to the game's connector (was "D-BOX Motion Engine"). Motion values are unchanged, but the file is part of the motion path, so the update shows the motion warning.

## 0.1.0-alpha

First public alpha.

- **Pipeline:** Star Citizen's motion output (through D-BOX's LiveMotion connector, unchanged) → capture server `SC_Motion_Server.py` → tee `lm_simhub4t.py` → bridge `lm_simhub4.py` (100 Hz, seat gate, ground detection) → SimHub game "Star Citizen" (24 fields: motion axes plus the fields SimHub's Motion haptics use: speed, engine, throttle, ground contact, suspension).
- **SC Haptics plugin 0.3.1** for SimHub: 21 effects from the game's events (guns, boost, atmospheric and hit rumble, landing gear, shocks, shield and hull hits, missiles, countermeasures, heartbeat, seat enter/exit, destroyed) on up to 8 shaker channels through ShakeIt; edit `effects.json`, reloaded within a second.
- **Installers** for Linux (CachyOS / Arch, Wine) and Windows: read-only plan, dependencies first, backups, SimHub game definition installed and registered, plugin built and installed, menu entries / Start menu shortcut. Guided installs for D-BOX HaptiSync Center and SimHub under Wine.
- **Launcher** with certificate check, self-tests, SimHub active-game warning, rig pause, clean-up after the game.
- **Update check** in the launcher: pauses only when a newer release exists; replaces only changed files; never touches your effects, SimHub's settings or your certificates; warns when platform movement values change ([docs/UPDATING.md](docs/UPDATING.md)).
- **Verifiers** (`--live`, `--last-session`) for both systems.

Known limits: Windows untested on real hardware; SimHub's "Touch down" Motion haptic is not available for Star Citizen yet; some ship weapons produce no haptic event.
