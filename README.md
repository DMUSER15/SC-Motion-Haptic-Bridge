# SC-Motion-Haptic-Bridge

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-support-FFDD00?logo=buymeacoffee&logoColor=black)](https://www.buymeacoffee.com/dmuser15)

Star Citizen motion telemetry → SimHub → your motion platform and bass shakers, on **Linux** (CachyOS / Arch, Wine) and **Windows**.

> **Alpha.** Linux is in daily use. Windows has been tested on stand-ins only, not yet on a real Windows PC. Expect rough edges and please report them.

The pipeline ends at SimHub, so **any motion platform SimHub supports works**; you connect and tune it in SimHub as usual. Nothing here depends on one manufacturer.

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
lm_simhub4t.py tee ──► lm_simhub4.py bridge (100 Hz, seat gate) ──► SimHub game "Star Citizen" ──► Motion tab + Motion haptics ──► your platform
                   └─► SimHub plugin "SC Haptics" ──► ShakeIt "SC Low" / "SC High" ──► bass shakers
```

The launcher (**Start StarCitizen - Motion Enabled**) runs everything in order: update check, certificate check, capture server and TLS self-test, tee and bridge with a zero-frame self-test, SimHub (with a warning if its active game is not Star Citizen), a rig pause while you bring the platform up, then the game. It cleans up after the game exits.

## Install

- **Linux (CachyOS / Arch):** [docs/INSTALL-LINUX.md](docs/INSTALL-LINUX.md)
- **Windows:** [docs/INSTALL-WINDOWS.md](docs/INSTALL-WINDOWS.md)

Both installers start with a read-only plan, change nothing until every dependency is met, keep backups of everything they replace, and never overwrite your certificate pair or your effects.

**You need:** Star Citizen (RSI Launcher; on Linux via LUG-Helper), D-BOX HaptiSync Center (free; no D-BOX hardware needed), SimHub, Python 3 with `grpcio` and `protobuf`, OpenSSL, and optionally the .NET SDK to build the SC Haptics plugin.

## Updates

The launcher checks GitHub for a newer release each time it starts. When you are up to date it prints one line and carries on; it pauses only when an update exists, shows exactly which files would change, and replaces only those. It never touches your effects, SimHub's settings and profiles, or your certificates. If an update changes anything that shapes platform movement, it says so clearly and asks you to re-check your SimHub motion settings. Details: [docs/UPDATING.md](docs/UPDATING.md).

## Tuning is yours

The bridge sends the game's values **unshaped**. Every value that reaches your platform (axes, intensity, smoothing, limits, motion scaling, crash protection, Motion haptics gains) is set by you in SimHub, for your platform. Start low.

## Safety

This drives a powered motion platform, which can injure.

- Keep the E-stop within reach.
- First run, and after any update that warns that platform movement values have changed:
  1. Nobody in the rig, motion power OFF, SimHub motion OFF; check the numbers in SimHub.
  2. Motion power ON, still nobody in the rig.
  3. Only then a person.
- SimHub's crash protection is an acceleration threshold; it may not cover every channel. Set limits conservatively.

You use this software at your own risk (see the licence: no warranty).

## Disclaimer

- **Not affiliated** with, endorsed by or supported by Cloud Imperium Games / Roberts Space Industries (Star Citizen), D-BOX Technologies, or SimHub. All trademarks belong to their owners.
- **It changes a D-BOX data file.** Each copy of D-BOX's `livemotion.crt` in the game prefix (Linux) or in `C:\ProgramData\D-BOX` (Windows) is replaced by your own certificate, with backups. Nothing else of D-BOX's or the game's is modified, patched or injected into, and neither D-BOX's private keys nor the game's credentials are used.
- **Terms of service and anti-cheat: unknown.** Whether replacing that certificate file is acceptable under RSI's or D-BOX's terms, or to Easy Anti-Cheat, is **unknown**. The project uses only the game's own motion output and touches no game file or memory, but no one can promise how a third party will judge it. Decide for yourself.
- The capture server implements the message schema it needs to talk to the connector (written by hand in `tools/motion_schema.py`). Whether publishing this description is acceptable to D-BOX or RSI is also unknown.

## Repository layout

| Folder | Contents |
|---|---|
| `scripts/` | Runtime scripts: launcher, capture server and its schema, tee and bridge, effects engine, certificate tools, plugin installer, clean-up. On Linux they are linked into your home folder; Windows copies them to `%USERPROFILE%\sc-motion`. |
| `plugin/` | SC Haptics SimHub plugin source and `effects.default.json`. |
| `config/` | `effects.json` (your effects; never overwritten by updates) and `simhub/Star Citizen.simdef` (SimHub's game definition, installed by the installers). |
| `setup/` | Installers and verifiers for Linux and Windows, shared helpers, the update check, and the D-BOX installer workarounds for Wine. |
| `tools/` | The link map and helpers used by the installers, and the schema generator. |
| `docs/` | Install guides, updates, verification, architecture. |
| `release.json` | This release's version and the sha256 of every file (used by the update check). |

## Support

This is a free, unofficial hobby project. If it is useful to you and you want to say thanks, you can [buy me a coffee](https://www.buymeacoffee.com/dmuser15). Entirely optional: everything here stays free and GPL-3.0, and support buys no warranty or priority.

## Licence

GPL-3.0 ([LICENSE](LICENSE)).
