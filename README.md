# SC-Motion-Haptic-Bridge

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-support-FFDD00?logo=buymeacoffee&logoColor=black)](https://www.buymeacoffee.com/dmuser15)

Star Citizen motion telemetry → SimHub → your motion platform and bass shakers, on **Linux** (CachyOS / Arch, Wine) and **Windows**.

> **Alpha.** Linux is in daily use and is the version this release was tested on.
>
> **Windows: largely untested — use at your own risk.** The Windows installer, launcher and verifier are included and share most of their code with Linux, but this release has not been run on a real Windows PC with a motion platform. **The motion path (capture server, tee and bridge) has changed since 0.1.1-alpha, and how platform movement behaves on Windows MAY have changed.** If you try it on Windows, follow the first-run safety procedure below, start with very low SimHub motion settings, and please log issues.

The pipeline ends at SimHub, so **any motion platform SimHub supports works**; you connect and tune it in SimHub as usual. Nothing here depends on one manufacturer.

## What's new in 0.2.0-alpha

- **Control window (Linux):** one window replaces the terminal launcher — starts and watches the motion bridge, SimHub and the RSI Launcher in order, with status, repair and clean-up buttons. See [docs/CONTROL-WINDOW.md](docs/CONTROL-WINDOW.md).
- **Ship names in SimHub and automatic profile switching:** the bridge sends the vehicle you are seated in (for example `AEGS_Gladius`), `SC_OnFoot` when you are not seated, and `SC_UnknownVehicle` when the game gives no name. SimHub can switch motion profiles per ship.
- **Add-on taps and an add-on installer**, a telemetry **field database**, **rollback** of an update, and a much sturdier bridge.

Full list: [CHANGELOG.md](CHANGELOG.md).

## How it works

Star Citizen sends motion telemetry through D-BOX's LiveMotion connector, a DLL the game loads from D-BOX's free HaptiSync Center software. The connector speaks gRPC over TLS to `127.0.0.1:61666`.

This project puts a small receiver on that port and forwards the data to SimHub. The game and D-BOX's software are not modified. **The only file changed is D-BOX's trust certificate data file** (`livemotion.crt`, in D-BOX's folders), so that the connector accepts the local receiver. The menu entry **Star Citizen - Remove Motion Setup** (Linux) puts D-BOX's originals back.

```
Star Citizen ── D-BOX connector (unchanged)
      │  gRPC + TLS, 127.0.0.1:61666
      ▼
SC_Motion_Server.py  capture server: decodes events, sends JSON per event
      │  UDP 61700
      ▼
lm_simhub4t.py tee ──► lm_simhub4.py bridge (100 Hz, seat gate, vehicle name) ──► SimHub game "Star Citizen" ──► Motion tab + Motion haptics ──► your platform
                   ├─► SimHub plugin "SC Haptics" ──► ShakeIt "SC Low" / "SC High" ──► bass shakers
                   └─► optional add-on taps (UDP 61704–61708, 61720–61739), receive-only copies
```

Two ways to start it, both running the same steps in the same order (update check, certificate check, capture server and TLS self-test, tee and bridge with a zero-frame self-test, add-ons, SimHub with an active-game warning, a rig pause while you bring the platform up, then the game; clean-up after the game exits):

- **Control window** (Linux, new): `python3 gui/sc_control.py` from your copy of this repository. It has no menu entry of its own yet.
- **Start StarCitizen - Motion Enabled** (Linux menu) / the Start menu shortcut (Windows): the terminal launcher, as before.

## Install

- **Linux (CachyOS / Arch):** [docs/INSTALL-LINUX.md](docs/INSTALL-LINUX.md)
- **Windows (largely untested, see above):** [docs/INSTALL-WINDOWS.md](docs/INSTALL-WINDOWS.md)

Both installers start with a read-only plan, change nothing until every dependency is met, keep backups of everything they replace, and never overwrite your certificate pair or your effects.

**You need:** Star Citizen (RSI Launcher; on Linux via LUG-Helper), D-BOX HaptiSync Center (free; no D-BOX hardware needed), SimHub, Python 3 with `grpcio` and `protobuf`, OpenSSL, and optionally the .NET SDK to build the SC Haptics plugin. The control window also needs PySide6 (`sudo pacman -S --needed pyside6`).

## Ship names and per-ship profiles

The bridge fills SimHub's **Vehicle id** and **Vehicle name** fields from the seat you enter, so SimHub's *Known Vehicles* list fills itself as you fly and motion profiles can be tied to ships:

| You are… | Vehicle id sent | Vehicle name sent |
|---|---|---|
| in a pilot or crew seat | the game's ship id, e.g. `AEGS_Gladius` | `AEGS Gladius` |
| not seated (on foot, EVA) | `SC_OnFoot` | `On Foot` |
| seated, but the game gave no name within 1 s | `SC_UnknownVehicle` | `Unknown Vehicle` |

The bridge sends these only when SimHub's game definition for Star Citizen declares the two fields. **The game definition shipped in this release is unchanged (24 fields)**, so nothing changes until you add them: SimHub → Star Citizen → *Sim definition editor* → *Telemetry definition* → add the standard fields **Vehicle id (UTF8)** and **Vehicle name (UTF8)**, save. The bridge keeps a list of vehicles it has seen (with size and kind) in `~/.config/sc-motion/vehicles_seen.json`; `python3 ~/lm_simhub4.py vehicles` prints it.

A seat with no profile of its own uses your default Star Citizen profile.

## Updates and rollback

The launcher and the control window check GitHub for a newer release each time they start. When you are up to date they print one line and carry on; they pause only when an update exists, show exactly which files would change, and replace only those. They never touch your effects, SimHub's settings and profiles, or your certificates. If an update changes anything that shapes platform movement, they say so clearly and ask you to type YES and re-check your SimHub motion settings.

**This release changes the motion path, so you will see that warning.**

New: `python3 ~/sc_start.py rollback` (Windows: `sc_start_win.py rollback`) undoes the newest update from its backup. Details: [docs/UPDATING.md](docs/UPDATING.md).

## Add-ons

Optional add-ons (for example a cockpit display) receive a read-only copy of the telemetry on their own UDP port ("taps") and read the field database in `telemetry/sc_fields.json` to know what each value means. Install, update, roll back or remove them with `python3 setup/linux/sc_motion_install.py addon …` (Windows: `setup\windows\sc_install_win.py addon …`); every file is checked against the add-on's `release.json` first. An add-on never changes what the bridge sends to SimHub. Set `SC_START_NO_ADDONS=1` to start without them, or `LM_TEE_TAPS=0` to switch taps off in the tee.

## Tuning is yours

The bridge sends the game's values **unshaped**. Every value that reaches your platform (axes, intensity, smoothing, limits, motion scaling, crash protection, Motion haptics gains) is set by you in SimHub, for your platform. Start low.

Vibration effects have per-effect sliders as well as an overall Master slider for gain on all effects.

## Safety

This drives a powered motion platform, which can injure.

- Keep the E-stop within reach.
- First run, after this update, and after any update that warns that platform movement values have changed:
  1. Nobody in the rig, motion power OFF, SimHub motion OFF; check the numbers in SimHub.
  2. Motion power ON, still nobody in the rig.
  3. Only then a person.
- SimHub's crash protection is an acceleration threshold; it may not cover every channel (rotation rates in particular). Set limits conservatively.
- **Windows users:** this release is largely untested on Windows and motion behaviour MAY differ from 0.1.1-alpha. Treat every Windows start as a first run.

You use this software at your own risk (see the licence: no warranty).

## Disclaimer

- **Not affiliated** with, endorsed by or supported by Cloud Imperium Games / Roberts Space Industries (Star Citizen), D-BOX Technologies, or SimHub. All trademarks belong to their owners.
- **It changes a D-BOX data file.** Each copy of D-BOX's `livemotion.crt` in the game prefix (Linux) or in `C:\ProgramData\D-BOX` (Windows) is replaced by your own certificate, with backups. Nothing else of D-BOX's or the game's is modified, patched or injected into, and neither D-BOX's private keys nor the game's credentials are used.
- **Terms of service and anti-cheat: unknown.** Whether replacing that certificate file is acceptable under RSI's or D-BOX's terms, or to Easy Anti-Cheat, is **unknown**. The project uses only the game's own motion output and touches no game file or memory, but no one can promise how a third party will judge it. Decide for yourself.
- The capture server implements the message schema it needs to talk to the connector (written by hand in `tools/motion_schema.py`). Whether publishing this description is acceptable to D-BOX or RSI is also unknown.

## Repository layout

| Folder | Contents |
|---|---|
| `scripts/` | Runtime scripts: launcher, capture server and its schema, tee and bridge, effects engine, certificate tools, plugin installer, clean-up, add-on taps. On Linux they are linked into your home folder; Windows copies them to `%USERPROFILE%\sc-motion`. |
| `gui/` | The control window (Linux; PySide6). |
| `plugin/` | SC Haptics SimHub plugin source and `effects.default.json`. |
| `config/` | `effects.json` (your effects; never overwritten by updates) and `simhub/Star Citizen.simdef` (SimHub's game definition, installed by the installers). |
| `telemetry/` | `sc_fields.json`, the field database (what each telemetry value is, its unit and how sure we are). |
| `setup/` | Installers and verifiers for Linux and Windows, shared helpers, the update check and rollback, the add-on installer, and the D-BOX installer workarounds for Wine. |
| `tools/` | The link map and helpers used by the installers, the schema generator and the field-database checker. |
| `docs/` | Install guides, control window, updates, verification, architecture. |
| `release.json` | This release's version and the sha256 of every file (used by the update check). |

## Support

This is a free, unofficial hobby project. If it is useful to you and you want to say thanks, you can [buy me a coffee](https://www.buymeacoffee.com/dmuser15). Entirely optional: everything here stays free and GPL-3.0, and support buys no warranty or priority.

## Licence

GPL-3.0 ([LICENSE](LICENSE)).
