# scripts/

The runtime scripts. On Linux, the installer links each one into your home folder (`~/<name>.py` → this folder), so updates of this folder take effect at the next start. On Windows, the installer copies them to `%USERPROFILE%\sc-motion`.

| Script | Role |
|---|---|
| `sc_start.py` | Launcher (menu entry "Start StarCitizen - Motion Enabled"); Windows: `setup/windows/sc_start_win.py` |
| `SC_Motion_Server.py` | Capture server on 127.0.0.1:61666 |
| `motion_desc.bin` | Its message schema, generated from `tools/motion_schema.py` |
| `lm_simhub4t.py` + `lm_simhub4.py` | Tee + bridge to SimHub (layout from SimHub's registered game definition) |
| `lm_simhub3.py` | Motion core the bridge imports; `lm_simhub3t.py` is the older 9-field bridge (`SC_BRIDGE_SCRIPT`) |
| `sc_fx.py` | Effects engine reference (the plugin's `Effects.cs` mirrors it); `python3 ~/sc_fx.py check FILE` checks an effects file |
| `sc_lmcert.py` + `sc_certswap.py` | Certificate pair and the copies of `livemotion.crt` |
| `sc_haptics_install.py` | Installs the SC Haptics plugin DLL into SimHub (Linux) |
| `sc_motion_clean.py` | Removes the motion setup again (Linux) |
