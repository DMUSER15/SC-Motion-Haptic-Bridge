# Changelog

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
