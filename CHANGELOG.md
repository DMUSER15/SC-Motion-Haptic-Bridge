# Changelog

## 0.1.0-alpha

First public alpha.

- **Pipeline:** Star Citizen's motion output (through D-BOX's LiveMotion connector, unchanged) → capture server `SC_Motion_Server.py` → tee `lm_simhub4t.py` → bridge `lm_simhub4.py` (100 Hz, seat gate, ground detection) → SimHub game "Star Citizen" (24 fields: motion axes plus the fields SimHub's Motion haptics use: speed, engine, throttle, ground contact, suspension).
- **SC Haptics plugin 0.3.1** for SimHub: 21 effects from the game's events (guns, boost, atmospheric and hit rumble, landing gear, shocks, shield and hull hits, missiles, countermeasures, heartbeat, seat enter/exit, destroyed) on up to 8 shaker channels through ShakeIt; edit `effects.json`, reloaded within a second.
- **Installers** for Linux (CachyOS / Arch, Wine) and Windows: read-only plan, dependencies first, backups, SimHub game definition installed and registered, plugin built and installed, menu entries / Start menu shortcut. Guided installs for D-BOX HaptiSync Center and SimHub under Wine.
- **Launcher** with certificate check, self-tests, SimHub active-game warning, rig pause, clean-up after the game.
- **Update check** in the launcher: pauses only when a newer release exists; replaces only changed files; never touches your effects, SimHub's settings or your certificates; warns when platform movement values change ([docs/UPDATING.md](docs/UPDATING.md)).
- **Verifiers** (`--live`, `--last-session`) for both systems.

Known limits: Windows untested on real hardware; SimHub's "Touch down" Motion haptic is not available for Star Citizen yet; some ship weapons produce no haptic event.
