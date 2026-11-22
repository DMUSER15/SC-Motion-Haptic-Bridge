# Updates

The launcher checks for a newer release every time you start it ("Start StarCitizen - Motion Enabled" on Linux and Windows). It looks at the newest release on GitHub, pre-releases included, before anything else starts.

| Situation | What you see |
|---|---|
| You have the newest release | one line: `update check: up to date (0.1.0-alpha)`; the launcher carries on |
| No network, or GitHub does not answer within 5 s | one line; the launcher carries on with what you have |
| A newer release exists | **the launcher pauses**: version, release notes, and the exact list of files that would change; then `[u]pdate now, [s]kip this time, or [q]uit` (Enter = skip) |
| The update changes anything that shapes platform movement | a warning that **platform movement values HAVE changed**; you must type `YES` before it updates; the warning is shown again at the rig pause |
| Star Citizen is already running (the launcher was started again mid-game) | one line; update after the game |

Skipping is always safe: the launcher starts the version you have, and asks again next time. To turn the check off: set the environment variable `SC_UPDATE_CHECK=0`.

## What an update changes, and what it never touches

An update replaces **only the files whose content differs** from the release (compared by sha256), and adds files that are new in it. It never deletes a file. Every file it downloads must match the sha256 in the release's `release.json` before anything is replaced; if one does not match, nothing is changed.

Before a file is replaced, the old copy goes to `~/sc_motion_update_backup/<old>_to_<new>_<time>/` (Windows: `%USERPROFILE%\sc_motion_update_backup\…`), with a `manifest.json` listing what changed.

**Never touched by an update:**

- your SC Haptics effects (`effects.json`): `config/effects.json` in the release folder, `~/sc_haptics/effects.json`, `%USERPROFILE%\sc_haptics\effects.json`;
- SimHub's own settings and profiles: the Motion tab (axes, intensity, smoothing, limits, crash protection), Motion haptics gains, ShakeIt effects, controllers and COM ports;
- your certificate pair (`lm.crt`, `lm.key`) and D-BOX's files in the game prefix;
- the locations the installer saved (`config.json`).

**Changed only when the release changed them, and with the launcher's warning:**

| Release changes … | The update also … |
|---|---|
| the bridge, the capture server, its schema or the effects engine (`scripts/lm_simhub*.py`, `SC_Motion_Server.py`, `motion_desc.bin`, `sc_fx.py`) | nothing else; these are used at the next start |
| SimHub's game definition (`config/simhub/Star Citizen.simdef`) | installs it into SimHub and registers it; your previous one is renamed to `Star Citizen.simdef.bak-<time>` (SimHub must be closed) |
| the SC Haptics plugin source (`plugin/src/`) | rebuilds the plugin with `dotnet` and installs it; the previous DLL goes to `~/sc_haptics/backup` (SimHub must be closed) |
| the default effects (`plugin/effects.default.json`) | nothing; your `effects.json` stays as it is. Compare the two files if you want the new defaults |

When the update would replace the game definition or the plugin while SimHub is running, the launcher asks you to close SimHub first.

## After an update that changed platform movement values

The warning means a file that shapes what reaches the motion platform has changed, so the platform can move differently with the same SimHub settings. Before anyone rides:

1. Check your SimHub Motion settings for Star Citizen: the Motion tab (axes, intensity, smoothing, limits) and Motion haptics (gain, smoothing). Adjust them for your platform.
2. First run with the E-stop within reach:
   1. Nobody in the rig, motion power OFF, SimHub motion OFF; check the numbers in SimHub.
   2. Motion power ON, still nobody in the rig.
   3. Only then a person.

The bridge sends the game's values unshaped; all shaping and limits are yours, in SimHub.

## Kinds of install

| You installed by … | The launcher's update … |
|---|---|
| downloading and unpacking a release | replaces the changed files in that folder |
| `git clone` of this repository | runs `git fetch` and fast-forwards to the release tag. If you changed tracked files other than `config/effects.json`, git refuses and nothing changes; update by hand with git |
| Windows | updates the repository folder the installer saved, then copies the runtime files into `%USERPROFILE%\sc-motion` again (the same files as installer step 6). The control window runs from that repository folder, so it is current after the update; the terminal launcher adds its Start menu entry the first time it finds `gui\` there |

Keep the folder you installed from: it is the one that gets updated. If you moved or deleted it on Windows, the launcher can only tell you about the new release; download it and run `Install-SCMotion.ps1 -Apply` from the new folder (your settings are kept).

After an update that touched setup files, you can run the verifier as usual (`sc_motion_verify.py` / `sc_verify_win.py`).

## Undo an update

```
python3 ~/sc_start.py rollback
```

Windows: `py -3 $env:USERPROFILE\sc-motion\sc_start_win.py rollback`.

It undoes the newest update. It first lists what it will do and asks; nothing changes until you answer `r` (and type `YES` when files that shape platform movement are involved).

- Every file the update replaced comes back from its backup folder.
- Files that were new in the update are moved out. Nothing is deleted: the newer versions go into a new folder `~/sc_motion_update_backup/<new>_back_to_<old>_<time>/`.
- The same follow-up steps run as after an update: links, SimHub's game definition (the registered one is renamed to `.bak-<time>` first), the plugin rebuild. SimHub must be closed when the plugin or the game definition is involved; the game must not be running.
- A git clone of the public repository is moved back to the older release tag (`git reset --keep`, which refuses to lose a local change).
- Never touched: your `effects.json`, SimHub's settings and profiles, your certificates, your saved locations.

Run it again to undo the rollback. Several updates are undone one after the other, newest first. The next start offers the update again; answer `s` to stay on the older version.

After a rollback that changed platform movement values, treat the first ride like the first ride after an update (next section applies the same way).

A developer checkout (your own git repository without `release.json`) is not handled by the launcher: use git there. Tag each state you may want to return to (`git tag -a NAME -m "..."`), and go back with `git checkout NAME`.

## Optional add-ons

An add-on is a separate program in its own repository that uses the bridge's telemetry. The bridge never needs one.

```
python3 setup/linux/sc_motion_install.py addon list
python3 setup/linux/sc_motion_install.py addon install PATH-TO-ARCHIVE-OR-FOLDER
python3 setup/linux/sc_motion_install.py addon update NAME PATH-TO-NEWER-ARCHIVE
python3 setup/linux/sc_motion_install.py addon rollback NAME
python3 setup/linux/sc_motion_install.py addon remove NAME
```

Windows: the same words after `py -3 setup\windows\sc_install_win.py addon`.

Before anything is installed the package is checked: every file must match the sha256 in the package's `release.json`, nothing unlisted may be in it, archives may not contain links or paths outside their folder, the add-on must support this bridge's contract number, and its telemetry port must be in the add-on ranges. Then it asks. An add-on's own installer is a program from that package and runs as you: install only add-ons you trust.

Add-ons live in `~/.local/share/sc-motion/addons/<name>/<version>/` (Windows `%LOCALAPPDATA%\sc-motion\addons`). The version before is kept, so `addon rollback NAME` goes back to it. Each add-on has its own version and is updated and rolled back separately from the bridge.

An add-on that asks for telemetry writes a tap file (`~/.config/sc-motion/taps.d/<name>.json`) when it is installed and removes it when it is removed. From then on the normal start ("Start StarCitizen - Motion Enabled") sends it its copy and starts and stops it with the pipeline: no second menu entry is needed. `python3 ~/scm_taps.py` shows what the next start will do.
